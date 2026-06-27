import unittest
import sys
import types
from contextlib import nullcontext
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

    def test_faq_generation_receives_structured_ai_overview_sections(self):
        settings = {
            **_settings(),
            "gen_faqs": True,
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
            return_value=[{"question": "What matters when choosing a dosing system?", "answer": "Accuracy matters.", "source": "ai_overview"}],
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
        self.assertEqual(generate.call_args.kwargs["ai_overview_sections"], ai_overview_sections)
        self.assertEqual(generate.call_args.kwargs["ai_overview_raw"], "Structured AIO raw text.")

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

    def test_meta_generation_receives_scraped_page_context(self):
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
        self.assertIn("SCRAPED PAGE CONTENT:\nScraped page facts.", context)
        self.assertIn("CLIENT BRIEF:\nClient brief note.", context)

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
             patch.object(all_in_one, "generate_page", return_value={"hero": "Hero copy", "benefits": "Benefit copy", "_word_count": 4}):
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


if __name__ == "__main__":
    unittest.main()
