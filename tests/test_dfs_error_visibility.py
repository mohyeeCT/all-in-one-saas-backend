import unittest
from unittest.mock import Mock, patch

from utils import dfs


class DataForSeoErrorVisibilityTests(unittest.TestCase):
    @patch("utils.dfs.requests.post")
    def test_post_raises_task_level_api_error(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status_code": 20000,
            "status_message": "Ok.",
            "tasks": [{
                "status_code": 40100,
                "status_message": "Authentication failed",
                "result": None,
            }],
        }
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "DFS error 40100: Authentication failed"):
            dfs._post("keywords_data/google_ads/search_volume/live", [], "login", "password")

    @patch("utils.dfs._post", side_effect=RuntimeError("DFS error 40100: Authentication failed"))
    def test_optional_dfs_helpers_do_not_swallow_errors(self, _post):
        calls = (
            lambda: dfs.get_ranked_keywords_for_url("https://example.com/widgets", "login", "password"),
            lambda: dfs.get_keyword_ideas("widgets", "login", "password"),
            lambda: dfs.get_serp_content("widgets", "login", "password"),
        )

        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(RuntimeError, "Authentication failed"):
                    call()


if __name__ == "__main__":
    unittest.main()
