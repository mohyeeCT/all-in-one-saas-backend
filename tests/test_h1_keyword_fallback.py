import unittest
import json
import sys
import types
from contextlib import nullcontext
from unittest.mock import Mock, patch

supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *args, **kwargs: None
supabase_stub.Client = object
sys.modules.setdefault("supabase", supabase_stub)

google_stub = types.ModuleType("google")
google_auth_stub = types.ModuleType("google.auth")
google_auth_exceptions_stub = types.ModuleType("google.auth.exceptions")
google_auth_exceptions_stub.RefreshError = RuntimeError
google_stub.auth = google_auth_stub
google_auth_stub.exceptions = google_auth_exceptions_stub
sys.modules.setdefault("google", google_stub)
sys.modules.setdefault("google.auth", google_auth_stub)
sys.modules.setdefault("google.auth.exceptions", google_auth_exceptions_stub)

gsc_stub = types.ModuleType("utils.gsc")
gsc_stub.GscOAuthConfigError = RuntimeError
gsc_stub.get_gsc_client = lambda *args, **kwargs: None
gsc_stub.get_top_queries_for_url = lambda *args, **kwargs: []
sys.modules.setdefault("utils.gsc", gsc_stub)

docx_export_stub = types.ModuleType("utils.docx_export")
docx_export_stub.build_docx = lambda *args, **kwargs: b"docx"
sys.modules.setdefault("utils.docx_export", docx_export_stub)

from routers import all_in_one
from utils.templates import TEMPLATES


class _FakeQuery:
    def select(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Resp", (), {"data": []})()


class _FakeSupabase:
    def table(self, *_args, **_kwargs):
        return _FakeQuery()


class _CapturingQuery:
    def __init__(self, payloads):
        self.payloads = payloads

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.payloads.append(payload)
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Resp", (), {"data": []})()


class _CapturingSupabase:
    def __init__(self):
        self.payloads = []

    def table(self, *_args, **_kwargs):
        return _CapturingQuery(self.payloads)


class _MissingColumnError(Exception):
    code = "42703"


class _MissingInternalLinksQuery:
    def __init__(self, payloads):
        self.payloads = payloads
        self._pending_payload = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self._pending_payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self._pending_payload is None:
            return type("Resp", (), {"data": [{"logs": []}]})()
        self.payloads.append(self._pending_payload)
        if "internal_link_suggestions" in self._pending_payload:
            raise _MissingColumnError("column jobs.internal_link_suggestions does not exist")
        return type("Resp", (), {"data": []})()


class _MissingInternalLinksSupabase:
    def __init__(self):
        self.payloads = []

    def table(self, *_args, **_kwargs):
        return _MissingInternalLinksQuery(self.payloads)


def _settings():
    return {
        "provider": "Claude",
        "api_key": "key",
        "dfs_login": "dfs@example.com",
        "dfs_password": "secret",
        "use_gsc": False,
        "site_url": "",
        "min_volume": 10,
        "gen_meta": False,
        "gen_faqs": False,
        "gen_page_copy": False,
    }


class AllInOneH1KeywordFallbackTests(unittest.TestCase):
    def _process(
        self,
        row,
        settings=None,
        gsc_client=None,
        ranked=None,
        brand_profile=None,
        serp_data=None,
        patch_combined_docx=True,
    ):
        docx_patch = (
            patch.object(all_in_one, "_build_combined_docx", return_value=b"docx")
            if patch_combined_docx
            else nullcontext()
        )
        page_context_patch = (
            nullcontext()
            if isinstance(all_in_one.scrape_page_context, Mock)
            else patch.object(
                all_in_one,
                "scrape_page_context",
                return_value={"success": False, "content": "", "error": "unavailable"},
            )
        )
        scrape_url_patch = (
            nullcontext()
            if isinstance(all_in_one.scrape_url, Mock)
            else patch.object(all_in_one, "scrape_url", return_value={"success": False, "body_text": ""})
        )
        strategy_patch = (
            nullcontext()
            if isinstance(all_in_one.generate_strategy_brief, Mock)
            else patch.object(
                all_in_one,
                "generate_strategy_brief",
                return_value={
                    "search_intent": "Commercial",
                    "page_goal": "Help the reader evaluate the page topic.",
                    "primary_positioning": "Lead with the page's core value.",
                    "headline_direction": "Use a clear, natural headline.",
                    "meta_direction": "Summarise the page accurately.",
                    "faq_direction": "Answer relevant decision questions.",
                    "verified_facts": [{
                        "id": "F1",
                        "fact": "The page documents its core service.",
                        "source": "current_page",
                    }],
                },
            )
        )
        strategy_issues_patch = (
            nullcontext()
            if isinstance(all_in_one.strategy_brief_issues, Mock)
            else patch.object(all_in_one, "strategy_brief_issues", return_value=[])
        )
        with patch.object(all_in_one, "get_niche_context", return_value=""), \
             patch.object(all_in_one, "get_ranked_keywords_for_url", return_value=[]), \
             patch.object(all_in_one, "get_search_volume", return_value={}), \
             patch.object(all_in_one, "get_keyword_difficulty", return_value={}), \
             patch.object(all_in_one, "rank_keywords", return_value=ranked or []), \
             patch.object(
                 all_in_one,
                 "get_serp_data",
                 return_value=serp_data or {"organic": [], "paa_items": [], "ai_overview": ""},
             ), \
             page_context_patch, \
             scrape_url_patch, \
             strategy_patch, \
             strategy_issues_patch, \
             docx_patch:
            return all_in_one._process_single_row(
                row=row,
                settings=settings or _settings(),
                gsc_client=gsc_client,
                branded_terms=[],
                used_keywords=set(),
                sb=_FakeSupabase(),
                job_id="job-1",
                row_num=1,
                total_rows=1,
                brand_profile=brand_profile,
            )

    def test_uses_h1_when_ranked_pool_empty_and_gsc_disabled(self):
        result = self._process({
            "url": "https://example.com/emergency-plumbing",
            "keyword": "",
            "page_type": "service",
            "h1": "Emergency Plumbing Services",
        })

        self.assertEqual(result["primary_keyword"], "Emergency Plumbing Services")
        self.assertEqual(result["keyword_source"], "h1 fallback")

    def test_existing_ranked_keyword_still_wins_over_h1(self):
        ranked = [{
            "keyword": "ranked plumbing keyword",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        result = self._process(
            {
                "url": "https://example.com/emergency-plumbing",
                "keyword": "",
                "page_type": "service",
                "h1": "Emergency Plumbing Services",
            },
            ranked=ranked,
        )

        self.assertEqual(result["primary_keyword"], "ranked plumbing keyword")
        self.assertEqual(result["keyword_source"], "dfs")

    def test_manual_keyword_overrides_ranked_pool(self):
        ranked = [{
            "keyword": "dfs winner keyword",
            "volume": 500,
            "difficulty": 15,
            "score": 50.0,
            "branded": False,
        }]

        result = self._process(
            {
                "url": "https://example.com/category",
                "keyword": "manual party appetizers",
                "page_type": "collection",
                "h1": "Party Appetizers",
                "template_key": "collection_page",
            },
            ranked=ranked,
        )

        self.assertEqual(result["primary_keyword"], "manual party appetizers")
        self.assertEqual(result["keyword_source"], "manual")

    def test_selected_model_reaches_all_generation_calls(self):
        settings = {
            **_settings(),
            "model": "claude-haiku-4-5-20251001",
            "gen_meta": True,
            "gen_faqs": True,
            "gen_page_copy": True,
            "brand_name": "Example",
            "business_type": "service",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "generate_copy", return_value={
            "title": "Industrial Dosing Systems",
            "description": "Industrial dosing systems for reliable service teams.",
            "h1_optimised": "Industrial Dosing Systems",
        }) as generate_copy, \
             patch.object(all_in_one, "generate_faq", return_value=[
                 {
                     "question": "What are industrial dosing systems?",
                     "answer": "Industrial dosing systems help teams dose materials consistently.",
                     "source": "generated",
                 }
             ]) as generate_faq, \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_full_page": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_word_count": 121,
             }) as generate_page:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(generate_copy.call_args.kwargs["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(generate_faq.call_args.kwargs["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(generate_page.call_args.kwargs["model"], "claude-haiku-4-5-20251001")

    def test_new_versioned_page_copy_threads_correction_to_strategy(self):
        with patch.dict(
            "os.environ",
            {"AIO_PAGE_COPY_QUALITY_V1_MODE": "on"},
        ):
            settings, _ = all_in_one._new_job_page_quality_settings(
                {
                    **_settings(),
                    "gen_page_copy": True,
                    "brand_name": "Example",
                    "business_type": "service",
                },
                "user-1",
                page_copy_requested=True,
            )
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        strategy_brief = {
            "search_intent": "Commercial",
            "page_goal": "Help readers evaluate the service.",
            "primary_positioning": "Lead with supported dosing services.",
            "headline_direction": "Use a direct service headline.",
            "verified_facts": [],
        }

        with patch.object(
            all_in_one,
            "generate_strategy_brief",
            return_value=strategy_brief,
        ) as generate_strategy, patch.object(
            all_in_one,
            "generate_page",
            return_value={
                "hero": "Industrial dosing systems "
                + " ".join(["copy"] * 120),
                "_full_page": "Industrial dosing systems "
                + " ".join(["copy"] * 120),
                "_word_count": 121,
            },
        ):
            self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "industrial dosing systems",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertIs(
            generate_strategy.call_args.kwargs[
                "page_copy_correction_enabled"
            ],
            True,
        )

    def test_page_copy_removes_template_faq_when_separate_faq_output_is_enabled(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "generate_faq", return_value=[
            {
                "question": "What are industrial dosing systems?",
                "answer": "Industrial dosing systems help teams dose materials consistently.",
                "source": "generated",
            }
        ]), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_full_page": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_word_count": 121,
             }) as generate_page:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        section_names = [section["name"] for section in generate_page.call_args.kwargs["template"]["sections"]]
        section_labels = [section["label"] for section in generate_page.call_args.kwargs["template"]["sections"]]
        self.assertNotIn("faq", section_names)
        self.assertNotIn("support_notes", section_names)
        self.assertNotIn("Final Decision Notes", section_labels)
        self.assertNotIn("support_notes", result["keyword_assignment"])
        self.assertNotIn("faq", result["keyword_assignment"])

    def test_page_copy_keeps_template_faq_when_separate_faq_output_is_disabled(self):
        settings = {
            **_settings(),
            "gen_faqs": False,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "generate_page", return_value={
            "hero": "Industrial dosing systems " + " ".join(["copy"] * 120),
            "_full_page": "Industrial dosing systems " + " ".join(["copy"] * 120),
            "_word_count": 121,
        }) as generate_page:
            self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        section_names = [section["name"] for section in generate_page.call_args.kwargs["template"]["sections"]]
        self.assertIn("faq", section_names)
        self.assertNotIn("support_notes", section_names)

    def test_separate_faq_output_removes_embedded_faq_from_every_template(self):
        templates_with_faq = 0
        for template_key, template in TEMPLATES.items():
            faq_sections = [
                section
                for section in template.get("sections") or []
                if "faq" in str(section.get("name") or "").lower()
                or str(section.get("label") or "").lower() == "frequently asked questions"
            ]
            if not faq_sections:
                continue
            templates_with_faq += 1
            with self.subTest(template=template_key):
                adjusted = all_in_one._template_for_page_copy(template, True)
                names = [str(section.get("name") or "").lower() for section in adjusted["sections"]]
                labels = [str(section.get("label") or "") for section in adjusted["sections"]]
                self.assertFalse(any("faq" in name for name in names))
                self.assertNotIn("Final Decision Notes", labels)
                self.assertEqual(
                    len(adjusted["sections"]),
                    len(template["sections"]) - len(faq_sections),
                )

        self.assertGreater(templates_with_faq, 0)

    def test_page_copy_adapts_sections_after_keyword_assignment_without_reassigning_keywords(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        keyword_assignment = {
            "hero": {"primary": "industrial dosing systems", "supporting": ""},
            "benefits": {"primary": "", "supporting": "dosing system benefits"},
            "process": {"primary": "", "supporting": ""},
            "social_proof": {"primary": "", "supporting": ""},
        }
        strategy_brief = {
            "search_intent": "Commercial",
            "page_goal": "Help readers evaluate the service.",
            "primary_positioning": "Lead with accurate dosing control.",
            "headline_direction": "Use a direct service headline.",
            "verified_facts": [{"id": "F1", "fact": "The service includes dosing support."}],
            "section_guidance": [
                {
                    "section": "benefits",
                    "responsibility": "Explain supported benefits.",
                    "proof_points": ["Proof one", "Proof two", "Proof three"],
                },
                {
                    "section": "process",
                    "responsibility": "Explain the verified process.",
                    "proof_points": ["One process fact"],
                },
                {
                    "section": "social_proof",
                    "responsibility": "Use confirmed social proof only.",
                    "proof_points": [],
                },
            ],
        }

        with patch.object(all_in_one, "assign_keywords_to_sections", return_value=keyword_assignment), \
             patch.object(all_in_one, "generate_strategy_brief", return_value=strategy_brief), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_full_page": "Industrial dosing systems " + " ".join(["copy"] * 120),
                 "_word_count": 121,
             }) as generate_page:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "industrial dosing systems",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        generated_template = generate_page.call_args.kwargs["template"]
        generated_sections = {section["name"]: section for section in generated_template["sections"]}
        plan = {item["section"]: item for item in result["adaptive_section_plan"]}

        self.assertEqual(generate_page.call_args.kwargs["keyword_assignment"], keyword_assignment)
        self.assertEqual(result["keyword_assignment"], keyword_assignment)
        self.assertEqual(generated_sections["benefits"]["word_count"], [250, 430])
        self.assertEqual(generated_sections["process"]["word_count"], [300, 500])
        self.assertNotIn("social_proof", generated_sections)
        self.assertEqual(plan["process"]["mode"], "full")
        self.assertEqual(plan["social_proof"]["mode"], "omit")
        self.assertEqual(result["adaptive_template_family"], "lead_generation")

    def test_skips_when_h1_unavailable_and_gsc_disabled(self):
        for h1 in ("", "none", "NoNe"):
            with self.subTest(h1=h1):
                result = self._process({
                    "url": "https://example.com/no-keyword",
                    "keyword": "",
                    "page_type": "service",
                    "h1": h1,
                })

                self.assertIsNone(result["primary_keyword"])
                self.assertEqual(result["status"], "skipped: no keywords found")

    def test_gsc_enabled_without_keyword_does_not_fall_back_to_h1(self):
        settings = _settings()
        settings["use_gsc"] = True
        settings["site_url"] = "sc-domain:example.com"

        result = self._process(
            {
                "url": "https://example.com/no-gsc-keyword",
                "keyword": "",
                "page_type": "service",
                "h1": "Emergency Plumbing Services",
            },
            settings=settings,
            gsc_client=None,
        )

        self.assertIsNone(result["primary_keyword"])
        self.assertEqual(result["status"], "skipped: no keywords found")

    def test_meta_generation_receives_style_only_brand_context_when_evidence_is_ready(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        brand_profile = {
            "brand_voice": "Plainspoken expert",
            "tone": "Confident",
            "target_audience": "Facilities managers",
            "usps": "Same-day support",
            "key_messages": "Reduce downtime",
            "competitors": "Acme Rival",
            "products_services": "Industrial dosing systems",
            "words_to_avoid": "cheap",
            "example_copy": "Existing brand sample.",
            "guidelines": "Always mention compliance.",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Reliable Industrial Dosing Systems",
                "description": "Learn about industrial dosing systems.",
                "h1_optimised": "Industrial Dosing Systems",
            },
        ) as generate:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                },
                settings=settings,
                ranked=ranked,
                brand_profile=brand_profile,
            )

        self.assertEqual(result["status"], "warning")
        brand_context = generate.call_args.kwargs["brand_context"]
        self.assertIn("BRAND STYLE:", brand_context)
        self.assertIn("- Voice: Plainspoken expert", brand_context)
        self.assertIn("- Tone: Confident", brand_context)
        self.assertNotIn("Facilities managers", brand_context)
        self.assertNotIn("Same-day support", brand_context)
        self.assertNotIn("Reduce downtime", brand_context)
        self.assertNotIn("Acme Rival", brand_context)
        self.assertNotIn("Industrial dosing systems", brand_context)
        self.assertNotIn("Existing brand sample", brand_context)
        self.assertNotIn("Always mention compliance", brand_context)
        self.assertNotIn("tone_of_voice", brand_context)

    def test_brand_profile_name_falls_back_when_manual_brand_name_is_empty(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "business_type": "service",
            "brand_name": "",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Reliable Industrial Dosing Systems",
                "description": "Learn about industrial dosing systems.",
                "h1_optimised": "Industrial Dosing Systems",
            },
        ) as generate:
            self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                },
                settings=settings,
                ranked=ranked,
                brand_profile={"brand_name": "Profile Brand"},
            )

        self.assertEqual(generate.call_args.kwargs["brand_name"], "Profile Brand")

    def test_faq_generation_receives_structured_ai_overview_sections(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "num_faqs": 1,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        ai_overview_sections = [
            {"title": "Selection factors", "content": "Compare dosing systems by accuracy and maintenance needs."}
        ]
        serp_data = {
            "organic": [],
            "paa_items": [{"question": "How do dosing systems work?", "answer": "They control flow."}],
            "ai_overview_sections": ai_overview_sections,
            "ai_overview_raw": "Structured AIO raw text.",
        }
        with patch.object(
            all_in_one,
            "generate_faq",
            return_value=[{
                "question": "What matters when choosing a dosing system?",
                "answer": "Accuracy, maintenance needs, operating conditions, and process requirements all matter when comparing industrial dosing systems.",
                "source": "generated",
            }],
        ) as generate:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                },
                settings=settings,
                ranked=ranked,
                serp_data=serp_data,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(generate.call_args.kwargs["paa_items"], serp_data["paa_items"])
        self.assertEqual(generate.call_args.kwargs["ai_overview_sections"], ai_overview_sections)
        self.assertEqual(generate.call_args.kwargs["ai_overview_raw"], "Structured AIO raw text.")

    def test_faq_generation_is_not_blocked_by_an_incomplete_strategy_brief(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "num_faqs": 1,
            "business_type": "service",
        }
        faq_result = [{
            "question": "How does this service support implementation?",
            "answer": "The service helps teams understand implementation steps, responsibilities, and practical considerations before they begin the work.",
            "source": "generated",
        }]

        with patch.object(all_in_one, "generate_strategy_brief", return_value={"search_intent": "Commercial"}), \
             patch.object(all_in_one, "strategy_brief_issues", return_value=["Page goal is missing."]), \
             patch.object(all_in_one, "generate_faq", return_value=faq_result) as generate:
            result = self._process(
                {
                    "url": "https://example.com/service",
                    "keyword": "implementation service",
                    "page_type": "service",
                    "h1": "Implementation Service",
                },
                settings=settings,
            )

        self.assertEqual(result["faq_items"], faq_result)
        self.assertTrue(generate.called)
        self.assertNotIn("strategy_brief", generate.call_args.kwargs)
        self.assertNotIn("faq_plan", generate.call_args.kwargs)

    def test_page_copy_reuses_faq_page_scrape_for_existing_content(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "gen_page_copy": True,
            "jina_api_key": "jina-key",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        scraped_content = "Client page content from the FAQ scrape."

        with patch.object(all_in_one, "scrape_page_context", return_value={"success": True, "content": scraped_content}) as faq_scrape, \
             patch.object(all_in_one, "scrape_url", return_value={"success": True, "body_text": "Second scrape content"}) as scrape_url, \
             patch.object(all_in_one, "generate_faq", return_value=[]), \
             patch.object(all_in_one, "generate_page", return_value={"hero": "Hero copy", "_word_count": 2}) as generate_page:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        faq_scrape.assert_called_once()
        scrape_url.assert_not_called()
        self.assertEqual(generate_page.call_args.kwargs["client_existing_content"], scraped_content)

    def test_page_copy_only_reads_owned_page_before_strategy_generation(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "jina_api_key": "jina-key",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={"success": True, "content": "Owned page evidence."},
        ) as owned_scrape, patch.object(
            all_in_one,
            "generate_strategy_brief",
            return_value={},
        ) as generate_strategy, patch.object(
            all_in_one,
            "generate_page",
            return_value={"hero": "# Industrial Dosing Systems\nUseful page copy.", "_word_count": 6},
        ):
            self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        owned_scrape.assert_called_once_with(
            "jina-key",
            "https://example.com/industrial-dosing",
            mode="default",
        )
        self.assertEqual(generate_strategy.call_args.kwargs["page_context"], "Owned page evidence.")


    def test_meta_generation_excludes_raw_context_when_evidence_contract_is_ready(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_faqs": True,
            "jina_api_key": "jina-key",
            "client_brief": "Client brief note.",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "scrape_page_context", return_value={"success": True, "content": "Scraped page facts."}), \
             patch.object(all_in_one, "generate_copy", return_value={
                 "title": "Industrial Dosing Systems",
                 "description": "Learn about industrial dosing systems.",
                 "h1_optimised": "Industrial Dosing Systems",
             }) as generate_copy, \
             patch.object(all_in_one, "generate_faq", return_value=[]):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        context = generate_copy.call_args.kwargs["context"]
        self.assertEqual(context, "")
        strategy_brief = generate_copy.call_args.kwargs["strategy_brief"]
        self.assertEqual(strategy_brief["verified_facts"][0]["id"], "F1")


    def test_faq_output_is_trimmed_to_requested_count(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "num_faqs": 3,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        generated_faqs = [
            {"question": f"Question {i}?", "answer": f"Answer {i}."}
            for i in range(1, 6)
        ]

        with patch.object(all_in_one, "generate_strategy_brief", return_value={}), \
             patch.object(all_in_one, "generate_faq", return_value=generated_faqs):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "num_faqs": 3,
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["faq_count"], 3)
        self.assertEqual([item["question"] for item in result["faq_items"]], [
            "Question 1?",
            "Question 2?",
            "Question 3?",
        ])
        schema = json.loads(result["faq_schema"])
        self.assertEqual(len(schema["mainEntity"]), 3)

    def test_job_faq_count_applies_when_row_has_no_override(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
            "num_faqs": 7,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        generated_faqs = [
            {"question": f"Question {i}?", "answer": f"Answer {i}."}
            for i in range(1, 8)
        ]
        row = all_in_one.AIORow(
            url="https://example.com/industrial-dosing",
            page_type="service",
            h1="Industrial Dosing Systems",
        ).model_dump()

        with patch.object(all_in_one, "generate_strategy_brief", return_value={}), \
             patch.object(all_in_one, "generate_faq", return_value=generated_faqs) as generate_faq:
            result = self._process(row, settings=settings, ranked=ranked)

        self.assertIsNone(row["num_faqs"])
        self.assertEqual(generate_faq.call_args.kwargs["num_faqs"], 7)
        self.assertEqual(result["faq_count"], 7)


    def test_row_gets_review_flags_for_forbidden_phrase_and_missing_requested_output(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_faqs": True,
            "gen_page_copy": True,
            "forbidden_phrases": "cheap",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Cheap Industrial Dosing Systems",
                "description": "",
                "h1_optimised": "Industrial Dosing Systems",
            },
        ), \
             patch.object(all_in_one, "generate_faq", return_value=[]), \
             patch.object(all_in_one, "generate_page", return_value={"_full_page": "", "_word_count": 0}), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        codes = [flag["code"] for flag in result["qa_flags"]]
        self.assertIn("forbidden_phrase", codes)
        self.assertIn("meta_missing_description", codes)
        self.assertIn("faq_missing", codes)
        self.assertIn("page_copy_missing", codes)

    def test_meta_title_matching_input_h1_is_flagged_for_review(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Industrial Dosing Systems",
                "description": "Learn about industrial dosing systems.",
                "h1_optimised": "Better Industrial Dosing Systems",
            },
        ):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        self.assertIn("meta_title_matches_h1", [flag["code"] for flag in result["qa_flags"]])

    def test_page_copy_sections_outside_word_count_target_are_flagged(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Too short.",
                 "benefits": " ".join(["benefit"] * 530),
                 "_word_count": 532,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        flags = result["qa_flags"]
        by_section = {flag.get("section"): flag for flag in flags}
        self.assertEqual(by_section["hero"]["code"], "section_word_count_below_target")
        self.assertEqual(by_section["hero"]["actual_words"], 2)
        self.assertEqual(by_section["hero"]["target_min"], 120)
        self.assertEqual(by_section["hero"]["target_max"], 220)
        self.assertEqual(by_section["hero"]["severity"], "review")
        self.assertEqual(by_section["benefits"]["code"], "section_word_count_above_target")
        self.assertEqual(by_section["benefits"]["actual_words"], 530)
        self.assertEqual(by_section["benefits"]["target_min"], 250)
        self.assertEqual(by_section["benefits"]["target_max"], 430)
        self.assertEqual(by_section["benefits"]["severity"], "review")

    def test_moderately_short_page_section_is_warning_only(self):
        flags = []
        all_in_one._add_section_word_count_flags(
            flags,
            {"intro": " ".join(["concise"] * 45)},
            {
                "sections": [{
                    "name": "intro",
                    "label": "Introduction",
                    "word_count": [60, 100],
                }],
            },
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["code"], "section_word_count_below_target")
        self.assertEqual(flags[0]["severity"], "warning")
        self.assertEqual(all_in_one._qa_status(flags), "warning")

    def test_generic_openers_in_meta_and_page_copy_are_flagged(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Reliable Industrial Dosing Systems",
                "description": "Looking for industrial dosing systems that simplify daily operations and reduce maintenance delays.",
                "h1_optimised": "Industrial Dosing Systems",
            },
        ), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Looking for " + " ".join(["service"] * 90),
                 "_word_count": 92,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        generic_flags = [flag for flag in result["qa_flags"] if flag["code"] == "generic_opener"]
        self.assertEqual(len(generic_flags), 2)
        self.assertIn("meta_description", {flag["output"] for flag in generic_flags})
        self.assertIn("hero", {flag.get("section") for flag in generic_flags})
        self.assertTrue(all(flag["phrase"] == "Looking for" for flag in generic_flags))

    def test_repeated_page_copy_phrase_is_flagged_for_review(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        page_copy = (
            "# Industrial Dosing Systems\n"
            "Industrial dosing systems support maintenance teams with cleaner planning. "
            "Gas station locations need reliable workflows for daily service requests. "
            "The same process helps gas station locations coordinate inspections and repairs. "
            "Clear documentation gives gas station locations a simple way to brief technicians, "
            "track urgent issues, compare parts, confirm responsibilities, and keep routine "
            "service moving without vague handoffs or repeated calls between teams."
        )

        with patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": page_copy,
                 "_word_count": len(page_copy.split()),
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        repeated_flag = next(flag for flag in result["qa_flags"] if flag["code"] == "repeated_phrase")
        self.assertEqual(repeated_flag["phrase"], "gas station locations")
        self.assertEqual(repeated_flag["count"], 3)






    def test_page_copy_h1_is_replaced_with_meta_h1_before_qa(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Reliable Industrial Dosing Systems",
                "description": "Improve industrial dosing systems with practical support for maintenance teams.",
                "h1_optimised": "Reliable Industrial Dosing Systems",
            },
        ), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "# Industrial Dosing System Services\n" + " ".join(["service"] * 90),
                 "_word_count": 94,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["section_results"]["hero"].startswith("# Reliable Industrial Dosing Systems"))
        self.assertNotIn("page_h1_differs_from_meta_h1", [flag["code"] for flag in result["qa_flags"]])

    def test_missing_target_keyword_in_meta_and_page_copy_is_flagged(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Reliable Equipment Support",
                "description": "Improve daily operations with practical support for maintenance teams.",
                "h1_optimised": "Reliable Equipment Support",
            },
        ), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": " ".join(["service"] * 90),
                 "_word_count": 90,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "review")
        codes = [flag["code"] for flag in result["qa_flags"]]
        self.assertIn("target_keyword_missing_from_meta", codes)
        self.assertIn("target_keyword_missing_from_page_copy", codes)

    def test_reordered_target_keyword_tokens_count_as_present(self):
        settings = {
            **_settings(),
            "gen_meta": True,
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        page_copy = "Industrial teams improve dosing accuracy with modular systems. " + " ".join(["service"] * 82)

        with patch.object(
            all_in_one,
            "generate_copy",
            return_value={
                "title": "Systems for Industrial Dosing",
                "description": "Improve dosing accuracy with systems built for industrial maintenance teams.",
                "h1_optimised": "Industrial Dosing Systems",
            },
        ), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": page_copy,
                 "_word_count": len(page_copy.split()),
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
            )

        self.assertEqual(result["status"], "warning")
        self.assertNotIn("target_keyword_missing_from_meta", [flag["code"] for flag in result["qa_flags"]])
        self.assertNotIn("target_keyword_missing_from_page_copy", [flag["code"] for flag in result["qa_flags"]])

    def test_new_quality_checks_report_hard_rules_and_advisory_seo_gaps(self):
        flags = all_in_one._collect_qa_flags(
            gen_meta=True,
            gen_faqs=True,
            gen_page_copy=True,
            generated_title="Short!",
            generated_description="Too short.",
            optimised_h1="Example Services",
            input_h1="Current Services",
            primary_keyword="industrial dosing systems",
            faq_items=[
                {"question": "Do you offer shipping", "answer": "Add to cart today!"},
                {"question": "Do you offer shipping", "answer": "Shipping is available."},
            ],
            section_results={
                "hero": "# Example Services\nGeneral operational support for facilities.",
                "details": "## Capabilities\nThis page gives teams practical guidance for daily work.",
            },
            forbidden_phrases=[],
            brand_name="Example",
            business_type="b2b",
            page_type="service",
        )

        by_code = {flag["code"]: flag for flag in flags}
        expected = {
            "exclamation_mark_present",
            "b2b_consumer_cta",
            "brand_name_in_h1",
            "meta_title_outside_preferred_range",
            "meta_description_outside_preferred_range",
            "target_keyword_missing_from_meta_title",
            "target_keyword_missing_from_meta_description",
            "meta_description_missing_action",
            "target_keyword_missing_from_h1",
            "target_keyword_missing_from_first_100_words",
            "target_keyword_missing_from_h2",
            "duplicate_faq_question",
            "faq_answer_very_short",
            "faq_question_missing_question_mark",
            "faq_risky_mutable_topic",
            "generic_page_reference",
        }
        self.assertTrue(expected.issubset(by_code))
        self.assertEqual(by_code["b2b_consumer_cta"]["severity"], "review")
        self.assertEqual(by_code["target_keyword_missing_from_h1"]["severity"], "warning")
        self.assertEqual(by_code["meta_title_outside_preferred_range"]["severity"], "warning")
        self.assertEqual(by_code["meta_description_missing_action"]["severity"], "warning")

    def test_new_quality_checks_do_not_flag_compliant_natural_variants(self):
        title = "Systems for Industrial Dosing in Reliable Facility Operations"
        description = (
            "Explore industrial dosing systems that support accurate dosing, practical maintenance planning, "
            "clearer process control, and dependable daily operations."
        )
        self.assertTrue(50 <= len(title) <= 80)
        self.assertTrue(140 <= len(description) <= 180)

        flags = all_in_one._collect_qa_flags(
            gen_meta=True,
            gen_faqs=True,
            gen_page_copy=True,
            generated_title=title,
            generated_description=description,
            optimised_h1="Industrial Dosing Systems for Process Control",
            input_h1="Current Process Control Services",
            primary_keyword="industrial dosing systems",
            faq_items=[
                {
                    "question": "How does the service support process control?",
                    "answer": "It helps operations teams plan maintenance, document routine work, and maintain clearer process responsibilities across facilities.",
                },
            ],
            section_results={
                "hero": (
                    "# Industrial Dosing Systems for Process Control\n"
                    "Industrial facilities rely on systems that improve dosing accuracy and maintenance planning."
                ),
                "details": "## Systems for Industrial Dosing\nClear responsibilities support consistent daily work.",
            },
            forbidden_phrases=[],
            brand_name="Example",
            business_type="b2b",
            page_type="service",
        )

        new_codes = {
            "exclamation_mark_present",
            "b2b_consumer_cta",
            "brand_name_in_h1",
            "meta_title_outside_preferred_range",
            "meta_description_outside_preferred_range",
            "target_keyword_missing_from_meta_title",
            "target_keyword_missing_from_meta_description",
            "meta_description_missing_action",
            "target_keyword_missing_from_h1",
            "target_keyword_missing_from_first_100_words",
            "target_keyword_missing_from_h2",
            "duplicate_faq_question",
            "faq_answer_very_short",
            "faq_question_missing_question_mark",
            "faq_risky_mutable_topic",
            "generic_page_reference",
        }
        self.assertFalse(new_codes.intersection(flag["code"] for flag in flags))

    def test_page_copy_result_stores_context_for_section_reruns(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [
            {"keyword": "industrial dosing systems", "volume": 100, "difficulty": 20, "score": 5.0, "branded": False},
            {"keyword": "dosing system service", "volume": 90, "difficulty": 20, "score": 4.0, "branded": False},
            {"keyword": "chemical dosing maintenance", "volume": 80, "difficulty": 20, "score": 3.0, "branded": False},
        ]
        serp_data = {
            "organic": [{"url": "https://competitor.example/service"}],
            "paa_items": [],
            "ai_overview_raw": "",
        }

        with patch.object(all_in_one, "scrape_url", return_value={"success": True, "body_text": "Competitor or client content"}), \
             patch.object(all_in_one, "is_editorial_competitor", return_value=True), \
             patch.object(all_in_one, "classify_competitor_relevance", return_value=1), \
             patch.object(all_in_one, "map_competitor_sections", return_value={"benefits": ["Competitor says onboarding is fast."]}), \
             patch.object(all_in_one, "get_keyword_ideas", return_value=[{"keyword": "metering pump maintenance"}]), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Industrial dosing systems " + " ".join(["hero"] * 127),
                 "benefits": " ".join(["benefit"] * 260),
                 "_word_count": 390,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
                serp_data=serp_data,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["keyword_assignment"]["benefits"]["supporting"], "dosing system service")
        self.assertEqual(result["lsi_keywords"]["dosing system service"], ["metering pump maintenance"])
        self.assertEqual(result["competitor_section_map"]["benefits"], ["Competitor says onboarding is fast."])

    def test_page_copy_result_includes_content_gap_diagnostics(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [
            {"keyword": "industrial dosing systems", "volume": 100, "difficulty": 20, "score": 5.0, "branded": False},
            {"keyword": "dosing system service", "volume": 90, "difficulty": 20, "score": 4.0, "branded": False},
        ]
        serp_data = {
            "organic": [{"url": "https://competitor.example/service"}],
            "paa_items": [],
            "ai_overview_raw": "",
        }

        with patch.object(all_in_one, "scrape_url", return_value={"success": True, "body_text": "Competitor content"}), \
             patch.object(all_in_one, "is_editorial_competitor", return_value=True), \
             patch.object(all_in_one, "classify_competitor_relevance", return_value=1), \
             patch.object(all_in_one, "map_competitor_sections", return_value={
                 "benefits": ["Competitors explain warranty coverage and setup timeline for buyers."]
             }), \
             patch.object(all_in_one, "get_keyword_ideas", return_value=[]), \
             patch.object(all_in_one, "generate_page", return_value={
                 "hero": "Industrial dosing systems help teams improve accuracy.",
                 "benefits": "This section covers reliability and maintenance planning for daily operations.",
                 "_word_count": 15,
             }):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
                serp_data=serp_data,
            )

        self.assertEqual(result["content_gap_summary"][0]["section"], "benefits")
        self.assertIn("warranty coverage", result["content_gap_summary"][0]["missing_topics"])



    def test_page_copy_scrapes_use_saved_jina_key(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "jina_api_key": "jina-key",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        serp_data = {
            "organic": [{"url": "https://competitor.example/service"}],
            "paa_items": [],
            "ai_overview_raw": "",
        }

        with patch.object(all_in_one, "scrape_url", return_value={"success": False}) as scrape_url, \
             patch.object(all_in_one, "generate_page", return_value={"hero": "Hero copy", "_word_count": 2}):
            self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
                serp_data=serp_data,
            )

        self.assertGreaterEqual(scrape_url.call_count, 2)
        for call in scrape_url.call_args_list:
            self.assertEqual(call.kwargs["api_key"], "jina-key")

    def test_row_result_includes_safe_run_diagnostics(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "gen_meta": True,
            "gen_faqs": True,
            "jina_api_key": "jina-secret",
            "api_key": "provider-secret",
            "dfs_password": "dfs-secret",
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]
        serp_data = {
            "organic": [{"url": "https://competitor.example/service"}],
            "paa_items": [{"question": "What is dosing?", "answer": "A process."}],
            "ai_overview_sections": ["Accuracy matters."],
            "ai_overview_raw": "Accuracy matters.",
        }

        with patch.object(all_in_one, "scrape_page_context", return_value={
                 "success": True,
                 "content": "Scraped page context.",
                 "source": "live",
                 "raw_chars": 100,
                 "cleaned_chars": 21,
             }), \
             patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_copy", return_value={
                 "title": "Industrial Dosing Systems",
                 "description": "Industrial dosing systems for facilities.",
                 "h1_optimised": "Industrial Dosing Systems",
             }), \
             patch.object(all_in_one, "generate_faq", return_value=[
                 {"question": "What is dosing?", "answer": "It controls chemicals."}
             ]), \
             patch.object(all_in_one, "generate_page", return_value={"hero": "Hero copy", "_word_count": 2}):
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
                serp_data=serp_data,
            )

        diagnostics = result["run_diagnostics"]
        self.assertEqual(diagnostics["provider"], "Claude")
        self.assertEqual(diagnostics["model"], "")
        self.assertEqual(diagnostics["gsc_auth_method"], "disabled")
        self.assertGreaterEqual(diagnostics["duration_ms"], 0)
        self.assertEqual(diagnostics["input_signal_counts"]["paa_questions"], 1)
        self.assertEqual(diagnostics["input_signal_counts"]["ai_overview_sections"], 1)
        self.assertEqual(diagnostics["input_signal_counts"]["serp_organic"], 1)
        self.assertEqual(diagnostics["input_signal_counts"]["competitor_candidates"], 1)
        self.assertEqual(diagnostics["input_signal_counts"]["competitor_scrape_successes"], 0)
        self.assertEqual(diagnostics["output_counts"]["faq_items"], 1)
        self.assertEqual(diagnostics["output_counts"]["sections"], 1)
        self.assertEqual(diagnostics["generation_requested"], {"meta": True, "faqs": True, "page_copy": True})
        self.assertEqual(diagnostics["scrape"]["page_context_source"], "live")
        self.assertEqual(diagnostics["scrape"]["requested_provider"], "jina")
        self.assertEqual(diagnostics["scrape"]["raw_response_chars"], 100)
        self.assertEqual(diagnostics["scrape"]["retained_context_chars"], 21)
        self.assertEqual(result["scrape_status"], "Success: Jina live")
        self.assertEqual(result["page_context_preview"], "Scraped page context.")
        self.assertNotIn("provider-secret", repr(diagnostics))
        self.assertNotIn("dfs-secret", repr(diagnostics))
        self.assertNotIn("jina-secret", repr(diagnostics))
        self.assertNotIn("Scraped page context.", repr(diagnostics))

    def test_page_copy_only_docx_uses_richer_export_builder(self):
        settings = {
            **_settings(),
            "gen_page_copy": True,
            "business_type": "service",
            "brand_name": "Example",
        }
        ranked = [{
            "keyword": "industrial dosing systems",
            "volume": 100,
            "difficulty": 20,
            "score": 5.0,
            "branded": False,
        }]

        with patch.object(all_in_one, "scrape_url", return_value={"success": False}), \
             patch.object(all_in_one, "generate_page", return_value={"hero": "Hero copy", "_word_count": 2}), \
             patch.object(all_in_one, "build_docx", return_value=b"rich-docx") as build_docx:
            result = self._process(
                {
                    "url": "https://example.com/industrial-dosing",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Industrial Dosing Systems",
                    "template_key": "service_page",
                },
                settings=settings,
                ranked=ranked,
                patch_combined_docx=False,
            )

        build_docx.assert_called_once()
        self.assertEqual(result["docx_b64"], "cmljaC1kb2N4")

    def test_process_job_flags_cross_row_duplicate_meta_and_intro(self):
        first_result = {
            "url": "https://example.com/products/blue-widget",
            "status": "ok",
            "qa_flags": [],
            "generated_title": "Blue Widget for Industrial Teams",
            "generated_description": "Shop durable blue widgets for industrial teams that need reliable performance, easy maintenance, and fast setup.",
            "section_results": {
                "product_intro": "Blue widgets give industrial teams reliable performance, easy maintenance, and fast setup for daily operations.",
                "details": "More unique details for the first product.",
            },
        }
        second_result = {
            "url": "https://example.com/products/red-widget",
            "status": "ok",
            "qa_flags": [],
            "generated_title": "Red Widget for Industrial Teams",
            "generated_description": "Shop durable red widgets for industrial teams that need reliable performance, easy maintenance, and fast setup.",
            "section_results": {
                "product_intro": "Red widgets give industrial teams reliable performance, easy maintenance, and fast setup for daily operations.",
                "details": "More unique details for the second product.",
            },
        }
        sb = _CapturingSupabase()

        with patch.object(all_in_one, "get_supabase", return_value=sb), \
             patch.object(all_in_one, "_is_cancelled", return_value=False), \
             patch.object(all_in_one, "_process_single_row", side_effect=[first_result, second_result]):
            all_in_one._process_job(
                "job-1",
                [{"url": first_result["url"]}, {"url": second_result["url"]}],
                _settings(),
                None,
                user_id="user-1",
            )

        final_payload = next(payload for payload in reversed(sb.payloads) if payload.get("status") == "complete")
        results = final_payload["results"]
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[1]["status"], "review")
        self.assertEqual(final_payload["failed_rows"], 0)

        flags = results[1]["qa_flags"]
        codes = [flag["code"] for flag in flags]
        self.assertIn("meta_description_similar_to_row", codes)
        self.assertIn("page_intro_similar_to_row", codes)
        self.assertTrue(all(flag["similar_to_row"] == 1 for flag in flags))

    def test_process_job_does_not_flag_distinct_cross_row_copy(self):
        first_result = {
            "url": "https://example.com/products/blue-widget",
            "status": "ok",
            "qa_flags": [],
            "generated_title": "Blue Widget for Industrial Teams",
            "generated_description": "Shop durable blue widgets for industrial teams that need reliable performance, easy maintenance, and fast setup.",
            "section_results": {
                "product_intro": "Blue widgets give industrial teams reliable performance, easy maintenance, and fast setup for daily operations.",
            },
        }
        second_result = {
            "url": "https://example.com/products/red-widget",
            "status": "ok",
            "qa_flags": [],
            "generated_title": "Red Widget for Field Technicians",
            "generated_description": "Compare compact red widgets built for mobile crews, tight storage spaces, field repairs, and fast replacement work.",
            "section_results": {
                "product_intro": "Red widgets help mobile technicians complete field repairs in tight spaces with compact storage and quick replacement parts.",
            },
        }
        sb = _CapturingSupabase()

        with patch.object(all_in_one, "get_supabase", return_value=sb), \
             patch.object(all_in_one, "_is_cancelled", return_value=False), \
             patch.object(all_in_one, "_process_single_row", side_effect=[first_result, second_result]):
            all_in_one._process_job(
                "job-1",
                [{"url": first_result["url"]}, {"url": second_result["url"]}],
                _settings(),
                None,
                user_id="user-1",
            )

        final_payload = next(payload for payload in reversed(sb.payloads) if payload.get("status") == "complete")
        results = final_payload["results"]
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[1]["status"], "ok")
        self.assertEqual(results[1]["qa_flags"], [])

    def test_process_job_adds_internal_link_suggestions_after_all_rows_complete(self):
        source_result = {
            "url": "https://example.com/blog/dosing-maintenance",
            "status": "ok",
            "qa_flags": [],
            "primary_keyword": "chemical dosing maintenance",
            "h1": "Chemical Dosing Maintenance",
            "section_results": {
                "intro": "Teams comparing industrial dosing systems often need maintenance planning before choosing a service partner.",
                "details": "Maintenance schedules and calibration checks reduce downtime.",
            },
        }
        target_result = {
            "url": "https://example.com/services/industrial-dosing-systems",
            "status": "ok",
            "qa_flags": [],
            "primary_keyword": "industrial dosing systems",
            "h1": "Industrial Dosing Systems",
            "section_results": {
                "hero": "Industrial dosing systems for facilities that need accurate chemical control.",
            },
        }
        sb = _CapturingSupabase()

        with patch.object(all_in_one, "get_supabase", return_value=sb), \
             patch.object(all_in_one, "_is_cancelled", return_value=False), \
             patch.object(all_in_one, "_process_single_row", side_effect=[source_result, target_result]):
            all_in_one._process_job(
                "job-1",
                [{"url": source_result["url"]}, {"url": target_result["url"]}],
                _settings(),
                None,
                user_id="user-1",
            )

        final_payload = next(payload for payload in reversed(sb.payloads) if payload.get("status") == "complete")
        suggestions = final_payload["internal_link_suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["source_url"], source_result["url"])
        self.assertEqual(suggestions[0]["target_url"], target_result["url"])
        self.assertEqual(suggestions[0]["anchor_text"], "industrial dosing systems")
        self.assertGreaterEqual(suggestions[0]["confidence"], 0.75)

    def test_process_job_completes_when_internal_link_suggestions_fail(self):
        result = {
            "url": "https://example.com/services/industrial-dosing-systems",
            "status": "ok",
            "qa_flags": [],
            "primary_keyword": "industrial dosing systems",
            "section_results": {
                "hero": "Industrial dosing systems for facilities that need reliable chemical control.",
            },
        }
        sb = _CapturingSupabase()

        with patch.object(all_in_one, "get_supabase", return_value=sb), \
             patch.object(all_in_one, "_is_cancelled", return_value=False), \
             patch.object(all_in_one, "_process_single_row", return_value=result), \
             patch.object(all_in_one, "_build_internal_link_suggestions", side_effect=RuntimeError("link failure")):
            all_in_one._process_job(
                "job-1",
                [{"url": result["url"]}],
                _settings(),
                None,
                user_id="user-1",
            )

        final_payload = next(payload for payload in reversed(sb.payloads) if payload.get("status") == "complete")
        self.assertEqual(final_payload["current_step"], "Done. Internal link suggestions unavailable.")
        self.assertEqual(final_payload["internal_link_suggestions"], [])

    def test_update_job_retries_without_internal_links_when_column_is_missing(self):
        sb = _MissingInternalLinksSupabase()

        all_in_one._update_job(
            sb,
            "job-1",
            "user-1",
            {
                "status": "complete",
                "current_step": "Done.",
                "internal_link_suggestions": [],
            },
        )

        self.assertEqual(len(sb.payloads), 2)
        self.assertIn("internal_link_suggestions", sb.payloads[0])
        self.assertEqual(sb.payloads[1]["status"], "complete")
        self.assertEqual(sb.payloads[1]["current_step"], "Done.")
        self.assertNotIn("internal_link_suggestions", sb.payloads[1])


if __name__ == "__main__":
    unittest.main()
