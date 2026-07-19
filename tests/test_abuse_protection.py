import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from abuse_protection import (
    RATE_LIMIT_UNAVAILABLE_DETAIL,
    enforce_job_start,
    enforce_rate_limit,
    execute_active_job_write,
)
from routers import all_in_one

_PRIVATE_RPC_SENTINEL = "private-rate-limit-rpc-8e1f7c"


class FakeQuery:
    def __init__(self, rows): self.rows = rows
    def select(self, *_args): return self
    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]; return self
    def in_(self, field, values):
        self.rows = [row for row in self.rows if row.get(field) in values]; return self
    def neq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) != value]; return self
    def limit(self, count): self.rows = self.rows[:count]; return self
    def execute(self): return type("Result", (), {"data": self.rows})()


class FakeSupabase:
    def __init__(self, rows=None): self.rows = rows or []
    def table(self, name): return FakeQuery(list(self.rows))


class AbuseProtectionTests(unittest.TestCase):
    def test_limits_and_concurrency(self):
        enforce_job_start(FakeSupabase(), "user-1", "all-in-one", 50, 50)
        with self.assertRaisesRegex(HTTPException, "maximum is 50"):
            enforce_job_start(FakeSupabase(), "user-1", "all-in-one", 51, 50)
        active = FakeSupabase([{"id": "job-1", "user_id": "user-1", "tool": "all-in-one", "status": "running"}])
        with self.assertRaisesRegex(HTTPException, "already active"):
            enforce_job_start(active, "user-1", "all-in-one", 1, 50)
        enforce_job_start(active, "user-1", "all-in-one", 1, 50, exclude_job_id="job-1")
        with self.assertRaisesRegex(HTTPException, "already active"):
            execute_active_job_write(lambda: (_ for _ in ()).throw(RuntimeError("jobs_one_active_per_user_tool_idx")), "all-in-one")

    def test_rate_limit_contract(self):
        class Rpc:
            def __init__(self, data=None, error=None): self.data, self.error, self.params = data, error, None
            def rpc(self, name, params): self.params = params; return self
            def execute(self):
                if self.error: raise self.error
                return type("Result", (), {"data": self.data})()
        allowed = Rpc([{"allowed": True, "retry_after_seconds": 0}])
        enforce_rate_limit(allowed, "user-1", "all-in-one", "job-create", 10)
        self.assertEqual(allowed.params["p_window_seconds"], 600)
        with self.assertRaises(HTTPException) as raised:
            enforce_rate_limit(Rpc([{"allowed": False, "retry_after_seconds": 181}]), "user-1", "all-in-one", "section-rerun", 30)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers["Retry-After"], "181")
        self.assertIn("Please wait 4 minutes", raised.exception.detail)

    def test_rate_limit_rpc_outage_fails_closed_without_leaking_details(self):
        class Rpc:
            def rpc(self, _name, _params): return self
            def execute(self): raise RuntimeError(_PRIVATE_RPC_SENTINEL)

        with self.assertLogs("abuse_protection", level="ERROR") as logs:
            with self.assertRaises(HTTPException) as raised:
                enforce_rate_limit(
                    Rpc(),
                    "user-1",
                    "all-in-one",
                    "job-create",
                    10,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, RATE_LIMIT_UNAVAILABLE_DETAIL)
        self.assertNotIn(_PRIVATE_RPC_SENTINEL, raised.exception.detail)
        server_logs = "\n".join(logs.output)
        self.assertIn("aio.rate_limit.unavailable", server_logs)
        self.assertIn("exception_type=RuntimeError", server_logs)
        self.assertNotIn(_PRIVATE_RPC_SENTINEL, server_logs)

    def test_unverifiable_rate_limit_contract_fails_closed(self):
        class Rpc:
            def __init__(self, data): self.data = data
            def rpc(self, _name, _params): return self
            def execute(self): return type("Result", (), {"data": self.data})()

        invalid_responses = (
            None,
            [],
            {},
            {"retry_after_seconds": 10},
            {"allowed": None},
            {"allowed": "true"},
            [
                {"allowed": True, "retry_after_seconds": 0},
                {"allowed": False, "retry_after_seconds": 10},
            ],
            [{"allowed": False}],
            [{"allowed": False, "retry_after_seconds": True}],
            [{"allowed": False, "retry_after_seconds": 0}],
            [{"allowed": False, "retry_after_seconds": "unknown"}],
        )

        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(HTTPException) as raised:
                    enforce_rate_limit(
                        Rpc(response),
                        "user-1",
                        "all-in-one",
                        "job-create",
                        10,
                    )
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(
                    raised.exception.detail,
                    RATE_LIMIT_UNAVAILABLE_DETAIL,
                )

        class MissingData:
            def rpc(self, _name, _params): return self
            def execute(self): return object()

        with self.assertRaises(HTTPException) as raised:
            enforce_rate_limit(
                MissingData(),
                "user-1",
                "all-in-one",
                "job-create",
                10,
            )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            RATE_LIMIT_UNAVAILABLE_DETAIL,
        )

    def test_new_job_rpc_outage_has_no_insert_or_background_side_effect(self):
        class RpcOutage:
            def __init__(self):
                self.table_calls = []

            def rpc(self, _name, _params):
                return self

            def execute(self):
                raise RuntimeError(_PRIVATE_RPC_SENTINEL)

            def table(self, name):
                self.table_calls.append(name)
                raise AssertionError("Job persistence must not run")

        class BackgroundTasks:
            def __init__(self):
                self.calls = []

            def add_task(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        sb = RpcOutage()
        background = BackgroundTasks()
        request = all_in_one.AIOJobRequest(
            rows=[
                all_in_one.AIORow(
                    url="https://example.com/page",
                    keyword="example",
                )
            ],
            settings=all_in_one.AIOSettings(),
        )

        with patch.object(all_in_one, "enforce_job_start"):
            with self.assertRaises(HTTPException) as raised:
                all_in_one.run_aio_job(
                    request,
                    background,
                    user=SimpleNamespace(id="user-1"),
                    sb=sb,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, RATE_LIMIT_UNAVAILABLE_DETAIL)
        self.assertEqual(sb.table_calls, [])
        self.assertEqual(background.calls, [])
