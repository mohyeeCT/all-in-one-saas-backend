import json
import sys
import types
import unittest
from unittest.mock import patch

from utils import copy_gen
from utils.templates import get_template


class ProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.original_provider = copy_gen.PROVIDER_FN.get("Test")
        self.original_delay = copy_gen.PROVIDER_DELAY.get("Test")

    def tearDown(self):
        if self.original_provider is None:
            copy_gen.PROVIDER_FN.pop("Test", None)
        else:
            copy_gen.PROVIDER_FN["Test"] = self.original_provider
        if self.original_delay is None:
            copy_gen.PROVIDER_DELAY.pop("Test", None)
        else:
            copy_gen.PROVIDER_DELAY["Test"] = self.original_delay

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

    def test_claude_strategy_request_can_use_medium_adaptive_effort(self):
        captured = {}

        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text="{}")]
                )

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
                max_tokens=copy_gen.STRATEGY_BRIEF_MAX_TOKENS,
                model="claude-sonnet-5",
                effort=copy_gen.STRATEGY_BRIEF_CLAUDE_EFFORT,
            )
        finally:
            if original_anthropic is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = original_anthropic

        self.assertEqual(text, "{}")
        self.assertEqual(captured["extra_body"]["thinking"], {"type": "adaptive"})
        self.assertEqual(
            captured["extra_body"]["output_config"],
            {"effort": copy_gen.STRATEGY_BRIEF_CLAUDE_EFFORT},
        )

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
        self.assertEqual(copy_gen.SECTION_PREVIOUS_CONTEXT_CHAR_LIMIT, 1200)
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
        self.assertIn("Title should be 50 to 80 characters.", captured["prompt"])
        self.assertIn("Meta description should be 140 to 180 characters.", captured["prompt"])
        self.assertIn("H1 has no hard character limit but should aim for under 80 characters.", captured["prompt"])
        self.assertIn("BRAND CONTEXT:", captured["prompt"])
        self.assertIn("- Voice: Plainspoken expert", captured["prompt"])
        self.assertIn("- Tone: Confident", captured["prompt"])
        self.assertIn("- Target audience: Facilities managers", captured["prompt"])
        self.assertNotIn("Title maximum 60 characters", captured["prompt"])
        self.assertNotIn("Meta description maximum 155 characters", captured["prompt"])

    def test_targeted_meta_and_faq_repairs_route_through_selected_provider(self):
        prompts = []
        responses = [
            json.dumps({
                "title": "Industrial Dosing Systems for Process Control",
                "description": "Clear metadata description for industrial operations teams.",
                "h1_optimised": "Industrial Dosing Systems",
            }),
            json.dumps([
                {
                    "question": "How do industrial dosing systems support process control?",
                    "answer": "They help teams manage repeatable chemical handling.",
                    "source": "generated",
                },
            ]),
        ]

        def fake_provider(_api_key, prompt, **_kwargs):
            prompts.append(prompt)
            return responses.pop(0)

        copy_gen.PROVIDER_FN["RepairTest"] = fake_provider
        meta = copy_gen.repair_meta_copy(
            provider="RepairTest",
            api_key="key",
            current={"title": "Bad!", "description": "Shop now!", "h1_optimised": "Example Services"},
            issues=["Title contains an exclamation mark."],
            url="https://example.com/service",
            keyword="industrial dosing systems",
            page_type="service",
            business_type="b2b",
            brand_name="Example",
            input_h1="Current Services",
        )
        faqs = copy_gen.repair_faq_items(
            provider="RepairTest",
            api_key="key",
            faq_items=[{"question": "Do you ship", "answer": ""}],
            issues=["FAQ is incomplete."],
            keyword="industrial dosing systems",
            page_type="service",
            business_type="b2b",
            brand_name="Example",
            num_faqs=1,
        )

        self.assertEqual(meta["h1_optimised"], "Industrial Dosing Systems")
        self.assertEqual(len(faqs), 1)
        self.assertIn("Change only what is needed", prompts[0])
        self.assertIn("Keep useful questions", prompts[1])
        self.assertTrue(all("consumer CTAs" in prompt for prompt in prompts))

    def test_generate_strategy_brief_routes_through_provider_function(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            captured["model"] = model
            return json.dumps({
                "search_intent": "Commercial investigation",
                "page_goal": "Help buyers understand the service and make contact.",
                "audience_need": "Proof that the provider can handle regulated work.",
                "primary_positioning": "Practical compliance support for regulated teams.",
                "supporting_attributes": ["Plainspoken guidance", "Implementation experience"],
                "headline_direction": "Lead with practical compliance support, not certification promises.",
                "recommended_angle": "Lead with practical compliance experience.",
                "brand_positioning": "Authoritative but plainspoken.",
                "verified_facts": [
                    {
                        "id": "F1",
                        "fact": "The current page mentions audits and implementation support.",
                        "source": "current_page",
                        "source_excerpt": "audits and implementation support",
                    },
                    {
                        "id": "F2",
                        "fact": "The client brief prioritises ISO experience.",
                        "source": "client_brief",
                        "source_excerpt": "Focus on ISO experience.",
                    },
                ],
                "facts_to_avoid": ["Guaranteed certification"],
                "proof_fact_ids": ["F1", "F2"],
                "claims_to_avoid": ["guaranteed rankings"],
                "competitor_gaps": ["Competitors do not explain the delivery process."],
                "meta_direction": "Mention compliance and implementation support.",
                "faq_direction": "Answer fit, process, and proof questions.",
                "section_guidance": [
                    {
                        "section": "intro",
                        "responsibility": "Frame the compliance problem.",
                        "guidance": "Lead with the operational risk.",
                        "proof_fact_ids": ["F1"],
                    },
                    {
                        "section": "process",
                        "responsibility": "Explain delivery.",
                        "guidance": "Show the implementation sequence.",
                        "proof_fact_ids": ["F1", "F2"],
                    },
                ],
            })

        copy_gen.PROVIDER_FN["Test"] = fake_provider

        brief = copy_gen.generate_strategy_brief(
            provider="Test",
            api_key="key",
            model="strategy-model",
            url="https://example.com/service",
            keyword="compliance consulting",
            page_type="service",
            business_type="b2b",
            brand_name="Example",
            h1="Compliance Consulting",
            brand_context=(
                "BRAND CONTEXT:\n"
                "- Voice: Plainspoken expert\n"
                "- Target audience: Operations leaders"
            ),
            client_brief="Avoid hype. Focus on ISO experience.",
            evidence_client_brief="Avoid hype. Focus on ISO experience.",
            page_context=(
                "The current page mentions audits and implementation support. "
                + ("A" * 2600)
                + " late-page-evidence"
            ),
            ai_overview="Search results mention risk, process, and certifications.",
            paa_questions=[{"question": "What does compliance consulting include?"}],
            competitor_section_map={"intro": ["Competitor talks about audit preparation."]},
            template_sections=[{"name": "intro", "label": "Introduction", "purpose": "Open the page."}],
        )

        self.assertEqual(brief["search_intent"], "Commercial investigation")
        self.assertEqual(brief["primary_positioning"], "Practical compliance support for regulated teams.")
        self.assertEqual(brief["supporting_attributes"], ["Plainspoken guidance", "Implementation experience"])
        self.assertEqual(
            brief["proof_points_to_use"],
            [
                "The current page mentions audits and implementation support.",
                "The client brief prioritises ISO experience.",
            ],
        )
        self.assertEqual(brief["verified_facts"][0]["id"], "F1")
        self.assertEqual(brief["verified_facts"][0]["source"], "current_page")
        self.assertEqual(brief["facts_to_avoid"], ["Guaranteed certification"])
        self.assertEqual(brief["section_guidance"][0]["section"], "intro")
        self.assertEqual(
            brief["section_guidance"][0]["proof_points"],
            ["The current page mentions audits and implementation support."],
        )
        self.assertEqual(
            brief["section_guidance"][1]["proof_points"],
            ["The client brief prioritises ISO experience."],
        )
        self.assertEqual(captured["model"], "strategy-model")
        self.assertEqual(captured["max_tokens"], copy_gen.STRATEGY_BRIEF_MAX_TOKENS)
        self.assertIn("BRAND CONTEXT:", captured["prompt"])
        self.assertIn("Plainspoken expert", captured["prompt"])
        self.assertIn("Search results mention risk, process, and certifications.", captured["prompt"])
        self.assertIn("Competitor talks about audit preparation.", captured["prompt"])
        self.assertIn("Never instruct a section to preserve the current H1", captured["prompt"])
        self.assertIn("Assign every selected proof ID to exactly one section", captured["prompt"])
        self.assertIn("Select page-level proof with proof_fact_ids", captured["prompt"])
        self.assertIn("Evidence precedence is: current owned-page content first", captured["prompt"])
        self.assertIn("Every verified fact must include an exact supporting excerpt", captured["prompt"])
        self.assertIn("late-page-evidence", captured["prompt"])

    def test_incomplete_strategy_brief_gets_one_bounded_repair(self):
        responses = [
            {"search_intent": "Commercial"},
            {
                "search_intent": "Commercial investigation",
                "page_goal": "Help operations teams evaluate the service.",
                "audience_need": "Clear evidence and delivery expectations.",
                "primary_positioning": "Practical compliance support.",
                "headline_direction": "Lead with practical compliance support.",
                "meta_direction": "Use clear service language.",
                "faq_direction": "Answer fit and process questions.",
                "section_guidance": [
                    {
                        "section": "intro",
                        "responsibility": "Frame the operational need.",
                        "guidance": "Explain the service without hype.",
                    },
                ],
            },
        ]
        captured_prompts = []

        def fake_provider(_api_key, prompt, **_kwargs):
            captured_prompts.append(prompt)
            return json.dumps(responses.pop(0))

        copy_gen.PROVIDER_FN["StrategyRepairTest"] = fake_provider
        brief = copy_gen.generate_strategy_brief(
            provider="StrategyRepairTest",
            api_key="key",
            url="https://example.com/service",
            keyword="compliance consulting",
            page_type="service",
            business_type="b2b",
            brand_name="Example",
            template_sections=[{"name": "intro", "label": "Introduction"}],
        )

        self.assertEqual(len(captured_prompts), 2)
        self.assertIn("CORRECTION REQUIRED", captured_prompts[1])
        self.assertEqual(brief["primary_positioning"], "Practical compliance support.")
        self.assertEqual(copy_gen.strategy_brief_issues(brief, [{"name": "intro"}]), [])

    def test_strategy_brief_rejects_unverified_and_mutable_profile_facts(self):
        brief = copy_gen._normalise_strategy_brief(
            {
                "verified_facts": [
                    {
                        "id": "F1",
                        "fact": "The business has nine operating locations.",
                        "source": "current_page",
                        "source_excerpt": "nine operating locations",
                    },
                    {
                        "id": "F2",
                        "fact": "Detroit Burger Brawl Champion 2016.",
                        "source": "brand_profile",
                        "source_excerpt": "Detroit Burger Brawl Champion 2016",
                    },
                    {
                        "id": "F3",
                        "fact": "The business has 10 locations.",
                        "source": "brand_profile",
                        "source_excerpt": "10 locations",
                    },
                    {
                        "id": "F4",
                        "fact": "Every location uses the same recipe.",
                        "source": "current_page",
                        "source_excerpt": "same recipe",
                    },
                    {
                        "id": "F5",
                        "fact": "A stable but unused fact.",
                        "source": "brand_profile",
                        "source_excerpt": "stable but unused fact",
                    },
                ],
                "proof_fact_ids": ["F1", "F2", "F3", "F4", "F5"],
                "section_guidance": [
                    {
                        "section": "hero",
                        "guidance": "Lead with current scale.",
                        "proof_fact_ids": ["F1", "F4"],
                    },
                    {
                        "section": "social_proof",
                        "guidance": "Use stable recognition.",
                        "proof_fact_ids": ["F3", "F2"],
                    },
                ],
            },
            evidence_sources={
                "current_page": "The business has nine operating locations. Monroe is coming soon.",
                "client_brief": "",
                "brand_profile": (
                    "Detroit Burger Brawl Champion 2016. "
                    "The business has 10 locations. A stable but unused fact."
                ),
            },
        )

        verified = [item["fact"] for item in brief["verified_facts"]]
        self.assertEqual(
            verified,
            [
                "The business has nine operating locations.",
                "Detroit Burger Brawl Champion 2016.",
                "A stable but unused fact.",
            ],
        )
        self.assertEqual(brief["proof_points_to_use"], verified[:2])
        self.assertIn("The business has 10 locations.", brief["facts_to_avoid"])
        self.assertIn("Every location uses the same recipe.", brief["facts_to_avoid"])
        self.assertEqual(
            brief["section_guidance"][0]["proof_points"],
            ["The business has nine operating locations."],
        )
        self.assertEqual(
            brief["section_guidance"][1]["proof_points"],
            ["Detroit Burger Brawl Champion 2016."],
        )

        meta_strategy = copy_gen.format_strategy_brief_for_prompt(brief, output_type="meta")
        page_strategy = copy_gen.format_strategy_brief_for_prompt(
            brief,
            output_type="page",
            section_names=["hero"],
            include_headline_direction=True,
        )
        self.assertIn("The business has nine operating locations.", meta_strategy)
        self.assertNotIn("The business has 10 locations.", meta_strategy)
        self.assertIn("Do not state an exact number of locations.", meta_strategy)
        self.assertIn("The business has nine operating locations.", page_strategy)
        self.assertNotIn("Detroit Burger Brawl Champion 2016.", page_strategy)

    def test_strategy_brief_is_added_to_meta_faq_and_page_prompts(self):
        strategy_brief = {
            "search_intent": "Commercial investigation",
            "primary_positioning": "Practical compliance support for regulated teams.",
            "supporting_attributes": ["Plainspoken guidance"],
            "headline_direction": "Lead with practical support for regulated teams.",
            "recommended_angle": "Lead with practical compliance experience.",
            "claims_to_avoid": ["Do not promise guaranteed certification."],
            "proof_points_to_use": ["Page-level proof for metadata and FAQs"],
            "meta_direction": "Mention compliance and implementation support.",
            "faq_direction": "Answer fit, process, and proof questions.",
            "section_guidance": [
                {
                    "section": "intro",
                    "responsibility": "Frame the compliance problem.",
                    "guidance": "Lead with the compliance problem.",
                    "proof_points": ["ISO implementation experience"],
                },
                {
                    "section": "benefits",
                    "responsibility": "Explain the operational benefits.",
                    "guidance": "Connect the service to lower operational risk.",
                    "proof_points": ["Documented implementation process"],
                },
            ],
        }

        meta_capture = {}

        def fake_meta_provider(api_key, prompt, max_tokens=1500, model=None):
            meta_capture["prompt"] = prompt
            return json.dumps({
                "title": "Compliance Consulting",
                "description": "Practical compliance consulting for implementation teams.",
                "h1_optimised": "Compliance Consulting",
            })

        copy_gen.PROVIDER_FN["Test"] = fake_meta_provider
        copy_gen.generate_copy(
            provider="Test",
            api_key="key",
            url="https://example.com/service",
            keyword="compliance consulting",
            page_type="service",
            brand_name="Example",
            forbidden_phrases="",
            context="",
            business_type="b2b",
            h1="Compliance Consulting",
            strategy_brief=strategy_brief,
        )

        self.assertIn("STRATEGY BRIEF:", meta_capture["prompt"])
        self.assertIn("Do not promise guaranteed certification.", meta_capture["prompt"])
        self.assertIn("Practical compliance support for regulated teams.", meta_capture["prompt"])
        self.assertIn("Lead with practical support for regulated teams.", meta_capture["prompt"])
        self.assertIn("Page-level proof for metadata and FAQs", meta_capture["prompt"])
        self.assertIn("Mention compliance and implementation support.", meta_capture["prompt"])
        self.assertNotIn("Answer fit, process, and proof questions.", meta_capture["prompt"])
        self.assertNotIn("Lead with the compliance problem.", meta_capture["prompt"])
        self.assertIn(
            "Primary positioning, headline direction, and claims to avoid are contract requirements",
            meta_capture["prompt"],
        )
        self.assertIn("Do not turn search-query wording into an awkward H1", meta_capture["prompt"])
        self.assertIn("complete evidence allowlist for concrete brand claims", meta_capture["prompt"])

        faq_capture = {}

        def fake_faq_provider(api_key, prompt, max_tokens=1500, model=None):
            faq_capture["prompt"] = prompt
            return json.dumps([{
                "question": "What does the service include?",
                "answer": "It includes practical planning and implementation support.",
                "source": "generated",
            }])

        copy_gen.PROVIDER_FN["Test"] = fake_faq_provider
        copy_gen.generate_faq(
            provider="Test",
            api_key="key",
            keyword="compliance consulting",
            page_type="service",
            brand_name="Example",
            business_type="b2b",
            h1="Compliance Consulting",
            ai_overview_sections=[],
            ai_overview_raw="",
            paa_items=[],
            num_faqs=1,
            page_context="",
            strategy_brief=strategy_brief,
        )

        self.assertIn("STRATEGY BRIEF:", faq_capture["prompt"])
        self.assertIn("Do not promise guaranteed certification.", faq_capture["prompt"])
        self.assertIn("Practical compliance support for regulated teams.", faq_capture["prompt"])
        self.assertIn("Page-level proof for metadata and FAQs", faq_capture["prompt"])
        self.assertNotIn("Lead with practical support for regulated teams.", faq_capture["prompt"])
        self.assertIn("Answer fit, process, and proof questions.", faq_capture["prompt"])
        self.assertNotIn("Mention compliance and implementation support.", faq_capture["prompt"])
        self.assertNotIn("Lead with the compliance problem.", faq_capture["prompt"])
        self.assertIn("Do not turn search-query wording into FAQ questions", faq_capture["prompt"])
        self.assertIn("complete evidence allowlist for concrete brand claims", faq_capture["prompt"])

        page_capture = {}

        def fake_page_provider(api_key, prompt, max_tokens=1500, model=None):
            page_capture["prompt"] = prompt
            return "Generated section copy."

        copy_gen.PROVIDER_FN["Test"] = fake_page_provider
        copy_gen.generate_page(
            template={
                "sections": [
                    {
                        "name": "intro",
                        "label": "Introduction",
                        "purpose": "Introduce the service.",
                        "word_count": [20, 40],
                        "keyword_slot": "primary",
                        "heading_level": "none",
                        "prompt_rules": "Write directly.",
                    }
                ]
            },
            keyword_assignment={"intro": {"primary": "compliance consulting", "supporting": ""}},
            lsi_keywords={},
            business_type="b2b",
            brand_name="Example",
            h1="Compliance Consulting",
            page_type="service",
            paa_questions=[],
            ai_overview="",
            competitor_section_map={},
            client_brief="",
            client_existing_content="",
            provider="Test",
            api_key="key",
            strategy_brief=strategy_brief,
        )

        self.assertIn("STRATEGY BRIEF:", page_capture["prompt"])
        self.assertIn("Do not promise guaranteed certification.", page_capture["prompt"])
        self.assertIn("Frame the compliance problem.", page_capture["prompt"])
        self.assertIn("Lead with the compliance problem.", page_capture["prompt"])
        self.assertIn("ISO implementation experience", page_capture["prompt"])
        self.assertNotIn("Documented implementation process", page_capture["prompt"])
        self.assertNotIn("Page-level proof for metadata and FAQs", page_capture["prompt"])
        self.assertNotIn("Lead with practical support for regulated teams.", page_capture["prompt"])
        self.assertNotIn("Mention compliance and implementation support.", page_capture["prompt"])
        self.assertNotIn("Answer fit, process, and proof questions.", page_capture["prompt"])
        self.assertIn("Strategy brief priorities outrank exact keyword phrasing.", page_capture["prompt"])
        self.assertIn("Do not turn search-query wording into headings", page_capture["prompt"])
        self.assertIn("Treat proof points as a page-wide budget.", page_capture["prompt"])
        self.assertIn("complete evidence allowlist for concrete claims in this section", page_capture["prompt"])

        h1_strategy = copy_gen.format_strategy_brief_for_prompt(
            strategy_brief,
            output_type="page",
            section_names=["intro"],
            include_headline_direction=True,
        )
        self.assertIn("Lead with practical support for regulated teams.", h1_strategy)

    def test_strategy_prompt_does_not_blindly_truncate_late_common_fields(self):
        strategy_brief = {
            "claims_to_avoid": ["Do not invent claims."],
            "meta_direction": "Keep metadata natural.",
            "recommended_angle": "A" * 700,
            "brand_positioning": "B" * 700,
            "proof_points_to_use": ["P" * 300 for _ in range(6)],
            "page_goal": "G" * 700,
            "audience_need": "N" * 700,
            "search_intent": "I" * 700,
            "competitor_gaps": ["late-field-marker"],
        }

        prompt = copy_gen.format_strategy_brief_for_prompt(strategy_brief, output_type="meta")

        self.assertGreater(len(prompt), 1200)
        self.assertIn("Do not invent claims.", prompt)
        self.assertIn("Keep metadata natural.", prompt)
        self.assertIn("late-field-marker", prompt)
        self.assertLess(prompt.index("Output constraints"), prompt.index("Meta direction"))

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
            strategy_brief={
                "verified_facts": [{
                    "id": "F1",
                    "fact": "WDIV Detroit named the business Best Burger.",
                    "source": "current_page",
                }],
                "facts_to_avoid": ["An unverified review count."],
            },
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
        self.assertIn("WDIV Detroit named the business Best Burger.", captured["prompt"])
        self.assertIn("An unverified review count.", captured["prompt"])
        self.assertIn("must not reduce the score", captured["prompt"])

    def test_output_quality_review_uses_verified_evidence_and_ignores_keywords(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            captured["model"] = model
            return json.dumps({
                "issues": [{
                    "output": "page_copy",
                    "section": "benefits",
                    "code": "unsupported_claim",
                    "message": "The copy adds an unsupported guarantee.",
                    "claim": "Guaranteed results",
                }]
            })

        copy_gen.PROVIDER_FN["Test"] = fake_provider
        result = copy_gen.review_output_quality(
            provider="Test",
            api_key="key",
            model="editorial-review-model",
            strategy_brief={
                "primary_positioning": "Practical support backed by documented experience.",
                "verified_facts": [{
                    "id": "F1",
                    "fact": "The current page documents implementation support.",
                    "source": "current_page",
                }],
                "facts_to_avoid": ["Guaranteed results"],
            },
            outputs={
                "page_copy": {"benefits": "Guaranteed results for every client."},
            },
            owned_page_evidence="Our locations include Detroit, Ann Arbor, and Warren.",
            client_evidence="The client brief confirms delivery is available.",
        )

        self.assertEqual(result["issues"][0]["code"], "unsupported_claim")
        self.assertEqual(result["issues"][0]["section"], "benefits")
        self.assertEqual(captured["model"], "editorial-review-model")
        self.assertEqual(captured["max_tokens"], copy_gen.EDITORIAL_REVIEW_MAX_TOKENS)
        self.assertIn("The current page documents implementation support.", captured["prompt"])
        self.assertIn("Guaranteed results", captured["prompt"])
        self.assertIn("Our locations include Detroit, Ann Arbor, and Warren.", captured["prompt"])
        self.assertIn("The client brief confirms delivery is available.", captured["prompt"])
        self.assertIn("curated but not exhaustive", captured["prompt"])
        self.assertIn("Do not evaluate keyword selection, placement, or exact-match usage", captured["prompt"])

    def test_gemini_review_uses_json_mode_and_current_flash_model(self):
        with patch.object(
            copy_gen,
            "_call_gemini_json",
            return_value='{"issues": []}',
        ) as call_gemini_json:
            result = copy_gen.review_output_quality(
                provider="Gemini (free)",
                api_key="gemini-key",
                strategy_brief={"primary_positioning": "Clear local positioning."},
                outputs={"meta": {"title": "Example title"}},
            )

        self.assertEqual(result, {"issues": []})
        call_gemini_json.assert_called_once()
        self.assertEqual(call_gemini_json.call_args.kwargs["model"], "gemini-3.5-flash")

    def test_gemini_brand_review_uses_json_mode(self):
        with patch.object(
            copy_gen,
            "_call_gemini_json",
            return_value='{"score": 91, "reason": "Strong alignment."}',
        ) as call_gemini_json:
            result = copy_gen.score_brand_consistency(
                provider="Gemini (free)",
                api_key="gemini-key",
                brand_profile={"tone_of_voice": "direct"},
                outputs={"meta": "Direct copy."},
            )

        self.assertEqual(result["score"], 91)
        call_gemini_json.assert_called_once()
        self.assertEqual(call_gemini_json.call_args.kwargs["model"], "gemini-3.5-flash")

    def test_strategy_prompt_redacts_forbidden_fact_values(self):
        prompt = copy_gen.format_strategy_brief_for_prompt(
            {
                "primary_positioning": "An award-winning local burger destination.",
                "facts_to_avoid": [
                    "Franchise timelines beyond 60 days to confirm and 3-6 months to open.",
                    "4.8 rating with over 3,000 Google reviews.",
                ],
                "claims_to_avoid": ["Do not make halal the sole positioning."],
                "verified_facts": [{"id": "F1", "fact": "All meats are halal.", "source": "current_page"}],
            },
            output_type="faq",
        )

        self.assertNotIn("60 days", prompt)
        self.assertNotIn("3-6 months", prompt)
        self.assertNotIn("4.8", prompt)
        self.assertNotIn("3,000", prompt)
        self.assertIn("Do not state any franchise approval or opening timeline", prompt)
        self.assertIn("Do not state review counts or star ratings", prompt)
        self.assertIn("Do not make halal the sole positioning", prompt)

    def test_faq_plan_assigns_only_verified_fact_ids(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            return json.dumps([
                {"question": "Are all meats halal?", "fact_ids": ["F1"]},
                {"question": "Can customers order for pickup?", "fact_ids": ["F2"]},
            ])

        copy_gen.PROVIDER_FN["Test"] = fake_provider
        plan = copy_gen.generate_faq_plan(
            provider="Test",
            api_key="key",
            keyword="halal burgers",
            page_type="homepage",
            business_type="local",
            brand_name="Example",
            num_faqs=2,
            paa_items=[{"question": "Does the restaurant deliver?", "answer": "Competitor snippet"}],
            strategy_brief={
                "faq_direction": "Answer practical visitor questions.",
                "verified_facts": [
                    {"id": "F1", "fact": "All meats are halal.", "source": "current_page"},
                    {"id": "F2", "fact": "DoorDash pickup is available.", "source": "current_page"},
                ],
            },
        )

        self.assertEqual(plan[0]["fact_ids"], ["F1"])
        self.assertEqual(plan[1]["fact_ids"], ["F2"])
        self.assertIn("Does the restaurant deliver?", captured["prompt"])
        self.assertNotIn("Competitor snippet", captured["prompt"])

    def test_planned_faq_answers_exclude_raw_factual_context(self):
        prompt = copy_gen._build_faq_prompt(
            keyword="halal burgers",
            page_type="homepage",
            brand_name="Example",
            business_type="local",
            h1="Award-Winning Burgers",
            ai_overview_sections=[],
            ai_overview_raw="Research-only overview",
            paa_items=[{"question": "Does it deliver?", "answer": "Research-only answer"}],
            num_faqs=1,
            forbidden_phrases="",
            page_context="Unverified franchise timeline is 60 days.",
            brand_profile={"tone": "friendly", "key_messages": "Unverified claim"},
            strategy_brief={
                "faq_direction": "Confirm halal food.",
                "verified_facts": [{"id": "F1", "fact": "All meats are halal.", "source": "current_page"}],
            },
            faq_plan=[{"question": "Are all meats halal?", "fact_ids": ["F1"]}],
        )

        self.assertIn("Are all meats halal?", prompt)
        self.assertIn("F1", prompt)
        self.assertNotIn("60 days", prompt)
        self.assertNotIn("Unverified claim", prompt)
        self.assertNotIn("Research-only overview", prompt)
        self.assertNotIn("Research-only answer", prompt)

    def test_repetition_repair_matches_normalised_brand_punctuation(self):
        captured = {}

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            captured["prompt"] = prompt
            return json.dumps({"hero": "# Fresh Burgers\nAward-winning burgers made fresh."})

        copy_gen.PROVIDER_FN["Test"] = fake_provider
        repaired = copy_gen.repair_repeated_page_copy(
            section_results={"hero": "# Fresh Burgers\nTaystee's serves Taystee's favorites."},
            repeated_phrases=["taystee s"],
            template={"sections": [{"name": "hero", "purpose": "Introduce the brand.", "word_count": [5, 20], "heading_level": "h1"}]},
            strategy_brief={},
            brand_name="Taystee's",
            provider="Test",
            api_key="key",
        )

        self.assertIn("hero", captured["prompt"])
        self.assertEqual(repaired["hero"], "# Fresh Burgers\nAward-winning burgers made fresh.")

    def test_page_generation_passes_proof_and_cta_ledger_to_later_sections(self):
        prompts = []

        def fake_provider(api_key, prompt, max_tokens=1500, model=None):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "# Fresh Burgers\nTaystee's won a regional award. Stop by today."
            return "## Menu\nFresh options made to order."

        copy_gen.PROVIDER_FN["Test"] = fake_provider
        copy_gen.PROVIDER_DELAY["Test"] = 0
        copy_gen.generate_page(
            template={"sections": [
                {"name": "hero", "label": "Hero", "purpose": "Introduce the page.", "word_count": [5, 30], "keyword_slot": "none", "heading_level": "h1", "prompt_rules": "Lead clearly."},
                {"name": "menu", "label": "Menu", "purpose": "Describe the menu.", "word_count": [5, 30], "keyword_slot": "none", "heading_level": "h2", "prompt_rules": "Stay factual."},
            ]},
            keyword_assignment={},
            lsi_keywords={},
            business_type="local",
            brand_name="Taystee's",
            h1="Fresh Burgers",
            page_type="homepage",
            paa_questions=[],
            ai_overview="",
            competitor_section_map={},
            client_brief="",
            client_existing_content="",
            provider="Test",
            api_key="key",
            strategy_brief={"section_guidance": [
                {"section": "hero", "proof_points": ["Won a regional award."]},
                {"section": "menu", "proof_points": ["Food is made to order."]},
            ]},
        )

        self.assertEqual(len(prompts), 2)
        self.assertIn("Completed sections: hero", prompts[1])
        self.assertIn("Won a regional award.", prompts[1])
        self.assertIn("stop by", prompts[1])
        self.assertIn("Brand-name mentions already used: 1", prompts[1])

    def test_editorial_review_retries_malformed_json_once(self):
        with patch.object(
            copy_gen,
            "_call_gemini_json",
            side_effect=['{"issues":[', '{"issues": []}'],
        ) as call_gemini_json:
            result = copy_gen.review_output_quality(
                provider="Gemini (free)",
                api_key="gemini-key",
                strategy_brief={"primary_positioning": "Clear local positioning."},
                outputs={"meta": {"title": "Example title"}},
            )

        self.assertEqual(result, {"issues": []})
        self.assertEqual(call_gemini_json.call_count, 2)

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
