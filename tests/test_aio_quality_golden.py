import json
import unittest
from pathlib import Path

from routers import all_in_one


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aio_quality_golden.txt"


class AioQualityGoldenSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_golden_outputs_keep_expected_quality_classification(self):
        self.assertGreaterEqual(len(self.cases), 8)
        for case in self.cases:
            with self.subTest(case=case["name"]):
                flags = all_in_one._collect_qa_flags(
                    gen_meta=True,
                    gen_faqs=True,
                    gen_page_copy=True,
                    generated_title=case["generated_title"],
                    generated_description=case["generated_description"],
                    optimised_h1=case["optimised_h1"],
                    input_h1=case["input_h1"],
                    primary_keyword=case["primary_keyword"],
                    faq_items=case["faq_items"],
                    section_results=case["section_results"],
                    forbidden_phrases=case["forbidden_phrases"],
                    brand_name=case["brand_name"],
                    business_type=case["business_type"],
                )
                codes = {flag["code"] for flag in flags}
                self.assertTrue(set(case["expected_codes"]).issubset(codes), codes)
                self.assertFalse(set(case["forbidden_codes"]).intersection(codes), codes)
                self.assertEqual(all_in_one._qa_status(flags), case["expected_status"], flags)

    def test_strategy_quality_problem_is_visible_and_review_blocking(self):
        flags = []
        all_in_one._add_strategy_qa_flag(
            flags,
            "needs_review",
            ["Headline direction is missing."],
        )

        self.assertEqual(all_in_one._qa_status(flags), "review")
        self.assertEqual(flags[0]["code"], "strategy_brief_needs_review")
        self.assertEqual(flags[0]["details"], ["Headline direction is missing."])


if __name__ == "__main__":
    unittest.main()
