import unittest
from unittest.mock import Mock, patch

import requests

from utils import faq_scraper, scraper
from utils.owned_page import build_owned_page_registry


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

    def test_owned_page_scraper_recovers_when_selector_removes_page_content(self):
        diagnostic = Mock(status_code=200)
        diagnostic.text = (
            "Warning: This page contains iframe that are currently hidden, consider enabling iframe processing.\n\n"
            "Images:\nThis page does not seem to contain any images.\n\n"
            "Links/Buttons:\nThis page does not seem to contain any buttons/links."
        )
        diagnostic.raise_for_status.return_value = None

        recovered = Mock(status_code=200)
        recovered.text = (
            "Title: Dhukka Law Firm\n\n"
            "# Experienced Legal Representation\n\n"
            "Dhukka Law Firm represents clients with substantive legal guidance and practical support."
        )
        recovered.raise_for_status.return_value = None

        with patch.object(
            faq_scraper.requests,
            "get",
            side_effect=[diagnostic, recovered],
        ) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://www.dhukkalawfirm.com/",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "live_selector_recovery")
        self.assertEqual(get.call_count, 2)
        self.assertIn("X-Remove-Selector", get.call_args_list[0].kwargs["headers"])
        recovery_headers = get.call_args_list[1].kwargs["headers"]
        self.assertNotIn("X-Remove-Selector", recovery_headers)
        self.assertEqual(recovery_headers["X-No-Cache"], "true")
        self.assertEqual(recovery_headers["X-Timeout"], "180")

    def test_owned_page_scraper_uses_cache_when_selector_recovery_is_still_diagnostic(self):
        diagnostic = Mock(status_code=200)
        diagnostic.text = (
            "Warning: This page contains iframe that are currently hidden, consider enabling iframe processing.\n\n"
            "Images:\nThis page does not seem to contain any images.\n\n"
            "Links/Buttons:\nThis page does not seem to contain any buttons/links."
        )
        diagnostic.raise_for_status.return_value = None

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
            side_effect=[diagnostic, diagnostic, cached],
        ) as get:
            result = faq_scraper.scrape_page_context("jina-key", "https://example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "cached_fallback")
        self.assertEqual(get.call_count, 3)
        cached_headers = get.call_args_list[2].kwargs["headers"]
        self.assertNotIn("X-No-Cache", cached_headers)
        self.assertNotIn("X-Remove-Selector", cached_headers)
        self.assertNotIn("X-Timeout", cached_headers)

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

    def test_versioned_aio_collection_capture_rejects_navigation_and_keeps_product_evidence(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Party Cowboy Hats\n\n"
            "# Party Cowboy Hats\n\n"
            "[Home](https://example.com/)\n\n"
            "[About Us](https://example.com/pages/about)\n\n"
            "[Shop All](https://example.com/collections/all)\n\n"
            "## Filters\nBrand\nUltimate Party\nColor\nPink\n\n"
            "[Pink Cowboy Hat](https://example.com/products/pink-hat)\n$12.99\n\n"
            "[Light Up Cowboy Hat](https://example.com/products/light-up-hat)\n$18.99\n\n"
            "[Nested Shopify Hat](https://example.com/collections/cowboy-hats/products/nested-hat)\n$24.99\n\n"
            "## Choose the right hat\n\n"
            "[Compare light-up styles](https://example.com/guides/light-up-hats)\n\n"
            "Use the filters to compare colors, finishes, and available party styles.\n\n"
            "Use the filters to compare colors, finishes, and available party styles."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response):
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/collections/cowboy-hats",
                mode="ecommerce_collection",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertTrue(result["success"])
        self.assertNotIn("- Home |", result["content"])
        self.assertNotIn("- About Us |", result["content"])
        self.assertNotIn("- Shop All |", result["content"])
        self.assertIn("- Pink Cowboy Hat | $12.99", result["content"])
        self.assertIn("- Light Up Cowboy Hat | $18.99", result["content"])
        self.assertIn("- Nested Shopify Hat | $24.99", result["content"])
        self.assertNotIn("https://example.com/products/", result["content"])
        self.assertIn("Filters found:", result["content"])
        self.assertIn("- Brand: Ultimate Party", result["content"])
        self.assertIn("Compare light-up styles", result["content"])
        self.assertNotIn("https://example.com/guides/", result["content"])
        self.assertNotIn("Products found:", result["page_copy_content"])
        self.assertNotIn("Filters found:", result["page_copy_content"])
        self.assertNotIn("Pink Cowboy Hat", result["page_copy_content"])
        self.assertNotIn("colors", result["page_copy_content"].casefold())
        self.assertIn("Compare light-up styles", result["page_copy_content"])
        registry = build_owned_page_registry(result["content"])
        self.assertFalse(registry["truncated"])
        self.assertLessEqual(len(registry["blocks"]), 24)
        self.assertGreaterEqual(
            result["quality_diagnostics"]["duplicate_blocks_rejected"],
            1,
        )

    def test_versioned_aio_capture_preserves_short_page_blocks_and_visible_link_text(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Example Services\n\n"
            "# Example Services\n\n"
            "## Services\n\n"
            "Fast setup.\n\n"
            "Expert support.\n\n"
            "- Strategy\n- Implementation\n- Reporting\n\n"
            "[Read the size guide](https://example.com/pages/size-guide)\n\n"
            "[Home](https://example.com/)\n\n"
            "## Contact\n\n"
            "Call 212-555-0100."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response):
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/services",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertIn("Fast setup.", result["content"])
        self.assertIn("Expert support.", result["content"])
        self.assertIn("- Strategy", result["content"])
        self.assertIn("Read the size guide", result["content"])
        self.assertNotIn("https://example.com/pages/size-guide", result["content"])
        self.assertNotIn("\nHome\n", "\n" + result["content"] + "\n")
        self.assertIn("Call 212-555-0100.", result["content"])
        quality = result["quality_diagnostics"]
        self.assertGreaterEqual(quality["short_blocks_retained"], 3)
        self.assertEqual(quality["navigation_links_rejected"], 1)
        self.assertFalse(quality["mapping_truncated"])
        self.assertLessEqual(quality["mapped_block_count"], 24)

    def test_unversioned_capture_keeps_the_legacy_short_paragraph_filter(self):
        response = Mock(status_code=200)
        response.text = (
            "# Example Services\n\nFast setup.\n\n"
            "This longer paragraph remains available to legacy standalone callers."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response):
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/services",
            )

        self.assertNotIn("Fast setup.", result["content"])
        self.assertIn("longer paragraph", result["content"])
        self.assertNotIn("capture_version", result)
        self.assertNotIn("quality_diagnostics", result)

    def test_versioned_aio_capture_recovers_a_sparse_success_with_the_same_provider(self):
        sparse = Mock(status_code=200)
        social_chrome = "\n\n".join(
            f"[Facebook {index}](https://www.facebook.com/example/{index})"
            for index in range(180)
        )
        sparse.text = (
            "Title: Example\n\n# Example\n\n"
            "A small amount of page copy survived the first capture.\n\n"
            + social_chrome
        )
        sparse.raise_for_status.return_value = None
        recovered = Mock(status_code=200)
        recovered.text = "\n\n".join([
            "Title: Example",
            "# Example",
            "## Services",
            "Fast setup.",
            "Expert support.",
            "## Process",
            "Discovery and planning.",
            "Implementation and reporting.",
            "## Resources",
            "Size guide and project checklist.",
            "## Contact",
            "Call 212-555-0100.",
        ])
        recovered.raise_for_status.return_value = None

        with patch.object(
            faq_scraper.requests,
            "get",
            side_effect=[sparse, recovered],
        ) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertEqual(get.call_count, 2)
        self.assertEqual(result["source"], "live_selector_recovery")
        self.assertIn("Implementation and reporting.", result["content"])
        quality = result["quality_diagnostics"]
        self.assertTrue(quality["recovery_attempted"])
        self.assertTrue(quality["recovery_selected"])
        self.assertGreater(
            quality["recovery_retained_chars"],
            quality["primary_retained_chars"],
        )

    def test_valid_short_capture_does_not_pay_for_selector_recovery(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Compact Service\n\n# Compact Service\n\n"
            "A focused consultation with a documented recommendation."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/compact-service",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertEqual(get.call_count, 1)
        self.assertTrue(result["success"])
        self.assertFalse(result["quality_diagnostics"]["sparse"])
        self.assertNotIn("recovery_attempted", result["quality_diagnostics"])

    def test_versioned_default_capture_keeps_h1_price_and_short_product_facts(self):
        response = Mock(status_code=200)
        response.text = (
            "Title: Pink Hat\n\n# Pink Hat\n\n"
            "$12.99\n\nIn stock\n\nFree shipping\n\n"
            "## Details\n\nA lightweight party hat with an adjustable fit.\n\n"
            "Copyright 2026 Example. All rights reserved."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/products/pink-hat",
                mode="ecommerce_product",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertEqual(get.call_count, 1)
        self.assertIn("# Pink Hat", result["content"])
        self.assertIn("$12.99", result["content"])
        self.assertIn("In stock", result["content"])
        self.assertIn("Free shipping", result["content"])
        self.assertNotIn("All rights reserved", result["content"])
        self.assertLess(result["content"].index("# Pink Hat"), result["content"].index("## Details"))
        self.assertNotIn("$12.99", result["page_copy_content"])
        self.assertNotIn("In stock", result["page_copy_content"])
        self.assertNotIn("Free shipping", result["page_copy_content"])
        self.assertIn("A lightweight party hat with an adjustable fit.", result["page_copy_content"])

    def test_versioned_capture_keeps_repeated_facts_under_their_original_headings(self):
        result = faq_scraper._process_reader_text(
            (
                "# Product A\n\n$12.99\n\nIn stock\n\n"
                "## Product B\n\n$12.99\n\nIn stock\n\n"
                "## Availability\n\nSold out\n\nPickup available\n\n"
                "Usually ready\n\nCheck availability"
            ),
            10_000,
            capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
        )

        self.assertTrue(result["success"])
        self.assertIn("# Product A", result["content"])
        self.assertIn("## Product B", result["content"])
        self.assertEqual(result["content"].count("$12.99"), 2)
        self.assertEqual(result["content"].count("In stock"), 2)
        self.assertIn("Sold out", result["content"])
        self.assertIn("Pickup available", result["content"])
        self.assertIn("Usually ready", result["content"])
        self.assertIn("Check availability", result["content"])
        registry = build_owned_page_registry(result["content"])
        product_b_excerpts = [
            block["excerpt"]
            for block in registry["blocks"]
            if block["heading"] == "Product B"
        ]
        self.assertIn("$12.99", product_b_excerpts)
        self.assertIn("In stock", product_b_excerpts)

    def test_versioned_capture_deduplicates_and_keeps_later_sections_in_source_order(self):
        early_blocks = "\n\n".join(
            (
                f"General introductory block {index} explains the site and its options. "
                "Visitors can review information, compare available choices, and continue "
                "through the page when they are ready. " * 4
            )
            for index in range(18)
        )
        text = (
            "Title: Example\n\n# Example\n\n"
            + early_blocks
            + "\n\n## Services\n\n"
            "Expert planning for every project.\n\n"
            "Expert planning for every project.\n\n"
            "## Pricing\n\n$129.00\n\nTransparent project pricing.\n\n"
            "## Contact\n\nCall 212-555-0100."
        )

        result = faq_scraper._process_reader_text(
            text,
            10_000,
            capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
        )

        self.assertTrue(result["success"])
        self.assertLessEqual(result["cleaned_chars"], 6_800)
        self.assertEqual(result["content"].count("Expert planning for every project."), 1)
        self.assertIn("$129.00", result["content"])
        self.assertIn("Call 212-555-0100.", result["content"])
        self.assertLess(result["content"].index("## Services"), result["content"].index("## Pricing"))
        self.assertLess(result["content"].index("## Pricing"), result["content"].index("## Contact"))
        self.assertGreaterEqual(
            result["quality_diagnostics"]["duplicate_blocks_rejected"],
            1,
        )
        registry = build_owned_page_registry(result["content"])
        self.assertFalse(registry["truncated"])

    def test_versioned_empty_collection_does_not_treat_synthetic_label_as_evidence(self):
        result = faq_scraper._process_reader_text(
            (
                "Title: Empty Collection\n\n# Empty Collection\n\n"
                "[Home](https://example.com/)\n\n"
                "[Shop All](https://example.com/collections/all)\n\n"
                "Add to cart"
            ),
            10_000,
            mode="ecommerce_collection",
            capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["content"], "")
        self.assertNotEqual(result["content"], "COLLECTION CONTEXT")

    def test_structurally_healthy_short_capture_does_not_trigger_recovery(self):
        response = Mock(status_code=200)
        response.text = "\n\n".join([
            "Title: Example",
            "# Example",
            "## Services",
            "Service planning includes discovery, prioritization, implementation, and clear reporting.",
            "Teams receive practical guidance for each stage of the engagement.",
            "## Process",
            "The process covers assessment, recommendations, delivery, measurement, and documented next steps.",
            "Each project follows the scope and information confirmed with the client.",
            "## Resources",
            "Reference guides explain available services and useful preparation details.",
            "## Contact",
            "Contact the team to discuss the page and the requested service.",
        ])
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertEqual(get.call_count, 1)
        self.assertLess(result["cleaned_chars"], 5_000)
        self.assertFalse(result["quality_diagnostics"]["sparse"])
        self.assertNotIn("recovery_attempted", result["quality_diagnostics"])

    def test_long_navigation_heavy_collection_capture_keeps_page_evidence_within_v1_mapping(self):
        navigation = "\n\n".join(
            f"[Browse collection {index}](https://example.com/collections/category-{index})"
            for index in range(520)
        )
        response = Mock(status_code=200)
        response.text = (
            "Title: Party Hats\n\n# Party Hats\n\n"
            + navigation
            + "\n\n## Filters\nBrand\nUltimate Party\nColor\nPink\n\n"
            + "[Pink Cowboy Hat](https://example.com/products/pink-hat)\n$12.99\n\n"
            + "[Light Up Cowboy Hat](https://example.com/products/light-up-hat)\n$18.99\n\n"
            + "## How to choose\n\n"
            + "Read the size guide before selecting a color and finish for the event."
        )
        response.raise_for_status.return_value = None

        with patch.object(faq_scraper.requests, "get", return_value=response) as get:
            result = faq_scraper.scrape_page_context(
                "jina-key",
                "https://example.com/collections/party-hats",
                mode="ecommerce_collection",
                capture_version=faq_scraper.AIO_OWNED_PAGE_CAPTURE_VERSION,
            )

        self.assertGreater(result["raw_chars"], 30_000)
        self.assertEqual(get.call_count, 1)
        self.assertLessEqual(result["cleaned_chars"], 6_800)
        self.assertNotIn("Browse collection", result["content"])
        self.assertIn("- Pink Cowboy Hat | $12.99", result["content"])
        self.assertIn("- Light Up Cowboy Hat | $18.99", result["content"])
        self.assertIn("## How to choose", result["content"])
        self.assertEqual(result["content"].count("$12.99"), 1)
        self.assertEqual(result["content"].count("$18.99"), 1)
        quality = result["quality_diagnostics"]
        self.assertGreaterEqual(quality["navigation_links_rejected"], 520)
        self.assertFalse(quality["mapping_truncated"])
        self.assertLessEqual(quality["mapped_block_count"], 24)

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
