import unittest
import sys
import types
from unittest.mock import patch

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
    def _process(self, row, settings=None, gsc_client=None, ranked=None, brand_profile=None):
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

    def test_meta_generation_receives_structured_brand_context(self):
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
                "title": "Industrial Dosing Systems",
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

        self.assertEqual(result["status"], "ok")
        brand_context = generate.call_args.kwargs["brand_context"]
        self.assertIn("BRAND CONTEXT:", brand_context)
        self.assertIn("- Voice: Plainspoken expert", brand_context)
        self.assertIn("- Tone: Confident", brand_context)
        self.assertIn("- Target audience: Facilities managers", brand_context)
        self.assertIn("- Unique selling points: Same-day support", brand_context)
        self.assertIn("- Key messages to reinforce: Reduce downtime", brand_context)
        self.assertIn("- Competitors to differentiate from: Acme Rival", brand_context)
        self.assertIn("- Products/services: Industrial dosing systems", brand_context)
        self.assertIn("- Words to avoid: cheap", brand_context)
        self.assertIn("- Example copy to emulate in style, not content:\nExisting brand sample.", brand_context)
        self.assertIn("- Additional brand guidelines:\nAlways mention compliance.", brand_context)
        self.assertNotIn("tone_of_voice", brand_context)


if __name__ == "__main__":
    unittest.main()
