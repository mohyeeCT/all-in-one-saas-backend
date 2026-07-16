import unittest
from unittest.mock import patch

from routers import all_in_one
from routers.all_in_one import AIOSettings


class AioScrapingSettingsTests(unittest.TestCase):
    def test_jina_remains_primary_and_firecrawl_fallback_stays_off(self):
        settings = AIOSettings()

        self.assertEqual(settings.scrape_provider, "jina")
        self.assertFalse(settings.firecrawl_fallback)

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
        firecrawl.assert_called_once_with("firecrawl", "https://example.com")

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
        firecrawl.assert_called_once_with("firecrawl", "https://example.com")


if __name__ == "__main__":
    unittest.main()
