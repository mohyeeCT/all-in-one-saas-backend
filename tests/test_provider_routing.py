import json
import sys
import types
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

    def test_openai_default_uses_current_gpt_5_model(self):
        self.assertEqual(copy_gen.DEFAULT_MODELS["OpenAI"], "gpt-5.5")
        self.assertNotEqual(copy_gen.DEFAULT_MODELS["OpenAI"], "gpt-4o-mini")

    def test_openai_gpt5_uses_max_completion_tokens(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))
                    ]
                )

        class FakeClient:
            def __init__(self, api_key):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        openai_stub = types.ModuleType("openai")
        openai_stub.OpenAI = FakeClient
        original_openai = sys.modules.get("openai")
        sys.modules["openai"] = openai_stub
        try:
            copy_gen._call_openai("key", "prompt", max_tokens=123, model="gpt-5.5")
        finally:
            if original_openai is None:
                sys.modules.pop("openai", None)
            else:
                sys.modules["openai"] = original_openai

        self.assertEqual(captured["model"], "gpt-5.5")
        self.assertEqual(captured["max_completion_tokens"], 123)
        self.assertNotIn("max_tokens", captured)

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
