import unittest

from fastapi.testclient import TestClient

from main import ALLOWED_CORS_ORIGINS, app

_PRIVATE_EXCEPTION_SENTINEL = "private-provider-exception-4c532b"


def _raise_unhandled_test_error():
    raise RuntimeError(_PRIVATE_EXCEPTION_SENTINEL)


app.add_api_route(
    "/__tests__/unhandled-error",
    _raise_unhandled_test_error,
    methods=["GET"],
)


class PlatformPreviewCorsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _preflight(self, origin):
        return self.client.options(
            "/api/jobs",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    def test_allows_only_exact_approved_origins(self):
        allowed_origins = (
            "https://copypilot.app",
            "https://all-in-one.copypilot.app",
            "https://copypilot-platform-mohyeects-projects.vercel.app",
        )
        self.assertEqual(ALLOWED_CORS_ORIGINS, allowed_origins)

        for origin in allowed_origins:
            with self.subTest(origin=origin):
                response = self._preflight(origin)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["access-control-allow-origin"],
                    origin,
                )

    def test_blocks_unapproved_origins(self):
        blocked_origins = (
            (
                "https://copypilot-platform-git-chore-platform-47fef0-"
                "mohyeects-projects.vercel.app"
            ),
            "https://unrelated-project-mohyeects-projects.vercel.app",
            "https://copypilot.app.attacker.example",
            "https://all-in-one.copypilot.app.attacker.example",
        )

        for origin in blocked_origins:
            with self.subTest(origin=origin):
                response = self._preflight(origin)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(
                    "access-control-allow-origin",
                    response.headers,
                )

    def test_unhandled_error_is_safe_for_approved_origin(self):
        origin = "https://copypilot.app"

        with self.assertLogs("main", level="ERROR") as logs:
            response = self.client.get(
                "/__tests__/unhandled-error",
                headers={"Origin": origin},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error."})
        self.assertNotIn(_PRIVATE_EXCEPTION_SENTINEL, response.text)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            origin,
        )
        self.assertEqual(
            response.headers["access-control-allow-credentials"],
            "true",
        )
        self.assertIn("Origin", response.headers["vary"])
        server_logs = "\n".join(logs.output)
        self.assertIn("aio.http.unhandled", server_logs)
        self.assertIn("exception_type=RuntimeError", server_logs)
        self.assertNotIn(_PRIVATE_EXCEPTION_SENTINEL, server_logs)

    def test_unhandled_error_does_not_reflect_hostile_origin(self):
        response = self.client.get(
            "/__tests__/unhandled-error",
            headers={"Origin": "https://copypilot.app.attacker.example"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error."})
        self.assertNotIn(_PRIVATE_EXCEPTION_SENTINEL, response.text)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_unhandled_error_without_origin_has_no_cors_headers(self):
        response = self.client.get("/__tests__/unhandled-error")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error."})
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertNotIn("access-control-allow-credentials", response.headers)


if __name__ == "__main__":
    unittest.main()
