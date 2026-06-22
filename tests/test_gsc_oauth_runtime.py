import os
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

from google.auth.exceptions import RefreshError
from fastapi import HTTPException

from credentials import (
    hydrate_job_settings,
    load_active_gsc_credentials,
    load_user_credentials,
    mark_gsc_reconnect_required,
)
from routers.all_in_one import AIOJobRequest, AIORow, AIOSettings
from routers import all_in_one, jobs
from utils import gsc

meta = all_in_one


SERVICE_ACCOUNT = {
    "method": "service_account",
    "service_account": {"client_email": "runtime@example.com", "private_key": "runtime-private-key"},
}
OAUTH = {"method": "google_oauth", "refresh_token_ciphertext": "v1:runtime-ciphertext"}
RECONNECT_ERROR = "Google Search Console reconnect required."
UNAVAILABLE_ERROR = "Selected Google Search Console connection unavailable."
CREDENTIALS_ERROR = "Saved credentials are temporarily unavailable."
SECRETS = ("runtime-api-secret", "runtime-dfs-secret", "v1:runtime-ciphertext", "runtime-private-key")


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.filters = []
        self.in_filters = []
        self.operation = "select"
        self.payload = None
        self.columns = None

    def select(self, columns):
        self.columns = columns
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.in_filters.append((column, tuple(values)))
        return self

    def execute(self):
        self.sb.executed.append(self)
        source = self.sb.tables.get(self.table, [])
        if isinstance(source, Exception):
            raise source
        if self.operation == "insert":
            return _Response([{"id": "job-new", **self.payload}])
        rows = [
            row for row in source
            if all(row.get(key) == value for key, value in self.filters)
            and all(row.get(key) in values for key, values in self.in_filters)
        ]
        if self.operation == "update":
            for row in rows:
                row.update(self.payload)
        return _Response(rows)


class _Supabase:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.executed = []

    def table(self, name):
        return _Query(self, name)


class _BackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))


class _DatabaseError(Exception):
    def __init__(self, code):
        self.code = code


def _tables(method="service_account", oauth_status="connected"):
    return {
        "user_settings": [{
            "user_id": "user-1",
            "gsc_auth_method": method,
            "provider_settings": {"api_key": "runtime-api-secret", "dfs_password": "runtime-dfs-secret"},
        }],
        "user_credentials": [{
            "user_id": "user-1",
            "provider_settings": {},
            "gsc_service_account": SERVICE_ACCOUNT["service_account"],
        }],
        "gsc_oauth_connections": [{
            "user_id": "user-1",
            "status": oauth_status,
            "refresh_token_ciphertext": OAUTH["refresh_token_ciphertext"],
        }],
        "jobs": [],
    }


def _runtime_settings(envelope=OAUTH):
    return {
        "provider": "Claude",
        "api_key": "runtime-api-secret",
        "dfs_password": "runtime-dfs-secret",
        "use_gsc": True,
        "site_url": "sc-domain:example.com",
        "_gsc_credentials": envelope,
    }


def _stored_job(error=None):
    row = {
        "id": "job-1",
        "user_id": "user-1",
        "settings": {"provider": "Claude", "use_gsc": True, "site_url": "sc-domain:example.com"},
        "rows": [{"url": "https://example.com/page", "keyword": "manual"}],
        "results": [{}],
    }
    if error is not None:
        row["error"] = error
    return row


def _assert_persistence_is_secret_free(test_case, sb):
    payloads = repr([query.payload for query in sb.executed if query.payload is not None])
    for secret in SECRETS:
        test_case.assertNotIn(secret, payloads)
    test_case.assertNotIn("_gsc_credentials", payloads)
    test_case.assertNotIn("_gsc_service_account", payloads)


class CredentialSelectionTests(unittest.TestCase):
    def test_get_and_duplicate_strip_legacy_secrets_without_mutating_source(self):
        legacy_settings = {
            "provider": "Claude", "api_key": "legacy-api", "dfs_password": "legacy-dfs",
            "jina_api_key": "legacy-jina", "gsc_service_account": {"private_key": "legacy-gsc"},
            "_gsc_credentials": {"refresh_token_ciphertext": "legacy-oauth"},
            "_gsc_service_account": {"private_key": "legacy-runtime-gsc"},
        }
        source = {**_stored_job(), "name": "Legacy", "settings": legacy_settings}
        sb = _Supabase({"jobs": [source]})
        with patch.object(jobs, "get_supabase", return_value=sb):
            response = jobs.get_job("job-1", user=SimpleNamespace(id="user-1"))
        with patch.object(jobs, "enforce_rate_limit"):
            jobs.duplicate_job("job-1", user=SimpleNamespace(id="user-1"), sb=sb)
        self.assertEqual(response["settings"], {"provider": "Claude"})
        insert = [query for query in sb.executed if query.operation == "insert"][-1]
        self.assertEqual(insert.payload["settings"], {"provider": "Claude"})
        self.assertEqual(source["settings"], legacy_settings)

    def test_service_account_selection_never_queries_oauth_connections(self):
        sb = _Supabase(_tables("service_account"))

        self.assertEqual(load_active_gsc_credentials(sb, "user-1"), SERVICE_ACCOUNT)

        self.assertFalse(any(query.table == "gsc_oauth_connections" for query in sb.executed))

    def test_oauth_selection_only_reads_method_row_before_oauth_connection(self):
        sb = _Supabase(_tables("google_oauth"))

        self.assertEqual(load_active_gsc_credentials(sb, "user-1"), OAUTH)

        self.assertEqual(
            [(query.table, query.columns) for query in sb.executed],
            [
                ("user_settings", "gsc_auth_method"),
                ("gsc_oauth_connections", "refresh_token_ciphertext,status"),
            ],
        )

    def test_selector_supports_both_authoritative_modes(self):
        for method, expected in (("service_account", SERVICE_ACCOUNT), ("google_oauth", OAUTH)):
            with self.subTest(method=method):
                self.assertEqual(load_active_gsc_credentials(_Supabase(_tables(method)), "user-1"), expected)

    def test_selector_missing_invalid_and_inactive_never_falls_back(self):
        cases = [
            ("google_oauth", "reconnect_required"),
            ("invalid_method", "connected"),
        ]
        for method, status in cases:
            with self.subTest(method=method, status=status):
                self.assertIsNone(load_active_gsc_credentials(_Supabase(_tables(method, status)), "user-1"))

        tables = _tables("service_account")
        tables["user_credentials"][0]["gsc_service_account"] = None
        self.assertIsNone(load_active_gsc_credentials(_Supabase(tables), "user-1"))

    def test_only_recognized_server_credential_migration_errors_are_ignored(self):
        for code in ("PGRST204", "PGRST205", "42P01", "42703"):
            tables = _tables()
            tables["user_credentials"] = _DatabaseError(code)
            self.assertEqual(load_user_credentials(_Supabase(tables), "user-1")["provider_settings"]["api_key"], "runtime-api-secret")

        tables = _tables()
        tables["user_credentials"] = _DatabaseError("50000")
        with self.assertRaises(_DatabaseError):
            load_user_credentials(_Supabase(tables), "user-1")

    def test_hydration_strips_all_incoming_secrets_then_uses_server_selection(self):
        incoming = {
            "provider": "Claude",
            "api_key": "attacker-api",
            "dfs_password": "attacker-dfs",
            "jina_api_key": "attacker-jina",
            "_gsc_service_account": {"private_key": "attacker-key"},
            "_gsc_credentials": {"method": "google_oauth", "refresh_token_ciphertext": "attacker-token"},
        }
        hydrated = hydrate_job_settings(_Supabase(_tables("service_account")), "user-1", incoming)
        self.assertEqual(hydrated["_gsc_credentials"], SERVICE_ACCOUNT)
        self.assertEqual(hydrated["api_key"], "runtime-api-secret")
        self.assertNotIn("_gsc_service_account", hydrated)
        self.assertNotIn("attacker", repr(hydrated))

    def test_reconnect_marker_is_tenant_status_and_ciphertext_stale_safe(self):
        tables = _tables("google_oauth")
        sb = _Supabase(tables)
        self.assertTrue(mark_gsc_reconnect_required(sb, "user-1", OAUTH["refresh_token_ciphertext"]))
        query = sb.executed[-1]
        self.assertEqual(query.filters, [
            ("user_id", "user-1"),
            ("status", "connected"),
            ("refresh_token_ciphertext", OAUTH["refresh_token_ciphertext"]),
        ])
        self.assertEqual(query.payload["last_error_code"], "refresh_failed")

        for user_id, ciphertext in (("other-user", OAUTH["refresh_token_ciphertext"]), ("user-1", "v1:stale")):
            self.assertFalse(mark_gsc_reconnect_required(_Supabase(_tables("google_oauth")), user_id, ciphertext))


class GscClientTests(unittest.TestCase):
    def test_scope_and_service_account_alias_are_exact(self):
        self.assertEqual(gsc.GSC_SCOPES, ["https://www.googleapis.com/auth/webmasters.readonly"])
        with patch.object(gsc, "ServiceAccountCredentials", create=True) as credentials, patch.object(gsc, "build") as build:
            gsc.get_gsc_client(SERVICE_ACCOUNT)
        credentials.from_service_account_info.assert_called_once_with(
            SERVICE_ACCOUNT["service_account"], scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        build.assert_called_once_with("searchconsole", "v1", credentials=credentials.from_service_account_info.return_value)

    def test_oauth_reads_env_before_decrypt_then_refreshes_before_build(self):
        order = Mock()
        credentials = Mock()
        request = Mock()
        with (
            patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_ID": "client-id", "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret"}, clear=True),
            patch.object(gsc, "decrypt_secret", return_value="refresh-token", create=True) as decrypt,
            patch.object(gsc, "OAuthCredentials", return_value=credentials, create=True) as oauth_credentials,
            patch.object(gsc, "Request", return_value=request, create=True),
            patch.object(gsc, "build") as build,
        ):
            order.attach_mock(decrypt, "decrypt")
            order.attach_mock(oauth_credentials, "credentials")
            order.attach_mock(credentials.refresh, "refresh")
            order.attach_mock(build, "build")
            gsc.get_gsc_client({**OAUTH, "client_id": "ignored", "client_secret": "ignored"})
        self.assertEqual(order.mock_calls, [
            call.decrypt(OAUTH["refresh_token_ciphertext"]),
            call.credentials(token=None, refresh_token="refresh-token", token_uri=gsc.TOKEN_URI, client_id="client-id", client_secret="client-secret", scopes=gsc.GSC_SCOPES),
            call.refresh(request),
            call.build("searchconsole", "v1", credentials=credentials),
        ])

    def test_missing_env_precedes_decrypt_and_invalid_envelopes_are_safe(self):
        with patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_SECRET": "secret"}, clear=True), patch.object(gsc, "decrypt_secret", create=True) as decrypt:
            with self.assertRaisesRegex(KeyError, "GOOGLE_OAUTH_CLIENT_ID"):
                gsc.get_gsc_client(OAUTH)
            decrypt.assert_not_called()

        for envelope in (None, "private", {}, {"method": "service_account"}, {"method": "google_oauth"}):
            with self.subTest(envelope=envelope), self.assertRaisesRegex(ValueError, "^Invalid GSC credentials$"):
                gsc.get_gsc_client(envelope)


class RuntimePathTests(unittest.TestCase):
    def test_initial_hydration_database_failure_returns_fixed_safe_503(self):
        private_detail = "postgres-password-and-host-private-detail"
        request = AIOJobRequest(
            name="Runtime",
            rows=[AIORow(url="https://example.com/page")],
            settings=AIOSettings(),
        )

        with (
            patch.object(meta, "enforce_job_start"),
            patch.object(meta, "enforce_rate_limit"),
            patch.object(meta, "hydrate_job_settings", side_effect=RuntimeError(private_detail)),
        ):
            with self.assertRaises(HTTPException) as raised:
                meta.run_aio_job(
                    request,
                    _BackgroundTasks(),
                    user=SimpleNamespace(id="user-1"),
                    sb=_Supabase(),
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, CREDENTIALS_ERROR)
        self.assertNotIn(private_detail, repr(raised.exception))

    def test_core_job_helpers_require_tenant_and_wrong_tenant_cannot_read_or_mutate(self):
        for function in (meta._is_cancelled, meta._update_job, meta._process_single_row):
            with self.subTest(function=function.__name__):
                self.assertIn("user_id", inspect.signature(function).parameters)

        job = {**_stored_job(), "status": "cancelling", "logs": []}
        sb = _Supabase({"jobs": [job]})
        self.assertFalse(meta._is_cancelled(sb, "job-1", "other-user"))
        meta._update_job(sb, "job-1", "other-user", {"status": "complete", "current_step": "private"})
        self.assertEqual(job["status"], "cancelling")
        self.assertEqual(job["logs"], [])
        for query in sb.executed:
            if query.table == "jobs":
                self.assertIn(("user_id", "other-user"), query.filters)

    def test_process_job_threads_tenant_through_all_core_helpers(self):
        with (
            patch.object(meta, "get_supabase", return_value=Mock()),
            patch.object(meta, "_update_job") as update,
            patch.object(meta, "_is_cancelled", return_value=False) as cancelled,
            patch.object(meta, "_process_single_row", return_value={"status": "ok"}) as process,
        ):
            meta._process_job(
                "job-1",
                [{"url": "https://example.com/page"}],
                {**_runtime_settings(), "use_gsc": False},
                None,
                user_id="user-1",
            )

        self.assertTrue(update.call_args_list)
        self.assertTrue(all(call.args[2] == "user-1" for call in update.call_args_list))
        self.assertTrue(all(call.args[2] == "user-1" for call in cancelled.call_args_list))
        self.assertEqual(process.call_args.kwargs["user_id"], "user-1")

    def test_rerun_kickoff_writes_are_tenant_scoped(self):
        cases = (
            (
                jobs.rerun_row,
                (_stored_job(),),
                {"job_id": "job-1", "row_index": 0, "body": jobs.RerunRequest(), "background_tasks": _BackgroundTasks()},
            ),
            (
                jobs.rerun_rows,
                (_stored_job(),),
                {"job_id": "job-1", "body": jobs.MultiRerunRequest(row_indices=[0]), "background_tasks": _BackgroundTasks()},
            ),
            (
                jobs.rerun_section,
                ({
                    **_stored_job(),
                    "results": [{"section_results": {"hero": "Existing"}}],
                },),
                {
                    "job_id": "job-1",
                    "body": jobs.RerunSectionRequest(row_index=0, section_name="hero"),
                    "background_tasks": _BackgroundTasks(),
                },
            ),
        )
        for function, rows, kwargs in cases:
            with self.subTest(function=function.__name__):
                sb = _Supabase({"jobs": list(rows)})
                with (
                    patch.object(jobs, "enforce_job_start"),
                    patch.object(jobs, "enforce_rate_limit"),
                    patch.object(jobs, "execute_active_job_write", side_effect=lambda write, _tool: write()),
                ):
                    function(**kwargs, user=SimpleNamespace(id="user-1"), sb=sb)
                kickoff = [query for query in sb.executed if query.operation == "update"][-1]
                self.assertEqual(kickoff.filters, [("id", "job-1"), ("user_id", "user-1")])

    def test_successful_single_and_bulk_retry_clear_only_credential_error(self):
        cases = (
            (jobs._rerun_single_row, None),
            (jobs._rerun_multiple_rows, [0]),
        )
        for function, indices in cases:
            for existing_error, expected_error in (
                ("Saved credentials are temporarily unavailable.", None),
                ("Unrelated job failure", "Unrelated job failure"),
                (RECONNECT_ERROR, RECONNECT_ERROR),
            ):
                with self.subTest(function=function.__name__, existing_error=existing_error):
                    sb = _Supabase({"jobs": [_stored_job(existing_error)]})
                    settings = {**_runtime_settings(), "use_gsc": False}
                    with (
                        patch.object(jobs, "hydrate_job_settings", return_value=settings),
                        patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
                        patch.object(meta, "_update_job"),
                    ):
                        if indices is None:
                            function(
                                "job-1",
                                0,
                                _stored_job()["rows"],
                                _stored_job()["settings"],
                                sb,
                                user_id="user-1",
                            )
                        else:
                            function(
                                "job-1",
                                indices,
                                _stored_job()["rows"],
                                _stored_job()["settings"],
                                sb,
                                "user-1",
                            )

                    self.assertEqual(sb.tables["jobs"][0].get("error"), expected_error)
                    clear_queries = [
                        query for query in sb.executed
                        if query.operation == "update" and query.payload == {"error": None}
                    ]
                    self.assertEqual(len(clear_queries), 1)
                    self.assertEqual(clear_queries[0].filters, [
                        ("id", "job-1"),
                        ("user_id", "user-1"),
                    ])
                    self.assertEqual(clear_queries[0].in_filters, [(
                        "error",
                        ("Saved credentials are temporarily unavailable.",),
                    )])

    def test_single_rerun_hydration_failure_persists_safe_tenant_scoped_failure(self):
        private_detail = "database-password-private-detail"
        sb = _Supabase({"jobs": [{**_stored_job(), "status": "complete"}]})

        with (
            patch.object(jobs, "hydrate_job_settings", side_effect=RuntimeError(private_detail)),
            patch.object(meta, "_process_single_row") as process,
        ):
            jobs._rerun_single_row(
                "job-1",
                0,
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                user_id="user-1",
            )

        process.assert_not_called()
        update = [query for query in sb.executed if query.operation == "update"][-1]
        self.assertEqual(update.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(update.payload, {
            "error": "Saved credentials are temporarily unavailable.",
            "current_step": "Row 1 re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        self.assertNotIn("rerunning", update.payload["current_step"].lower())
        self.assertNotIn(private_detail, repr(update.payload))

    def test_bulk_rerun_hydration_failure_sets_terminal_safe_tenant_scoped_failure(self):
        private_detail = "database-token-private-detail"
        sb = _Supabase({"jobs": [{**_stored_job(), "status": "running"}]})

        with (
            patch.object(jobs, "hydrate_job_settings", side_effect=RuntimeError(private_detail)),
            patch.object(meta, "_process_single_row") as process,
        ):
            jobs._rerun_multiple_rows(
                "job-1",
                [0],
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                "user-1",
            )

        process.assert_not_called()
        update = [query for query in sb.executed if query.operation == "update"][-1]
        self.assertEqual(update.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(update.payload, {
            "status": "failed",
            "error": "Saved credentials are temporarily unavailable.",
            "current_step": "Re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        self.assertNotIn(private_detail, repr(update.payload))

    def test_initial_path_uses_exact_envelope_and_never_persists_secrets(self):
        sb = _Supabase(_tables("google_oauth"))
        background = _BackgroundTasks()
        request = AIOJobRequest(name="Runtime", rows=[AIORow(url="https://example.com/page")], settings=AIOSettings(use_gsc=True))
        with (
            patch.object(meta, "get_supabase", return_value=sb),
            patch.object(meta, "enforce_job_start"),
            patch.object(meta, "enforce_rate_limit"),
            patch.object(meta, "execute_active_job_write", side_effect=lambda write, _tool: write()),
            patch.object(meta, "hydrate_job_settings", return_value=_runtime_settings()),
        ):
            all_in_one.run_aio_job(request, background, user=SimpleNamespace(id="user-1"), sb=sb)
        function, args, kwargs = background.calls[0]
        self.assertIs(function, all_in_one._process_job)
        self.assertEqual(args, ())
        self.assertEqual(kwargs["gsc_credentials"], OAUTH)
        self.assertEqual(kwargs["user_id"], "user-1")
        self.assertNotIn("sa_info", kwargs)
        _assert_persistence_is_secret_free(self, sb)

    def test_initial_processing_supports_both_modes_and_fixed_errors(self):
        for envelope in (SERVICE_ACCOUNT, OAUTH):
            with (
                self.subTest(method=envelope["method"]),
                patch.object(meta, "get_supabase", return_value=Mock()),
                patch.object(meta, "get_gsc_client", return_value="client") as get_client,
                patch.object(meta, "_process_single_row", return_value={"status": "ok"}) as process,
                patch.object(meta, "_update_job"),
                patch.object(meta, "_is_cancelled", return_value=False),
            ):
                all_in_one._process_job("job-1", [{"url": "https://example.com/page"}], _runtime_settings(envelope), envelope, user_id="user-1")
                get_client.assert_called_once_with(envelope)
                self.assertEqual(process.call_args.kwargs["gsc_client"], "client")

        for failure, expected in ((RefreshError("provider detail"), RECONNECT_ERROR), (RuntimeError("provider detail"), UNAVAILABLE_ERROR)):
            updates = []
            with (
                patch.object(meta, "get_supabase", return_value=Mock()),
                patch.object(meta, "get_gsc_client", side_effect=failure),
                patch.object(meta, "mark_gsc_reconnect_required") as mark,
                patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
                patch.object(meta, "_update_job", side_effect=lambda _sb, _job, _user, data: updates.append(data)),
                patch.object(meta, "_is_cancelled", return_value=False),
            ):
                all_in_one._process_job("job-1", [{"url": "https://example.com/page"}], _runtime_settings(), OAUTH, user_id="user-1")
            self.assertIn({"error": expected}, updates)
            if isinstance(failure, RefreshError):
                mark.assert_called_once_with(ANY, "user-1", OAUTH["refresh_token_ciphertext"])

    def test_rerun_client_handles_missing_refresh_generic_and_exact_error_clear(self):
        cases = [
            ({"use_gsc": True}, None, UNAVAILABLE_ERROR),
            (_runtime_settings(), RefreshError("provider detail"), RECONNECT_ERROR),
            (_runtime_settings(), RuntimeError("provider detail"), UNAVAILABLE_ERROR),
        ]
        for settings, failure, expected in cases:
            with self.subTest(expected=expected):
                sb = _Supabase({"jobs": [_stored_job()]})
                with patch("utils.gsc.get_gsc_client", side_effect=failure), patch.object(jobs, "mark_gsc_reconnect_required") as mark:
                    result = jobs._get_runtime_gsc_client(settings, sb, "user-1", "job-1")
                self.assertIsNone(result)
                self.assertEqual(sb.tables["jobs"][0]["error"], expected)
                if isinstance(failure, RefreshError):
                    mark.assert_called_once_with(sb, "user-1", OAUTH["refresh_token_ciphertext"])

        for old_error, expected in ((RECONNECT_ERROR, None), (UNAVAILABLE_ERROR, None), ("Unrelated failure", "Unrelated failure")):
            sb = _Supabase({"jobs": [_stored_job(old_error)]})
            with patch("utils.gsc.get_gsc_client", return_value="client"):
                self.assertEqual(jobs._get_runtime_gsc_client(_runtime_settings(), sb, "user-1", "job-1"), "client")
            self.assertEqual(sb.tables["jobs"][0]["error"], expected)
            clear = [q for q in sb.executed if q.operation == "update" and q.payload == {"error": None}][0]
            self.assertEqual(clear.filters, [("id", "job-1"), ("user_id", "user-1")])
            self.assertEqual(clear.in_filters, [("error", (UNAVAILABLE_ERROR, RECONNECT_ERROR))])

    def test_single_and_multi_reruns_freshly_hydrate_and_use_exact_envelope(self):
        for function, indices in ((jobs._rerun_single_row, None), (jobs._rerun_multiple_rows, [0])):
            for envelope in (SERVICE_ACCOUNT, OAUTH):
                with self.subTest(function=function.__name__, method=envelope["method"]):
                    sb = _Supabase({"jobs": [_stored_job()]})
                    with (
                        patch.object(jobs, "hydrate_job_settings", return_value=_runtime_settings(envelope)) as hydrate,
                        patch.object(jobs, "_get_runtime_gsc_client", return_value="client") as client,
                        patch.object(meta, "_process_single_row", return_value={"status": "ok"}) as process,
                        patch.object(meta, "_update_job"),
                    ):
                        if indices is None:
                            function("job-1", 0, _stored_job()["rows"], _stored_job()["settings"], sb, user_id="user-1")
                        else:
                            function("job-1", indices, _stored_job()["rows"], _stored_job()["settings"], sb, "user-1")
                    hydrate.assert_called_once_with(sb, "user-1", _stored_job()["settings"])
                    client.assert_called_once_with(_runtime_settings(envelope), sb, "user-1", "job-1")
                    self.assertEqual(process.call_args.kwargs["gsc_client"], "client")
                    terminal = [
                        query for query in sb.executed
                        if query.operation == "update" and query.payload and (
                            "results" in query.payload or query.payload.get("status") == "complete"
                        )
                    ][-1]
                    self.assertEqual(terminal.filters, [("id", "job-1"), ("user_id", "user-1")])
                    _assert_persistence_is_secret_free(self, sb)

    def test_section_rerun_hydration_failure_persists_safe_tenant_scoped_failure(self):
        private_detail = "database-section-private-detail"
        job = {
            **_stored_job(),
            "results": [{"section_results": {"hero": "Existing"}, "primary_keyword": "keyword"}],
        }
        sb = _Supabase({"jobs": [job]})

        with patch.object(jobs, "hydrate_job_settings", side_effect=RuntimeError(private_detail)):
            jobs._rerun_single_section("job-1", 0, "hero", job, "user-1", sb)

        update = [query for query in sb.executed if query.operation == "update"][-1]
        self.assertEqual(update.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(update.payload, {
            "error": "Saved credentials are temporarily unavailable.",
            "current_step": "Section re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        self.assertNotIn(private_detail, repr(update.payload))

    def test_section_rerun_freshly_hydrates_and_clears_only_credential_error(self):
        job = {
            **_stored_job(CREDENTIALS_ERROR),
            "results": [{"section_results": {"hero": "Existing"}, "primary_keyword": "keyword"}],
        }
        sb = _Supabase({"jobs": [job]})
        runtime = _runtime_settings()

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=runtime) as hydrate,
            patch("utils.templates.get_template", return_value={"sections": []}),
        ):
            jobs._rerun_single_section("job-1", 0, "hero", job, "user-1", sb)

        hydrate.assert_called_once_with(sb, "user-1", _stored_job()["settings"])
        self.assertIsNone(sb.tables["jobs"][0].get("error"))
        clear = [q for q in sb.executed if q.operation == "update" and q.payload == {"error": None}][0]
        self.assertEqual(clear.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(clear.in_filters, [("error", (CREDENTIALS_ERROR,))])
        terminal = [q for q in sb.executed if q.operation == "update"][-1]
        self.assertEqual(terminal.filters, [("id", "job-1"), ("user_id", "user-1")])
        _assert_persistence_is_secret_free(self, sb)

    def test_section_rerun_does_not_fall_back_to_direct_credential_reads(self):
        job = {
            **_stored_job(),
            "results": [{"section_results": {"hero": "Existing"}, "primary_keyword": "keyword"}],
        }
        sb = _Supabase({"jobs": [job], "user_settings": _tables()["user_settings"]})
        hydrated = {"provider": "Claude", "use_gsc": True, "_gsc_credentials": OAUTH}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=hydrated),
            patch("utils.templates.get_template", return_value={"sections": []}),
        ):
            jobs._rerun_single_section("job-1", 0, "hero", job, "user-1", sb)

        self.assertFalse(any(query.table == "user_settings" for query in sb.executed))

    def test_section_rerun_regenerates_real_section_with_fresh_credentials(self):
        private_api_key = "fresh-private-api-key"
        job = {
            **_stored_job(CREDENTIALS_ERROR),
            "rows": [{
                "url": "https://example.com/page",
                "page_type": "service",
                "template_key": "service_page",
            }],
            "results": [{
                "url": "https://example.com/page",
                "primary_keyword": "technical seo",
                "h1": "Technical SEO",
                "section_results": {"hero": "Existing hero"},
            }],
        }
        sb = _Supabase({"jobs": [job]})
        runtime = {
            **_runtime_settings(),
            "api_key": private_api_key,
            "dfs_login": "",
            "provider": "Claude",
            "brand_name": "CopyPilot",
        }
        provider = Mock(return_value="# Fresh technical SEO hero")

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=runtime) as hydrate,
            patch("utils.copy_gen.PROVIDER_FN", {"Claude": provider}),
            patch.object(meta, "_build_combined_docx", return_value=b"safe-docx") as build_docx,
        ):
            jobs._rerun_single_section("job-1", 0, "hero", job, "user-1", sb)

        hydrate.assert_called_once_with(sb, "user-1", _stored_job()["settings"])
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args[0], private_api_key)
        build_docx.assert_called_once()
        final = [
            query for query in sb.executed
            if query.operation == "update" and query.payload and "results" in query.payload
        ][-1]
        self.assertEqual(final.filters, [("id", "job-1"), ("user_id", "user-1")])
        persisted = final.payload["results"][0]
        self.assertEqual(persisted["section_results"]["hero"], "# Fresh technical SEO hero")
        self.assertEqual(persisted["docx_b64"], "c2FmZS1kb2N4")
        self.assertNotIn(private_api_key, repr(final.payload))
        self.assertNotIn("_gsc_credentials", repr(final.payload))


if __name__ == "__main__":
    unittest.main()
