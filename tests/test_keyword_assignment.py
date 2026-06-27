import unittest

from utils.keyword import assign_keywords_to_sections, merge_keyword_pools, rank_keywords


class KeywordAssignmentTests(unittest.TestCase):
    def test_non_gsc_keywords_do_not_use_volume_as_fake_impressions(self):
        pool = merge_keyword_pools(
            gsc_rows=[],
            dfs_ranked=[{"keyword": "service keyword", "volume": 900, "difficulty": 30}],
            manual_seeds=["manual keyword"],
            dfs_volume_map={"manual keyword": 700},
            dfs_difficulty_map={"manual keyword": 20},
        )

        by_keyword = {item["keyword"]: item for item in pool}

        self.assertEqual(by_keyword["service keyword"]["impressions"], 1)
        self.assertEqual(by_keyword["manual keyword"]["impressions"], 1)

    def test_keyword_assignment_reuses_topic_anchors_when_pool_is_exhausted(self):
        ranked = rank_keywords(
            [
                {"keyword": "primary service", "volume": 100, "difficulty": 10, "impressions": 1, "ctr": 0, "position": 20},
                {"keyword": "supporting service", "volume": 90, "difficulty": 10, "impressions": 1, "ctr": 0, "position": 20},
            ],
            brand_terms=[],
            h1="Primary Service",
        )

        assignment = assign_keywords_to_sections(ranked, ["hero", "benefits", "faq", "cta"])

        self.assertEqual(assignment["hero"]["primary"], "primary service")
        self.assertEqual(assignment["benefits"]["supporting"], "supporting service")
        self.assertEqual(assignment["faq"]["supporting"], "supporting service")
        self.assertEqual(assignment["cta"]["supporting"], "")

    def test_keyword_assignment_respects_template_slots(self):
        ranked = rank_keywords(
            [
                {"keyword": "primary service", "volume": 100, "difficulty": 10, "impressions": 1, "ctr": 0, "position": 20},
                {"keyword": "supporting service", "volume": 90, "difficulty": 10, "impressions": 1, "ctr": 0, "position": 20},
            ],
            brand_terms=[],
            h1="Primary Service",
        )

        assignment = assign_keywords_to_sections(
            ranked,
            [
                {"name": "intro", "keyword_slot": "primary"},
                {"name": "details", "keyword_slot": "supporting"},
                {"name": "faq", "keyword_slot": "lsi"},
                {"name": "summary", "keyword_slot": "primary"},
                {"name": "cta", "keyword_slot": "none"},
            ],
        )

        self.assertEqual(assignment["intro"]["primary"], "primary service")
        self.assertEqual(assignment["details"]["supporting"], "supporting service")
        self.assertEqual(assignment["faq"]["supporting"], "supporting service")
        self.assertEqual(assignment["summary"]["primary"], "primary service")
        self.assertEqual(assignment["cta"]["primary"], "")
        self.assertEqual(assignment["cta"]["supporting"], "")


if __name__ == "__main__":
    unittest.main()
