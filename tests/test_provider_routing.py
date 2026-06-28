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

    def test_claude_default_uses_sonnet(self):
        self.assertEqual(copy_gen.DEFAULT_MODELS["Claude"], "claude-sonnet-4-6")

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
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
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
            forbidden_phrases="banned phrase",
            page_context="Product page context.",
            brand_profile={"words_to_avoid": "cheap", "tone": "Helpful"},
        )

        self.assertEqual(result[0]["question"], "What does the service include?")
        self.assertIn("UNSUPPORTED CLAIM RULES:", captured["prompt"])
        self.assertIn("Do not use neutral fallback wording", captured["prompt"])
        self.assertIn("Treat AI Overview and PAA as research signals, not proof", captured["prompt"])
        self.assertIn("Match answer length to question complexity", captured["prompt"])
        self.assertIn("Simple yes/no or definition questions: 1-2 direct sentences, about 20-45 words.", captured["prompt"])
        self.assertIn("Vary question starter types across the FAQ set", captured["prompt"])
        self.assertIn("Avoid using more than 2 questions with the same starter word", captured["prompt"])
        self.assertIn("No AI Overview or PAA data is available for this keyword.", captured["prompt"])
        self.assertIn("Never use these phrases: banned phrase, cheap", captured["prompt"])
        self.assertNotIn("Keep answers 40 to 80 words", captured["prompt"])

    def test_paa_answer_snippets_are_sentence_aware(self):
        answer = (
            "This first sentence should remain intact. "
            "This second sentence should also remain because the shared snippet limit is higher. "
            "This third sentence is intentionally long enough to push the text beyond the snippet limit so it should be removed, "
            "with extra explanatory detail about unrelated product attributes, policy ideas, comparisons, buyer concerns, "
            "and other filler that should never appear in the final sentence-aware snippet."
        )

        snippet = copy_gen._format_paa_answer_snippet(answer)

        self.assertEqual(
            snippet,
            "This first sentence should remain intact. This second sentence should also remain because the shared snippet limit is higher.",
        )

    def test_generate_copy_routes_through_provider_function(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            captured["model"] = model
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
            brand_context=(
                "BRAND CONTEXT:\n"
                "- Voice: Plainspoken expert\n"
                "- Tone: Confident\n"
                "- Target audience: Facilities managers"
            ),
            business_type="service",
            h1="Example Service",
            model="test-meta-model",
        )

        self.assertEqual(result["title"], "Example Service")
        self.assertEqual(result["h1_optimised"], "Example Service")
        self.assertEqual(captured["model"], "test-meta-model")
        self.assertIn("Title should aim for up to 90 characters.", captured["prompt"])
        self.assertIn("Meta description should aim for up to 200 characters.", captured["prompt"])
        self.assertIn("H1 has no hard character limit but should aim for under 80 characters.", captured["prompt"])
        self.assertIn("BRAND CONTEXT:", captured["prompt"])
        self.assertIn("- Voice: Plainspoken expert", captured["prompt"])
        self.assertIn("- Tone: Confident", captured["prompt"])
        self.assertIn("- Target audience: Facilities managers", captured["prompt"])
        self.assertNotIn("Title maximum 60 characters", captured["prompt"])
        self.assertNotIn("Meta description maximum 155 characters", captured["prompt"])

    def test_generate_page_passes_aio_and_forbidden_phrases_to_sections(self):
        captured = []

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured.append({"prompt": prompt, "model": model})
            return "Generated section copy."

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        result = copy_gen.generate_page(
            template={
                "sections": [
                    {
                        "name": "intro",
                        "label": "Introduction",
                        "purpose": "Introduce the topic.",
                        "word_count": [20, 40],
                        "keyword_slot": "primary",
                        "heading_level": "none",
                        "prompt_rules": "Write directly.",
                    }
                ]
            },
            keyword_assignment={"intro": {"primary": "industrial dosing systems", "supporting": ""}},
            lsi_keywords={},
            business_type="service",
            brand_name="Example",
            h1="Industrial Dosing Systems",
            page_type="service",
            paa_questions=[],
            ai_overview="Google says accuracy and maintenance are important.",
            competitor_section_map={},
            client_brief="",
            client_existing_content="",
            provider="Test",
            api_key="key",
            model="test-page-model",
            forbidden_phrases="cheap, free audit",
        )

        self.assertEqual(result["intro"], "Generated section copy.")
        self.assertEqual(captured[0]["model"], "test-page-model")
        self.assertIn("Google AI Overview for this topic", captured[0]["prompt"])
        self.assertIn("Google says accuracy and maintenance are important.", captured[0]["prompt"])
        self.assertIn("Never use these phrases: cheap, free audit", captured[0]["prompt"])

    def test_generate_faq_batch_routes_through_provider_function(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
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
                    "forbidden_phrases": "banned phrase",
                    "page_context": "Service page context.",
                    "brand_profile": {"words_to_avoid": "cheap", "tone": "Helpful"},
                }
            ],
            num_faqs=1,
        )

        self.assertEqual(results[0][0]["question"], "What does the service include?")
        self.assertIn("UNSUPPORTED CLAIM RULES:", captured["prompt"])
        self.assertIn("Match answer length to question complexity", captured["prompt"])
        self.assertIn("Vary question starter types across the FAQ set", captured["prompt"])
        self.assertIn("No AI Overview or PAA data is available for this keyword.", captured["prompt"])
        self.assertIn("Never use: banned phrase, cheap", captured["prompt"])
        self.assertNotIn("Keep answers 40 to 80 words", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
