import unittest
from unittest.mock import Mock, patch

import requests

from utils import faq_scraper, scraper


class ScraperTests(unittest.TestCase):
    def test_owned_page_scraper_allows_slow_jina_render(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Example Page\n\n"
            "# Example Page\n\n"
            "This page contains enough substantive content for the scraper to retain."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response) as get:
            result = faq_scraper.scrape_page_context("jina-key", "https://example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "live")
        self.assertEqual(get.call_args.kwargs["headers"]["X-Timeout"], "180")
        self.assertEqual(get.call_args.kwargs["timeout"], 200)

    def test_owned_page_scraper_uses_cached_fallback_after_timeout(self):
        cached = Mock(status_code=200)
        cached.text = (
            "Title: Cached Page\n\n"
            "# Cached Page\n\n"
            "This cached snapshot contains enough useful page content for generation."
        )
        cached.raise_for_status.return_value = None

        with patch.object(
            faq_scraper.requests,
            "get",
            side_effect=[requests.exceptions.Timeout(), cached],
        ) as get:
            result = faq_scraper.scrape_page_context("jina-key", "https://example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "cached_fallback")
        self.assertEqual(get.call_count, 2)
        fallback = get.call_args_list[1]
        self.assertEqual(fallback.kwargs["timeout"], 30)
        self.assertNotIn("X-No-Cache", fallback.kwargs["headers"])
        self.assertNotIn("X-Remove-Selector", fallback.kwargs["headers"])
        self.assertNotIn("X-Timeout", fallback.kwargs["headers"])

    def test_owned_page_firecrawl_uses_fresh_v2_scrape(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "data": {
                "markdown": "# Example Page\n\nThis page contains enough substantive content for generation.",
                "metadata": {"title": "Example Page"},
            },
        }

        with patch.object(faq_scraper.requests, "post", return_value=response) as post:
            result = faq_scraper.scrape_page_context_firecrawl(
                "firecrawl-key",
                "https://example.com",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "firecrawl")
        self.assertEqual(post.call_args.args[0], "https://api.firecrawl.dev/v2/scrape")
        self.assertEqual(post.call_args.kwargs["timeout"], 135)
        self.assertEqual(post.call_args.kwargs["json"]["timeout"], 120000)
        self.assertEqual(post.call_args.kwargs["json"]["maxAge"], 0)
        self.assertFalse(post.call_args.kwargs["json"]["storeInCache"])

    def test_owned_page_collection_mode_preserves_products_prices_and_filters(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Party Cowboy Hats\n\n"
            "# Party Cowboy Hats\n\n"
            "## Filters\nBrand\nUltimate Party\n\n"
            "[Pink Cowboy Hat](https://example.com/products/pink-hat)\n$12.99\n\n"
            "[Light Up Cowboy Hat](https://example.com/products/light-up-hat)\n$18.99\n"
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response):
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/collections/cowboy-hats",
                mode="ecommerce_collection",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "ecommerce_collection")
        self.assertIn("Products found:", result["content"])
        self.assertIn("Pink Cowboy Hat | $12.99", result["content"])
        self.assertIn("Light Up Cowboy Hat | $18.99", result["content"])
        self.assertIn("Filters found:", result["content"])
        self.assertEqual(result["raw_chars"], len(response.text.strip()))
        self.assertEqual(result["cleaned_chars"], len(result["content"]))

    def test_scrape_url_sends_jina_authorization_when_api_key_is_provided(self):
        captured = {}

        class Response:
            text = "# Product Page\nUseful content for the page."

            def raise_for_status(self):
                return None

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return Response()

        with patch.object(scraper.requests, "get", side_effect=fake_get):
            result = scraper.scrape_url("https://example.com/products/widget", api_key="jina-key")

        self.assertTrue(result["success"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer jina-key")

    def test_ecommerce_competitor_filter_allows_product_and_collection_pages(self):
        product_scrape = {
            "url": "https://shop.example.com/products/widget",
            "word_count": 180,
            "headings": [{"level": 2, "text": "Details"}],
        }
        collection_scrape = {
            "url": "https://shop.example.com/collections/widgets",
            "word_count": 220,
            "headings": [{"level": 2, "text": "Buying Guide"}],
        }

        self.assertTrue(scraper.is_editorial_competitor(product_scrape, "product"))
        self.assertTrue(scraper.is_editorial_competitor(product_scrape, "product_page"))
        self.assertTrue(scraper.is_editorial_competitor(collection_scrape, "collection"))
        self.assertTrue(scraper.is_editorial_competitor(collection_scrape, "collection_page"))

    def test_ecommerce_relevance_scores_product_and_collection_signals(self):
        product_score = scraper.classify_competitor_relevance(
            {
                "url": "https://shop.example.com/products/widget",
                "title": "Widget Product",
                "body_text": "Add to cart. Product details, size, materials, reviews, specifications.",
                "headings": [{"level": 2, "text": "Product Details"}],
                "word_count": 220,
            },
            business_type="ecommerce",
            page_type="product",
        )
        collection_score = scraper.classify_competitor_relevance(
            {
                "url": "https://shop.example.com/collections/widgets",
                "title": "Widget Collection",
                "body_text": "Shop the collection. Compare products, sizes, materials, and buying guide.",
                "headings": [{"level": 2, "text": "Buying Guide"}],
                "word_count": 260,
            },
            business_type="ecommerce",
            page_type="collection",
        )

        self.assertGreater(product_score, 0)
        self.assertGreater(collection_score, 0)


if __name__ == "__main__":
    unittest.main()
