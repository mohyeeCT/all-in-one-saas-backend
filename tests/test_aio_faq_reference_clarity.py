import unittest

from routers import all_in_one
from utils.copy_gen import _build_faq_prompt


def _faq_prompt(page_type: str, page_context: str = "") -> str:
    return _build_faq_prompt(
        keyword="party cowboy hats",
        page_type=page_type,
        brand_name="Party Store",
        business_type="ecommerce",
        h1="Party Cowboy Hats",
        ai_overview_sections=[],
        ai_overview_raw="",
        paa_items=[],
        num_faqs=5,
        forbidden_phrases="",
        page_context=page_context,
    )


class AioFaqReferenceClarityTests(unittest.TestCase):
    def test_product_prompt_prefers_specific_nouns_over_vague_references(self):
        prompt = _faq_prompt("product")

        self.assertNotIn("Prefer natural generic references such as 'this product'", prompt)
        self.assertIn("Use no more than one vague standalone reference across the full FAQ set", prompt)
        self.assertIn("derived naturally from the page H1 or target keyword", prompt)

    def test_collection_prompt_limits_this_collection_across_the_faq_set(self):
        prompt = _faq_prompt("collection", "COLLECTION CONTEXT\nParty Cowboy Hats")

        self.assertIn("'this collection'", prompt)
        self.assertIn("Use no more than one vague standalone reference across the full FAQ set", prompt)
        self.assertIn("Do not repeatedly begin questions or answers", prompt)

    def test_qa_warns_when_vague_references_repeat(self):
        flags = []
        all_in_one._add_faq_quality_flags(
            flags,
            [
                {
                    "question": "How does this collection support a coordinated party theme?",
                    "answer": (
                        "This collection gives shoppers several style directions to compare while keeping "
                        "the party theme visually consistent across the chosen accessories."
                    ),
                },
                {
                    "question": "Which occasions work well for party cowboy hats?",
                    "answer": (
                        "These products can suit birthdays, themed events, and group celebrations when "
                        "shoppers want a recognizable western-inspired accessory."
                    ),
                },
            ],
        )

        self.assertIn("faq_vague_reference_repetition", {flag["code"] for flag in flags})

    def test_qa_allows_one_clear_vague_reference(self):
        flags = []
        all_in_one._add_faq_quality_flags(
            flags,
            [
                {
                    "question": "Which occasions work well for party cowboy hats?",
                    "answer": (
                        "This collection can suit birthdays, themed events, and group celebrations when "
                        "shoppers want a recognizable western-inspired accessory."
                    ),
                },
                {
                    "question": "What should shoppers compare before choosing a cowboy hat style?",
                    "answer": (
                        "Shoppers can compare material, shape, decorative details, intended audience, and "
                        "the overall party theme before selecting an option."
                    ),
                },
            ],
        )

        self.assertNotIn("faq_vague_reference_repetition", {flag["code"] for flag in flags})


if __name__ == "__main__":
    unittest.main()
