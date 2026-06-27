import unittest
from unittest.mock import patch

from utils import scraper


class ScraperTests(unittest.TestCase):
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
