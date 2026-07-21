import unittest
from unittest.mock import patch

from routers import all_in_one
from routers.all_in_one import AIOSettings
from utils.faq_scraper import AIO_OWNED_PAGE_CAPTURE_VERSION


class AioScrapingSettingsTests(unittest.TestCase):
    def test_new_aio_jobs_stamp_the_server_owned_capture_version(self):
        settings, profile = all_in_one._new_job_page_quality_settings(
            {"owned_page_capture_version": "client-controlled-value"},
            "legacy-user",
            page_copy_requested=False,
        )

        self.assertIsNone(profile)
        self.assertEqual(
            settings["owned_page_capture_version"],
            AIO_OWNED_PAGE_CAPTURE_VERSION,
        )

    def test_jina_remains_primary_and_firecrawl_fallback_stays_off(self):
        settings = AIOSettings()

        self.assertEqual(settings.scrape_provider, "jina")
        self.assertFalse(settings.firecrawl_fallback)

    def test_versioned_aio_capture_is_forwarded_without_changing_legacy_calls(self):
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={"success": True, "content": "Page context", "source": "live"},
        ) as jina:
            all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "owned_page_capture_version": AIO_OWNED_PAGE_CAPTURE_VERSION,
                },
                "https://example.com",
            )
            all_in_one._scrape_owned_page_for_settings(
                {"scrape_provider": "jina", "jina_api_key": "jina"},
                "https://example.com/legacy",
            )

        self.assertEqual(
            jina.call_args_list[0].kwargs["capture_version"],
            AIO_OWNED_PAGE_CAPTURE_VERSION,
        )
        self.assertNotIn("capture_version", jina.call_args_list[1].kwargs)

    def test_primary_firecrawl_skips_jina(self):
        with patch.object(all_in_one, "scrape_page_context") as jina, patch(
            "utils.faq_scraper.scrape_page_context_firecrawl",
            return_value={"success": True, "source": "firecrawl"},
        ) as firecrawl:
            result = all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "firecrawl",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                },
                "https://example.com",
            )

        self.assertTrue(result["success"])
        jina.assert_not_called()
        firecrawl.assert_called_once_with("firecrawl", "https://example.com", mode="default")
        self.assertEqual(result["requested_provider"], "firecrawl")
        self.assertEqual(result["mode"], "default")

    def test_jina_failure_does_not_use_firecrawl_when_fallback_is_off(self):
        jina_failure = {"success": False, "error": "Jina failed", "source": "live"}
        with patch.object(all_in_one, "scrape_page_context", return_value=jina_failure), patch(
            "utils.faq_scraper.scrape_page_context_firecrawl",
        ) as firecrawl:
            result = all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                    "firecrawl_fallback": False,
                },
                "https://example.com",
            )

        self.assertIs(result, jina_failure)
        firecrawl.assert_not_called()

    def test_jina_failure_uses_firecrawl_when_fallback_is_enabled(self):
        firecrawl_success = {"success": True, "content": "Page context", "source": "firecrawl"}
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={"success": False, "error": "Jina failed"},
        ), patch(
            "utils.faq_scraper.scrape_page_context_firecrawl",
            return_value=firecrawl_success,
        ) as firecrawl:
            result = all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                    "firecrawl_fallback": True,
                },
                "https://example.com",
            )

        self.assertIs(result, firecrawl_success)
        firecrawl.assert_called_once_with("firecrawl", "https://example.com", mode="default")
        self.assertTrue(result["fallback_used"])

    def test_versioned_sparse_jina_success_does_not_switch_to_firecrawl(self):
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={
                "success": True,
                "content": "Sparse but usable page context",
                "source": "live",
                "quality_diagnostics": {"sparse": True},
            },
        ), patch(
            "utils.faq_scraper.scrape_page_context_firecrawl",
        ) as firecrawl:
            result = all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                    "firecrawl_fallback": True,
                    "owned_page_capture_version": AIO_OWNED_PAGE_CAPTURE_VERSION,
                },
                "https://example.com",
            )

        self.assertTrue(result["success"])
        firecrawl.assert_not_called()

    def test_versioned_jina_failure_uses_enabled_firecrawl_with_the_same_capture_contract(self):
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={"success": False, "error": "Jina failed"},
        ), patch(
            "utils.faq_scraper.scrape_page_context_firecrawl",
            return_value={"success": True, "content": "Page context", "source": "firecrawl"},
        ) as firecrawl:
            result = all_in_one._scrape_owned_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                    "firecrawl_fallback": True,
                    "owned_page_capture_version": AIO_OWNED_PAGE_CAPTURE_VERSION,
                },
                "https://example.com",
            )

        self.assertTrue(result["success"])
        firecrawl.assert_called_once_with(
            "firecrawl",
            "https://example.com",
            mode="default",
            capture_version=AIO_OWNED_PAGE_CAPTURE_VERSION,
        )

    def test_jina_selector_recovery_is_reported_as_a_fallback(self):
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={
                "success": True,
                "content": "Recovered page context",
                "source": "live_selector_recovery",
            },
        ):
            result = all_in_one._scrape_owned_page_for_settings(
                {"scrape_provider": "jina", "jina_api_key": "jina"},
                "https://example.com",
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["source"], "live_selector_recovery")

    def test_collection_page_uses_collection_aware_scraping(self):
        with patch.object(
            all_in_one,
            "scrape_page_context",
            return_value={"success": True, "content": "Collection context", "source": "live"},
        ) as jina:
            result = all_in_one._scrape_owned_page_for_settings(
                {"scrape_provider": "jina", "jina_api_key": "jina"},
                "https://example.com/collections/hats",
                business_type="ecommerce",
                page_type="collection",
            )

        jina.assert_called_once_with(
            "jina",
            "https://example.com/collections/hats",
            mode="ecommerce_collection",
        )
        self.assertEqual(result["mode"], "ecommerce_collection")
        self.assertEqual(result["cleaned_chars"], len("Collection context"))


if __name__ == "__main__":
    unittest.main()
