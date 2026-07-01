import json
import sys
import types
import unittest

from utils import copy_gen
from utils.templates import get_template


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
        self.assertEqual(copy_gen.DEFAULT_MODELS["Claude"], "claude-sonnet-5")

    def test_sonnet_5_request_leaves_thinking_unset(self):
        options = copy_gen._anthropic_request_options("claude-sonnet-5", 1500)

        self.assertEqual(options["max_tokens"], 1500)
        self.assertNotIn("thinking", options)

    def test_non_sonnet_5_request_leaves_thinking_unset(self):
        options = copy_gen._anthropic_request_options("claude-sonnet-4-6", 1500)

        self.assertNotIn("thinking", options)

    def test_claude_high_token_request_uses_streaming(self):
        captured = {}

        class FakeStream:
            text_stream = ["Generated ", "copy"]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeMessages:
            def create(self, **kwargs):
                raise AssertionError("High-token Claude calls should stream")

            def stream(self, **kwargs):
                captured.update(kwargs)
                return FakeStream()

        class FakeAnthropic:
            def __init__(self, api_key):
                self.messages = FakeMessages()

        anthropic_stub = types.ModuleType("anthropic")
        anthropic_stub.Anthropic = FakeAnthropic
        original_anthropic = sys.modules.get("anthropic")
        sys.modules["anthropic"] = anthropic_stub
        try:
            text = copy_gen._call_claude(
                "key",
                "prompt",
                max_tokens=copy_gen.CLAUDE_STREAMING_TOKEN_THRESHOLD + 1,
                model="claude-sonnet-5",
            )
        finally:
            if original_anthropic is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = original_anthropic

        self.assertEqual(text, "Generated copy")
        self.assertEqual(captured["max_tokens"], copy_gen.CLAUDE_STREAMING_TOKEN_THRESHOLD + 1)
        self.assertNotIn("thinking", captured)

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
            captured["max_tokens"] = max_tokens
            captured["model"] = model
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
        self.assertEqual(captured["max_tokens"], copy_gen.FAQ_MAX_TOKENS)
        self.assertIn("UNSUPPORTED CLAIM RULES:", captured["prompt"])
        self.assertIn("Do not use neutral fallback wording", captured["prompt"])
        self.assertIn("Treat AI Overview and PAA as research signals, not proof", captured["prompt"])
        self.assertIn("Match answer length to question complexity", captured["prompt"])
        self.assertIn("Simple yes/no or definition questions: 1-2 direct sentences, about 20-45 words.", captured["prompt"])
        self.assertIn("Vary question starter types across the FAQ set", captured["prompt"])
        self.assertIn("Avoid using more than 2 questions with the same starter word", captured["prompt"])
        self.assertIn("No AI Overview or PAA data is available for this keyword.", captured["prompt"])
        self.assertIn("Never use these phrases: banned phrase, cheap", captured["prompt"])
        self.assertIn("BRAND NAME NATURALNESS RULES:", captured["prompt"])
        self.assertIn("MAIN KEYWORD NATURALNESS RULES:", captured["prompt"])
        self.assertIn("Do not force the brand name into every FAQ", captured["prompt"])
        self.assertIn("Do not force the keyword into every FAQ", captured["prompt"])
        self.assertNotIn("Keep answers 40 to 80 words", captured["prompt"])

    def test_product_faq_prompt_limits_product_name_repetition(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            return json.dumps([
                {
                    "question": "How should shoppers compare this item?",
                    "answer": "They should compare fit, use case, and supported details from the page.",
                    "source": "generated",
                }
            ])

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        copy_gen.generate_faq(
            provider="Test",
            api_key="key",
            keyword="fierce fruit raspberry puree",
            page_type="product",
            brand_name="Example",
            business_type="ecommerce",
            h1="Fierce Fruit Raspberry Puree",
            ai_overview_sections=[],
            ai_overview_raw="",
            paa_items=[],
            num_faqs=1,
            forbidden_phrases="",
            page_context="Product details about a raspberry puree.",
        )

        self.assertIn("PRODUCT NAME NATURALNESS RULES:", captured["prompt"])
        self.assertIn("Use the product name 2 or 3 times max", captured["prompt"])
        self.assertIn("Do not replace the full product name with half-name variations", captured["prompt"])

    def test_collection_faq_prompt_blocks_inventory_specific_claims(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            return json.dumps([
                {
                    "question": "How should shoppers compare options?",
                    "answer": "They should compare stable category fit and supported product details.",
                    "source": "generated",
                }
            ])

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        copy_gen.generate_faq(
            provider="Test",
            api_key="key",
            keyword="chicken party appetizers",
            page_type="collection",
            brand_name="Example",
            business_type="ecommerce",
            h1="Chicken Party Appetizers",
            ai_overview_sections=[],
            ai_overview_raw="",
            paa_items=[],
            num_faqs=1,
            forbidden_phrases="",
            page_context="COLLECTION CONTEXT: category grid with prices, filters, and product cards.",
        )

        self.assertIn("ECOMMERCE COLLECTION FAQ RULES:", captured["prompt"])
        self.assertIn("Do not mention exact prices", captured["prompt"])
        self.assertIn("Do not mention exact product counts", captured["prompt"])
        self.assertIn("Do not quote exact product names", captured["prompt"])

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

    def test_section_prompt_limits_are_named_and_keep_current_values(self):
        self.assertEqual(copy_gen.SECTION_LSI_KEYWORD_LIMIT, 3)
        self.assertEqual(copy_gen.SECTION_PAA_QUESTION_LIMIT, 5)
        self.assertEqual(copy_gen.SECTION_COMPETITOR_EXCERPT_LIMIT, 3)
        self.assertEqual(copy_gen.SECTION_EXISTING_CONTENT_CHAR_LIMIT, 400)
        self.assertEqual(copy_gen.SECTION_CLIENT_BRIEF_CHAR_LIMIT, 300)
        self.assertEqual(copy_gen.SECTION_PREVIOUS_CONTEXT_CHAR_LIMIT, 300)
        self.assertEqual(copy_gen.SECTION_AI_OVERVIEW_CHAR_LIMIT, 600)
        self.assertEqual(copy_gen.SECTION_REVIEWER_NOTE_LIMIT, 5)
        self.assertEqual(copy_gen.SECTION_REVIEWER_NOTE_CHAR_LIMIT, 300)

    def test_generate_copy_routes_through_provider_function(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
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
        self.assertEqual(captured["max_tokens"], copy_gen.META_MAX_TOKENS)
        self.assertIn("Title should aim for up to 90 characters.", captured["prompt"])
        self.assertIn("Meta description should aim for up to 200 characters.", captured["prompt"])
        self.assertIn("H1 has no hard character limit but should aim for under 80 characters.", captured["prompt"])
        self.assertIn("BRAND CONTEXT:", captured["prompt"])
        self.assertIn("- Voice: Plainspoken expert", captured["prompt"])
        self.assertIn("- Tone: Confident", captured["prompt"])
        self.assertIn("- Target audience: Facilities managers", captured["prompt"])
        self.assertNotIn("Title maximum 60 characters", captured["prompt"])
        self.assertNotIn("Meta description maximum 155 characters", captured["prompt"])

    def test_generate_copy_extracts_json_from_wrapped_response(self):
        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            return (
                'Here is the metadata:\n'
                '{"title":"Example Service","description":"Learn about the Example service.",'
                '"h1_optimised":"Example Service"}'
            )

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
        self.assertEqual(result["description"], "Learn about the Example service.")

    def test_generate_page_passes_aio_and_forbidden_phrases_to_sections(self):
        captured = []

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured.append({"prompt": prompt, "max_tokens": max_tokens, "model": model})
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
        self.assertEqual(captured[0]["max_tokens"], copy_gen.PAGE_SECTION_MAX_TOKENS)
        self.assertIn("Google AI Overview for this topic", captured[0]["prompt"])
        self.assertIn("Google says accuracy and maintenance are important.", captured[0]["prompt"])
        self.assertIn("Never use these phrases: cheap, free audit", captured[0]["prompt"])
        self.assertIn("You may adjust word order, add small connecting words, or use a close grammatical variation", captured[0]["prompt"])
        self.assertIn("Do not force the keyword at the beginning of the first sentence", captured[0]["prompt"])
        self.assertIn("A keyword used awkwardly is worse than not using it", captured[0]["prompt"])
        self.assertIn("The first sentence must communicate the core topic, benefit, or value", captured[0]["prompt"])
        self.assertIn("If the brand name appears, use exact casing: Example", captured[0]["prompt"])
        self.assertNotIn("Brand name must appear exactly as:", captured[0]["prompt"])

    def test_collection_template_uses_shorter_intro_and_single_guidance_section(self):
        template = get_template("collection_page")
        section_names = [section["name"] for section in template["sections"]]
        section_labels = [section["label"] for section in template["sections"]]
        intro = template["sections"][0]

        self.assertEqual(intro["name"], "category_intro")
        self.assertLessEqual(intro["word_count"][1], 120)
        self.assertIn("collection_guidance", section_names)
        self.assertNotIn("buying_guide", section_names)
        self.assertNotIn("subcategory_overview", section_names)
        self.assertNotIn("brand_value", section_names)
        self.assertNotIn("How to Choose", section_labels)
        self.assertNotIn("What's in This Collection", section_labels)
        self.assertNotIn("Why Shop With Us", section_labels)

    def test_section_prompt_blocks_generic_collection_language_and_unsupported_facts(self):
        prompt = copy_gen._build_section_prompt(
            section={
                "name": "category_intro",
                "label": "Category Introduction",
                "purpose": "Introduce the category.",
                "word_count": [60, 110],
                "keyword_slot": "primary",
                "heading_level": "h1",
                "prompt_rules": "Write directly.",
            },
            primary_keyword="chicken party appetizers",
            supporting_keyword="",
            lsi_keywords=[],
            business_type="ecommerce",
            brand_name="Perdue",
            h1="Chicken Party Appetizers",
            page_type="collection",
            paa_questions=[],
            competitor_excerpts=[],
            client_brief="",
            previous_section_text="",
            client_existing_content="",
        )

        self.assertIn("Do not write phrases like 'this page', 'this collection', 'this category'", prompt)
        self.assertIn("Do not invent product groupings, package sizes, event scales", prompt)
        self.assertIn("Competitor context is topic inspiration, not proof of client facts", prompt)
        self.assertIn("Finding the right", prompt)

    def test_build_section_prompt_includes_reviewer_corrections(self):
        prompt = copy_gen._build_section_prompt(
            section={
                "name": "benefits",
                "label": "Benefits",
                "purpose": "Explain the benefits.",
                "word_count": [50, 80],
                "keyword_slot": "primary",
                "heading_level": "h2",
                "prompt_rules": "Write clearly.",
            },
            primary_keyword="industrial dosing systems",
            supporting_keyword="",
            lsi_keywords=[],
            business_type="service",
            brand_name="Example",
            h1="Industrial Dosing Systems",
            page_type="service",
            paa_questions=[],
            competitor_excerpts=[],
            client_brief="",
            previous_section_text="",
            client_existing_content="",
            reviewer_corrections=[
                "too salesy, make it factual",
                "lead with the spec",
            ],
        )

        self.assertIn("Reviewer correction notes for this rerun", prompt)
        self.assertIn("too salesy, make it factual", prompt)
        self.assertIn("lead with the spec", prompt)
        self.assertIn("Treat the latest correction as highest priority", prompt)

    def test_score_brand_consistency_routes_through_provider_function(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["api_key"] = api_key
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            captured["model"] = model
            return json.dumps({
                "score": 64,
                "reason": "The copy is softer than the requested technical tone.",
            })

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        result = copy_gen.score_brand_consistency(
            provider="Test",
            api_key="key",
            model="brand-review-model",
            brand_profile={"tone_of_voice": "precise and technical", "words_to_avoid": "cheap"},
            outputs={
                "meta": "Industrial dosing systems for technical teams.",
                "page_copy": "Helpful, friendly copy that sounds casual.",
            },
        )

        self.assertEqual(result["score"], 64)
        self.assertEqual(captured["api_key"], "key")
        self.assertEqual(captured["model"], "brand-review-model")
        self.assertEqual(captured["max_tokens"], copy_gen.DIAGNOSTIC_MAX_TOKENS)
        self.assertIn("Return strict JSON", captured["prompt"])
        self.assertIn("precise and technical", captured["prompt"])
        self.assertIn("cheap", captured["prompt"])

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
        self.assertIn("BRAND NAME NATURALNESS RULES:", captured["prompt"])
        self.assertIn("MAIN KEYWORD NATURALNESS RULES:", captured["prompt"])
        self.assertNotIn("Keep answers 40 to 80 words", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
