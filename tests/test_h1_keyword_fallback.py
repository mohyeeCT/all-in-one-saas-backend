import unittest
from unittest.mock import patch

from routers import all_in_one


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
    def _process(self, row, settings=None, gsc_client=None, ranked=None):
        with patch.object(all_in_one, "get_niche_context", return_value=""), \
             patch.object(all_in_one, "get_ranked_keywords_for_url", return_value=[]), \
             patch.object(all_in_one, "get_search_volume", return_value={}), \
             patch.object(all_in_one, "get_keyword_difficulty", return_value={}), \
             patch.object(all_in_one, "rank_keywords", return_value=ranked or []), \
             patch.object(
                 all_in_one,
                 "get_serp_data",
                 return_value={"organic": [], "paa_items": [], "ai_overview": ""},
             ), \
             patch.object(all_in_one, "_build_combined_docx", return_value=b"docx"):
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


if __name__ == "__main__":
    unittest.main()
