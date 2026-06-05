import json
import unittest

from utils import copy_gen


class ProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.original_provider = copy_gen.PROVIDER_FN.get("Test")

    def tearDown(self):
        if self.original_provider is None:
            copy_gen.PROVIDER_FN.pop("Test", None)
        else:
            copy_gen.PROVIDER_FN["Test"] = self.original_provider

    def test_generate_faq_routes_through_provider_function(self):
        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            return json.dumps([
                {
                    "question": "What does the service include?",
                    "answer": "It includes a clear, practical service.",
                    "source": "generated",
                }
            ])

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        result = copy_gen.generate_faq(
            provider="Test",
            api_key="key",
            keyword="service",
            page_type="service",
            brand_name="Example",
            business_type="service",
            h1="Example Service",
            ai_overview_sections=[],
            ai_overview_raw="",
            paa_items=[],
            num_faqs=1,
        )

        self.assertEqual(result[0]["question"], "What does the service include?")

    def test_generate_copy_routes_through_provider_function(self):
        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            return json.dumps({
                "title": "Example Service",
                "description": "Learn about the Example service.",
                "h1_optimised": "Example Service",
            })

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        result = copy_gen.generate_copy(
            provider="Test",
            api_key="key",
            url="https://example.com/service",
            keyword="example service",
            page_type="service",
            brand_name="Example",
            forbidden_phrases="",
            context="",
            business_type="service",
            h1="Example Service",
        )

        self.assertEqual(result["title"], "Example Service")
        self.assertEqual(result["h1_optimised"], "Example Service")

    def test_generate_faq_batch_routes_through_provider_function(self):
        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            return json.dumps({
                "1": [
                    {
                        "question": "What does the service include?",
                        "answer": "It includes a clear, practical service.",
                        "source": "generated",
                    }
                ]
            })

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        results, _, _ = copy_gen.generate_faq_batch(
            provider="Test",
            api_key="key",
            pages=[
                {
                    "keyword": "service",
                    "page_type": "service",
                    "brand_name": "Example",
                    "business_type": "service",
                    "h1": "Example Service",
                    "ai_overview_sections": [],
                    "paa_items": [],
                }
            ],
            num_faqs=1,
        )

        self.assertEqual(results[0][0]["question"], "What does the service include?")


if __name__ == "__main__":
    unittest.main()
