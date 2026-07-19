import unittest
from unittest.mock import patch

from routers import all_in_one, jobs
from utils import copy_gen


_PRIVATE_WORKER_SENTINEL = "private-worker-detail-73c9e1"
_SAFE_ROW_ERROR = "This row could not be processed. Please try again."


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.operation = "select"
        self.payload = None
        self.filters = []

    def select(self, _columns):
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def execute(self):
        rows = [
            row
            for row in self.sb.tables.get(self.table, [])
            if all(row.get(field) == value for field, value in self.filters)
        ]
        self.sb.executed.append(
            {
                "table": self.table,
                "operation": self.operation,
                "payload": self.payload,
                "filters": list(self.filters),
            }
        )
        if self.operation == "update":
            for row in rows:
                row.update(self.payload)
        return _Response(rows)


class _Supabase:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.executed = []

    def table(self, name):
        return _Query(self, name)


def _runtime_settings():
    return {
        "provider": "Claude",
        "api_key": "runtime-key",
        "dfs_login": "dfs@example.com",
        "dfs_password": "runtime-password",
        "brand_name": "",
        "business_type": "general",
        "location_code": 2840,
        "use_gsc": False,
    }


def _quality_context():
    return {
        "enabled": False,
        "guidance": None,
        "policy": None,
        "page_quality_policy_version": "",
        "adaptive_policy_version": "",
        "owned_page_mapping_version": "",
    }


class WorkerErrorRedactionTests(unittest.TestCase):
    def test_section_provider_exception_never_enters_copy_or_next_prompt(self):
        prompts = []

        def failing_provider(_api_key, prompt, **_kwargs):
            prompts.append(prompt)
            raise RuntimeError(_PRIVATE_WORKER_SENTINEL)

        sections = [
            {
                "name": "intro",
                "label": "Introduction",
                "purpose": "Introduce the page.",
                "keyword_slot": "primary",
                "word_count": [40, 80],
            },
            {
                "name": "benefits",
                "label": "Benefits",
                "purpose": "Explain the benefits.",
                "keyword_slot": "supporting",
                "word_count": [60, 100],
            },
        ]

        with (
            patch.dict(
                copy_gen.PROVIDER_FN,
                {"ErrorTest": failing_provider},
                clear=False,
            ),
            patch.object(copy_gen.time, "sleep"),
            self.assertLogs("utils.copy_gen", level="ERROR") as logs,
        ):
            result = copy_gen.generate_page(
                template={"sections": sections},
                keyword_assignment={},
                lsi_keywords={},
                business_type="service",
                brand_name="",
                h1="Example Service",
                page_type="service",
                paa_questions=[],
                ai_overview="",
                competitor_section_map={},
                client_brief="",
                client_existing_content="",
                provider="ErrorTest",
                api_key="private-api-key",
            )

        placeholder = "[Section generation unavailable. Retry this section.]"
        self.assertEqual(result["intro"], placeholder)
        self.assertEqual(result["benefits"], placeholder)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, repr(result))
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, "\n".join(prompts))
        self.assertIn(placeholder, prompts[1])
        server_logs = "\n".join(logs.output)
        self.assertIn("aio.page_copy.section_failed", server_logs)
        self.assertIn("exception_type=RuntimeError", server_logs)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, server_logs)

    def test_initial_worker_persists_only_fixed_row_error(self):
        updates = []

        with (
            patch.object(all_in_one, "get_supabase", return_value=object()),
            patch.object(
                all_in_one,
                "_update_job",
                side_effect=lambda _sb, _job, _user, data: updates.append(
                    dict(data)
                ),
            ),
            patch.object(all_in_one, "_is_cancelled", return_value=False),
            patch.object(
                all_in_one,
                "_process_single_row",
                side_effect=RuntimeError(_PRIVATE_WORKER_SENTINEL),
            ),
            patch.object(
                all_in_one,
                "_build_internal_link_suggestions",
                return_value=[],
            ),
            self.assertLogs("routers.all_in_one", level="ERROR") as logs,
        ):
            all_in_one._process_job(
                "job-1",
                [{"url": "https://example.com/page"}],
                {"use_gsc": False, "brand_name": ""},
                None,
                user_id="user-1",
            )

        persisted = repr(updates)
        self.assertIn(f"'error': '{_SAFE_ROW_ERROR}'", persisted)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, persisted)
        self.assertNotIn("Traceback", persisted)
        server_logs = "\n".join(logs.output)
        self.assertIn("aio.row.failed", server_logs)
        self.assertIn("exception_type=RuntimeError", server_logs)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, server_logs)

    def test_single_and_bulk_reruns_redact_fatal_row_exceptions(self):
        runtime = _runtime_settings()
        rows = [{"url": "https://example.com/page"}]

        single_sb = _Supabase(
            {
                "jobs": [
                    {
                        "id": "job-1",
                        "user_id": "user-1",
                        "results": [{}],
                    }
                ]
            }
        )
        with (
            patch.object(jobs, "hydrate_job_settings", return_value=runtime),
            patch.object(jobs, "_clear_credentials_runtime_error"),
            patch.object(jobs, "_get_runtime_gsc_client", return_value=None),
            patch.object(
                all_in_one,
                "_process_single_row",
                side_effect=RuntimeError(_PRIVATE_WORKER_SENTINEL),
            ),
            self.assertLogs("routers.jobs", level="ERROR") as single_logs,
        ):
            jobs._rerun_single_row(
                "job-1",
                0,
                rows,
                runtime,
                single_sb,
                user_id="user-1",
            )

        single_job = single_sb.tables["jobs"][0]
        self.assertEqual(
            single_job["current_step"],
            "Row 1 rerun failed. Please try again.",
        )
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, repr(single_job))
        self.assertNotIn("Traceback", repr(single_job))
        single_server_logs = "\n".join(single_logs.output)
        self.assertIn("aio.rerun.row_failed", single_server_logs)
        self.assertIn("exception_type=RuntimeError", single_server_logs)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, single_server_logs)

        bulk_sb = _Supabase(
            {
                "jobs": [
                    {
                        "id": "job-1",
                        "user_id": "user-1",
                        "results": [{}],
                    }
                ]
            }
        )
        with (
            patch.object(jobs, "hydrate_job_settings", return_value=runtime),
            patch.object(jobs, "_clear_credentials_runtime_error"),
            patch.object(jobs, "_get_runtime_gsc_client", return_value=None),
            patch.object(all_in_one, "_update_job"),
            patch.object(
                all_in_one,
                "_process_single_row",
                side_effect=RuntimeError(_PRIVATE_WORKER_SENTINEL),
            ),
            self.assertLogs("routers.jobs", level="ERROR") as bulk_logs,
        ):
            jobs._rerun_multiple_rows(
                "job-1",
                [0],
                rows,
                runtime,
                bulk_sb,
                user_id="user-1",
            )

        bulk_job = bulk_sb.tables["jobs"][0]
        self.assertEqual(bulk_job["results"][0]["error"], _SAFE_ROW_ERROR)
        self.assertEqual(bulk_job["results"][0]["status"], "error")
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, repr(bulk_job))
        bulk_server_logs = "\n".join(bulk_logs.output)
        self.assertIn("aio.rerun.bulk_row_failed", bulk_server_logs)
        self.assertIn("exception_type=RuntimeError", bulk_server_logs)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, bulk_server_logs)
        final_update = [
            operation
            for operation in bulk_sb.executed
            if operation["operation"] == "update"
        ][-1]
        self.assertEqual(
            final_update["filters"],
            [("id", "job-1"), ("user_id", "user-1")],
        )

    def test_section_rerun_redacts_fatal_exception(self):
        runtime = _runtime_settings()
        job = {
            "settings": runtime,
            "rows": [{"url": "https://example.com/page", "page_type": "service"}],
            "results": [
                {
                    "primary_keyword": "example service",
                    "section_results": {},
                }
            ],
        }
        sb = _Supabase(
            {
                "jobs": [
                    {
                        "id": "job-1",
                        "user_id": "user-1",
                        "results": job["results"],
                    }
                ]
            }
        )

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=runtime),
            patch.object(jobs, "_clear_credentials_runtime_error"),
            patch.object(
                all_in_one,
                "_stored_page_quality_context",
                return_value=_quality_context(),
            ),
            patch(
                "utils.templates.get_template",
                side_effect=RuntimeError(_PRIVATE_WORKER_SENTINEL),
            ),
            self.assertLogs("routers.jobs", level="ERROR") as logs,
        ):
            jobs._rerun_single_section(
                "job-1",
                0,
                "hero",
                job,
                "user-1",
                sb,
            )

        stored = sb.tables["jobs"][0]
        self.assertEqual(
            stored["current_step"],
            "Section rerun failed for row 1. Please try again.",
        )
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, repr(stored))
        self.assertNotIn("Traceback", repr(stored))
        server_logs = "\n".join(logs.output)
        self.assertIn("aio.rerun.section_failed", server_logs)
        self.assertIn("exception_type=RuntimeError", server_logs)
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, server_logs)

    def test_owned_page_and_strategy_failures_are_redacted_everywhere(self):
        safe_scrape = {
            "success": True,
            "content": "Verified owned-page context.",
            "source": "live",
            "requested_provider": "jina",
            "mode": "default",
            "raw_chars": 28,
            "cleaned_chars": 28,
        }
        cases = (
            (
                "owned scrape exception",
                RuntimeError(_PRIVATE_WORKER_SENTINEL),
                None,
            ),
            (
                "owned scrape returned error",
                {
                    "success": False,
                    "content": "",
                    "error": _PRIVATE_WORKER_SENTINEL,
                    "source": "",
                    "requested_provider": "jina",
                    "mode": "default",
                },
                None,
            ),
            (
                "strategy exception",
                safe_scrape,
                RuntimeError(_PRIVATE_WORKER_SENTINEL),
            ),
        )

        for label, scrape_outcome, strategy_error in cases:
            with self.subTest(label=label):
                result, stored, server_logs = self._run_minimal_row(
                    scrape_outcome,
                    strategy_error,
                )
                public_state = repr((result, stored))
                self.assertNotIn(_PRIVATE_WORKER_SENTINEL, public_state)
                self.assertNotIn(_PRIVATE_WORKER_SENTINEL, server_logs)

                if label.startswith("owned scrape"):
                    self.assertIn(
                        "aio.scrape.owned_page_failed",
                        server_logs,
                    )
                    self.assertEqual(
                        result["run_diagnostics"]["scrape"][
                            "page_context_error"
                        ],
                        "Owned-page context was unavailable.",
                    )
                    self.assertEqual(
                        result["scrape_status"],
                        "Failed: Owned-page context was unavailable.",
                    )
                else:
                    self.assertIn("aio.strategy.failed", server_logs)
                    self.assertIn(
                        "exception_type=RuntimeError",
                        server_logs,
                    )
                    self.assertEqual(result["strategy_status"], "unavailable")
                    self.assertEqual(
                        result["strategy_issues"],
                        ["Strategy brief generation was unavailable."],
                    )

    def test_returned_serp_error_is_not_persisted(self):
        result, stored, server_logs = self._run_minimal_row(
            {
                "success": True,
                "content": "Verified owned-page context.",
                "source": "live",
                "requested_provider": "jina",
                "mode": "default",
            },
            None,
            serp_outcome={
                "error": _PRIVATE_WORKER_SENTINEL,
                "organic": [],
                "paa_items": [],
                "ai_overview": "",
            },
        )

        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, repr((result, stored)))
        self.assertNotIn(_PRIVATE_WORKER_SENTINEL, server_logs)
        self.assertIn("aio.serp.failed", server_logs)
        self.assertIn(
            "Search-result data was unavailable; continuing.",
            repr(stored),
        )

    def _run_minimal_row(
        self,
        scrape_outcome,
        strategy_error,
        *,
        serp_outcome=None,
    ):
        settings = {
            **_runtime_settings(),
            "min_volume": 0,
            "gen_meta": False,
            "gen_faqs": True,
            "gen_page_copy": False,
            "scrape_pages": True,
        }
        job_row = {
            "id": "job-1",
            "user_id": "user-1",
            "logs": [],
        }
        sb = _Supabase({"jobs": [job_row]})
        keyword = "example service"

        if isinstance(scrape_outcome, Exception):
            scrape_patch = patch.object(
                all_in_one,
                "_scrape_owned_page_for_settings",
                side_effect=scrape_outcome,
            )
        else:
            scrape_patch = patch.object(
                all_in_one,
                "_scrape_owned_page_for_settings",
                return_value=dict(scrape_outcome),
            )

        strategy_kwargs = (
            {"side_effect": strategy_error}
            if strategy_error is not None
            else {
                "return_value": {
                    "search_intent": "Commercial",
                    "page_goal": "Explain the service.",
                    "primary_positioning": "Lead with verified value.",
                }
            }
        )

        with (
            patch.object(all_in_one, "get_niche_context", return_value=""),
            patch.object(
                all_in_one,
                "get_ranked_keywords_for_url",
                return_value=[],
            ),
            patch.object(
                all_in_one,
                "get_search_volume",
                return_value={keyword: 10},
            ),
            patch.object(
                all_in_one,
                "get_keyword_difficulty",
                return_value={keyword: 20},
            ),
            patch.object(all_in_one, "rank_keywords", return_value=[]),
            patch.object(
                all_in_one,
                "get_serp_data",
                return_value=serp_outcome
                or {
                    "organic": [],
                    "paa_items": [],
                    "ai_overview": "",
                },
            ),
            scrape_patch,
            patch.object(
                all_in_one,
                "generate_strategy_brief",
                **strategy_kwargs,
            ),
            patch.object(
                all_in_one,
                "strategy_brief_issues",
                return_value=[],
            ),
            patch.object(all_in_one, "generate_faq", return_value=[]),
            patch.object(
                all_in_one,
                "_build_combined_docx",
                return_value=b"docx",
            ),
            self.assertLogs("routers.all_in_one", level="WARNING") as logs,
        ):
            result = all_in_one._process_single_row(
                row={
                    "url": "https://example.com/page",
                    "keyword": keyword,
                    "page_type": "service",
                },
                settings=settings,
                gsc_client=None,
                branded_terms=[],
                used_keywords=set(),
                sb=sb,
                job_id="job-1",
                row_num=1,
                total_rows=1,
                user_id="user-1",
            )

        return result, job_row, "\n".join(logs.output)


if __name__ == "__main__":
    unittest.main()
