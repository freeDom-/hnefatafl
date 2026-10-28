"""Disposable accounts, fake mail and explicit clocks; never production SMTP."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from hnefatafl.account_mail import AccountSettings, SMTPTransport
from hnefatafl.account_policy import RESERVED_NAMES, email, username
from hnefatafl.auth import Auth, SecuritySettings, verify
from hnefatafl.registration import ACKNOWLEDGMENT, INVALID_LINK, Registration
from hnefatafl.security_context import online_eligible
from hnefatafl.server import create_app
from hnefatafl.storage import Store

PASSWORD = "  twelve-characters-secret  "
NEW_PASSWORD = "new-password-for-recovery"
ORIGIN = {"Origin": "http://testserver"}


class Clock:
    value = 1800000000.0

    def __call__(self):
        return self.value


class Crash(BaseException):
    pass


class Mailbox:
    def __init__(self):
        self.messages = []
        self.failure = False
        self.crash = False
        self.callback = None
        self.delivered = []

    def send(self, address, purpose, link):
        self.messages.append((address, purpose, link))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.failure:
            raise RuntimeError("private SMTP password and recipient must never be logged")
        self.delivered.append((address, purpose, link))
        if self.crash:
            raise Crash()

    def token(self, index=-1):
        return parse_qs(urlsplit(self.messages[index][2]).fragment)["token"][0]


@pytest.fixture
def setup(tmp_path):
    clock, mailbox = Clock(), Mailbox()
    settings = AccountSettings(
        enabled=True,
        public_origin="https://tafl.example.test",
        sender="mail@example.test",
        smtp_host="fake.invalid",
    )
    app = create_app(
        tmp_path,
        account_settings=settings,
        mail_transport=mailbox,
        account_clock=clock,
        account_background=False,
    )
    return app, app.state.registration, mailbox, clock


def register(service, name="Raven", address="raven@example.test"):
    return service.register(name, address, PASSWORD)


def deliver(service, mailbox):
    assert service.process_one()
    return mailbox.token()


def account(service, name="Raven"):
    with service.store.connection() as c:
        return dict(
            c.execute("SELECT * FROM accounts WHERE username=? COLLATE NOCASE", (name,)).fetchone()
        )


def test_registration_normalization_no_session_and_digest_only_storage(setup):
    app, service, mailbox, clock = setup
    with TestClient(app, headers=ORIGIN) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": " Ｒaven ",
                "email": " RAVEN+News.Dot@EXAMPLE.test ",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 202 and response.json()["message"] == ACKNOWLEDGMENT
        assert not client.cookies and client.get("/api/auth/me").status_code == 401
    saved = account(service)
    assert saved["username"] == "Raven" and saved["email"] == "raven+news.dot@example.test"
    assert saved["role"] == "USER" and saved["active"] == 1
    assert saved["email_verified_at"] is None and not online_eligible(saved)
    assert verify(PASSWORD, saved["password_hash"]) and not verify(
        PASSWORD.strip(), saved["password_hash"]
    )
    token = deliver(service, mailbox)
    assert mailbox.messages[-1][2].startswith("https://tafl.example.test/verify-email#token=")
    with service.store.connection() as c:
        dump = "\n".join(c.iterdump())
    assert token not in dump and PASSWORD not in dump and "#token=" not in dump
    service.confirm("verify", token)
    assert online_eligible(account(service))


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_names_for_public_only(setup, name):
    _, service, _, _ = setup
    with pytest.raises(ValueError, match="unavailable"):
        register(service, " " + name.upper() + " ")
    created = service.auth.create_account(name, name + "@example.test", PASSWORD)
    assert created["username"] == name


@pytest.mark.parametrize("name", ["x", "x" * 65, "<script>", "a b", "a@b", "éé"])
def test_username_boundaries(name):
    with pytest.raises(ValueError):
        username(name, public=True)
    assert username("x" * 64) == "x" * 64


@pytest.mark.parametrize(
    "address",
    [
        "",
        "bad",
        "a@b",
        "a b@example.test",
        "a\r\nBcc:b@c.test",
        "x" * 255 + "@example.test",
        "a,b@example.test",
    ],
)
def test_email_boundaries(address):
    with pytest.raises(ValueError):
        email(address)


@pytest.mark.parametrize(
    "extra", [{"role": "ADMIN"}, {"active": 1}, {"email_verified": True}, {"email_verified_at": 1}]
)
def test_public_client_cannot_assign_privileges(setup, extra):
    app, service, _, _ = setup
    with TestClient(app, headers=ORIGIN) as client:
        result = client.post(
            "/api/auth/register",
            json={
                "username": "Raven",
                "email": "raven@example.test",
                "password": PASSWORD,
                **extra,
            },
        )
        assert result.status_code == 422
        assert PASSWORD not in result.text and "input" not in result.text
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_registration_disabled_and_missing_mail_preserve_login(setup):
    app, service, _, _ = setup
    saved = service.auth.create_account("Raven", "raven@example.test", PASSWORD)
    with TestClient(app, headers=ORIGIN) as client:
        service.settings = replace(service.settings, enabled=False)
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "Other", "email": "other@example.test", "password": PASSWORD},
            ).status_code
            == 403
        )
        service.settings = replace(service.settings, enabled=True, smtp_host="")
        assert not client.get("/api/auth/registration").json()["enabled"]
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "Other", "email": "other@example.test", "password": PASSWORD},
            ).status_code
            == 503
        )
        login = client.post("/api/auth/login", json={"identity": "Raven", "password": PASSWORD})
        assert login.status_code == 200 and login.json()["account"]["id"] == saved["id"]
        assert not login.json()["account"]["email_verified"]
        assert client.get("/api/games").status_code == 200


def test_generic_email_responses_and_capacity_do_not_enumerate(setup):
    _, service, mailbox, clock = setup
    register(service)
    deliver(service, mailbox)
    service.confirm("verify", mailbox.token())
    service.auth.create_account("Disabled", "disabled@example.test", PASSWORD)
    with service.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE username='Disabled'")
    for purpose in ("verify", "reset"):
        responses = [
            service.request_mail(purpose, address)
            for address in ("raven@example.test", "missing@example.test", "disabled@example.test")
        ]
        assert responses == [{"message": ACKNOWLEDGMENT}] * 3
    service.settings = replace(service.settings, queue_limit=1)
    for address in ("raven@example.test", "missing@example.test", "disabled@example.test"):
        with pytest.raises(HTTPException) as error:
            service.request_mail("verify", address)
        assert error.value.status_code == 503


@pytest.mark.parametrize("same", ["username", "email"])
def test_concurrent_registration_uniqueness(setup, same):
    _, service, _, _ = setup

    def attempt(index):
        try:
            return service.register(
                "Raven" if same == "username" else f"Raven{index}",
                "same@example.test" if same == "email" else f"raven{index}@example.test",
                PASSWORD,
            )
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM account_mail").fetchone()[0] == 1
    assert outcomes.count(409) == (1 if same == "username" else 0)


@pytest.mark.parametrize("purpose,ttl", [("verify", 86400), ("reset", 1800)])
@pytest.mark.parametrize("offset", [-0.001, 0, 0.001])
def test_expiry_boundary_and_single_use(setup, purpose, ttl, offset):
    _, service, mailbox, clock = setup
    register(service)
    if purpose == "reset":
        service.request_mail("reset", "raven@example.test")
        deliver(service, mailbox)  # earlier verification intent
    token = deliver(service, mailbox)
    clock.value += ttl + offset
    if offset < 0:
        service.confirm(purpose, token, NEW_PASSWORD if purpose == "reset" else None)
    else:
        with pytest.raises(HTTPException) as error:
            service.confirm(purpose, token, NEW_PASSWORD if purpose == "reset" else None)
        assert error.value.detail == INVALID_LINK
    with pytest.raises(HTTPException):
        service.confirm(purpose, token, NEW_PASSWORD if purpose == "reset" else None)


def test_scanner_get_purpose_address_binding_and_replacement(setup):
    app, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)
    with TestClient(app, headers=ORIGIN) as client:
        client.get("/api/auth/verify-email")
        assert account(service)["email_verified_at"] is None
    with pytest.raises(HTTPException):
        service.confirm("reset", token, NEW_PASSWORD)
    service.request_mail("verify", "raven@example.test")
    with pytest.raises(HTTPException):
        service.confirm("verify", token)
    next_token = deliver(service, mailbox)
    assert next_token != token
    with service.store.connection() as c:
        c.execute("UPDATE accounts SET email_version=email_version+1")
    with pytest.raises(HTTPException):
        service.confirm("verify", next_token)


def test_concurrent_consumption(setup):
    _, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)

    def consume(_):
        try:
            service.confirm("verify", token)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [200, 400]


def test_atomic_password_reset_revokes_all_sessions_across_instances(setup):
    _, service, mailbox, _ = setup
    register(service)
    saved = account(service)
    session1 = service.auth.login("Raven", PASSWORD, "one")[1]
    session2 = service.auth.login("Raven", PASSWORD, "two")[1]
    service.request_mail("reset", "raven@example.test")
    deliver(service, mailbox)
    token = deliver(service, mailbox)
    with service.store.connection() as c:
        c.execute(
            "CREATE TRIGGER fail_session_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT,'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.confirm("reset", token, NEW_PASSWORD)
    assert verify(PASSWORD, account(service)["password_hash"])
    assert service.auth.session(session1) and service.auth.session(session2)
    with service.store.connection() as c:
        assert (
            c.execute("SELECT consumed_at FROM account_tokens WHERE purpose='reset'").fetchone()[0]
            is None
        )
        c.execute("DROP TRIGGER fail_session_delete")
    service.confirm("reset", token, NEW_PASSWORD)
    restored = Auth(Store(service.store.path), SecuritySettings())
    assert restored.session(session1) is restored.session(session2) is None
    process_check = """import json,sys
from hnefatafl.auth import Auth,SecuritySettings
from hnefatafl.storage import Store
auth=Auth(Store(sys.argv[1]),SecuritySettings())
assert all(auth.session(token) is None for token in json.loads(sys.stdin.read()))
print('revoked')
"""
    checked = subprocess.run(
        [sys.executable, "-c", process_check, str(service.store.path)],
        input=json.dumps([session1, session2]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert checked.stdout.strip() == "revoked"
    assert verify(NEW_PASSWORD, restored.account(saved["id"])["password_hash"])
    assert restored.account(saved["id"])["email_verified_at"] is None
    assert not online_eligible(restored.account(saved["id"]))
    with pytest.raises(HTTPException):
        restored.login("Raven", PASSWORD, "three")


def test_disabled_tokens_and_cli_recovery_never_enable_or_verify(setup):
    _, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)
    saved = account(service)
    with service.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (saved["id"],))
    with pytest.raises(HTTPException):
        service.confirm("verify", token)
    service.auth.update_account(saved["id"], password=NEW_PASSWORD)
    assert account(service)["active"] == 0 and account(service)["email_verified_at"] is None
    assert not online_eligible(account(service))
    with service.store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM account_tokens WHERE purpose='reset'").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("role", ["USER", "ADMIN"])
def test_online_gate_is_independent_of_existing_play_permissions(setup, role):
    _, service, _, _ = setup
    entry = service.auth.create_account("Odin", "odin@example.test", PASSWORD, role)
    assert not online_eligible(entry)
    assert online_eligible({**entry, "email_verified_at": 0.0})
    assert not online_eligible({**entry, "email_verified_at": 1, "active": 0})
    assert not online_eligible(None)


def test_mail_retry_backoff_digest_rotation_and_sanitized_errors(setup, caplog):
    _, service, mailbox, clock = setup
    register(service)
    mailbox.failure = True
    first = deliver(service, mailbox)
    assert not service.process_one()
    with pytest.raises(HTTPException):
        service.confirm("verify", first)
    clock.value += service.settings.retry_seconds
    mailbox.failure = False
    second = deliver(service, mailbox)
    assert first != second
    service.confirm("verify", second)
    assert not caplog.text


@pytest.mark.parametrize("before_send", [True, False])
def test_crash_windows_recover_expired_lease_without_resurrecting_old_token(setup, before_send):
    _, service, mailbox, clock = setup
    register(service)
    mailbox.crash = True
    if before_send:
        mailbox.callback = lambda: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        service.process_one()
    assert len(mailbox.delivered) == (0 if before_send else 1)
    old = mailbox.token()
    restarted = Registration(service.auth, service.settings, mailbox, clock)
    assert not restarted.process_one()
    clock.value += service.settings.lease_seconds
    mailbox.crash = False
    new = deliver(restarted, mailbox)
    assert new != old
    with pytest.raises(HTTPException):
        service.confirm("verify", old)
    service.confirm("verify", new)


def test_stale_mail_finalizer_cannot_overwrite_resend(setup):
    _, service, mailbox, _ = setup
    register(service)
    mailbox.callback = lambda: service.request_mail("verify", "raven@example.test")
    old = deliver(service, mailbox)
    new = deliver(service, mailbox)
    assert old != new
    with pytest.raises(HTTPException):
        service.confirm("verify", old)
    service.confirm("verify", new)


def test_bounded_retry_and_global_mail_budget(setup):
    _, service, mailbox, clock = setup
    service.settings = replace(service.settings, max_attempts=2, global_mail=2)
    register(service)
    mailbox.failure = True
    deliver(service, mailbox)
    clock.value += 60
    deliver(service, mailbox)
    assert not service.process_one()
    with service.store.connection() as c:
        assert c.execute("SELECT status FROM account_mail").fetchone()[0] == "failed"
        assert c.execute("SELECT COUNT(*) FROM account_tokens").fetchone()[0] == 0
    register(service, "Second", "second@example.test")
    assert not service.process_one()
    clock.value += 900
    mailbox.failure = False
    assert service.process_one()


def test_origin_body_limits_validation_redaction_and_spoofed_proxy(setup):
    app, service, _, clock = setup
    service.settings = replace(service.settings, register_ip=1)
    payload = {"username": "Raven", "email": "raven@example.test", "password": PASSWORD}
    with TestClient(app) as client:
        for headers in ({}, {"Origin": "https://evil.example"}):
            assert (
                client.post("/api/auth/register", json=payload, headers=headers).status_code == 403
            )
        assert (
            client.post(
                "/api/auth/register",
                content="x" * 8193,
                headers={**ORIGIN, "Content-Type": "application/json"},
            ).status_code
            == 413
        )
        malformed = client.post(
            "/api/auth/register", json={**payload, "role": "ADMIN"}, headers=ORIGIN
        )
        assert malformed.status_code == 422 and PASSWORD not in malformed.text
        # Malformed requests already spend the IP budget, before parsing/hashing.
        blocked = client.post(
            "/api/auth/register", json=payload, headers={**ORIGIN, "X-Forwarded-For": "1.2.3.4"}
        )
        assert blocked.status_code == 429
        clock.value += 900
        assert client.post("/api/auth/register", json=payload, headers=ORIGIN).status_code == 202


def test_concurrent_ip_limits_restart_storage_and_hash_cap(setup, monkeypatch):
    _, service, _, clock = setup
    service.settings = replace(service.settings, register_ip=1, global_hashes=1)

    def admit(_):
        try:
            service.admit_ip("register", "same peer")
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(admit, range(2))) == [200, 429]
    other = Registration(service.auth, service.settings, service.transport, clock)
    with pytest.raises(HTTPException):
        other.admit_ip("register", "same peer")
    register(service)

    def forbidden(_):
        raise AssertionError("Rate limit must precede hashing")

    monkeypatch.setattr("hnefatafl.registration.password_hash", forbidden)
    with pytest.raises(HTTPException) as error:
        register(service, "Second", "second@example.test")
    assert error.value.status_code == 429
    with service.store.connection() as c:
        keys = [r[0] for r in c.execute("SELECT key FROM account_limits")]
        assert all("@" not in key and "same peer" not in key for key in keys)


def test_cross_process_global_limit_and_cleanup(setup):
    _, service, _, clock = setup
    service.settings = replace(service.settings, global_requests=1)
    service.admit_ip("register", "first")
    code = """import sys
from dataclasses import replace
from fastapi import HTTPException
from hnefatafl.auth import Auth,SecuritySettings
from hnefatafl.storage import Store
from hnefatafl.registration import Registration
from hnefatafl.account_mail import AccountSettings
s=Registration(Auth(Store(sys.argv[1]),SecuritySettings()),replace(AccountSettings(),global_requests=1),clock=lambda:1800000000.)
try: s.admit_ip('register','other process')
except HTTPException as e: print(e.status_code)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(service.store.path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "429"
    clock.value += 900
    service.admit_ip("register", "after expiry")
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM account_limits").fetchone()[0] == 2


def test_limiter_key_capacity_is_fail_closed(setup):
    _, service, _, _ = setup
    service.settings = replace(service.settings, limit_keys=2)
    service.admit_ip("register", "one")
    with pytest.raises(HTTPException):
        service.admit_ip("register", "two")
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM account_limits").fetchone()[0] == 2


def test_smtp_transport_requires_tls_and_uses_bounded_settings(setup, monkeypatch):
    _, service, _, _ = setup
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr("hnefatafl.account_mail.smtplib.SMTP", factory)
    settings = replace(
        service.settings, smtp_username="test-user", smtp_password="test-only-secret"
    )
    SMTPTransport(settings).send(
        "raven@example.test", "verify", "https://tafl.example.test/verify-email#token=test"
    )
    assert factory.call_args.kwargs["timeout"] == 10
    names = [call[0] for call in smtp.method_calls]
    assert names.index("starttls") < names.index("login") < names.index("send_message")
    assert "test-only-secret" not in repr(settings)
    assert not replace(settings, public_origin="http://public.example").mail_ready()
    assert not replace(settings, smtp_tls="plain").mail_ready()
    assert not replace(settings, public_origin="https://user:password@example.test").mail_ready()
    assert not replace(settings, public_origin="https://example.test/path").mail_ready()


def test_default_registration_is_disabled_and_settings_bounded(monkeypatch):
    monkeypatch.delenv("HNEFATAFL_REGISTRATION_ENABLED", raising=False)
    assert not AccountSettings.environment().enabled
    monkeypatch.setenv("HNEFATAFL_ACCOUNT_TIMEOUT", "31")
    with pytest.raises(ValueError):
        AccountSettings.environment()


def test_bad_mail_configuration_fails_closed_without_disabling_login(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HNEFATAFL_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("HNEFATAFL_ACCOUNT_TIMEOUT", "not-an-integer")
    app = create_app(tmp_path, account_background=False)
    app.state.auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN")
    with TestClient(app, headers=ORIGIN) as client:
        assert (
            client.post(
                "/api/auth/login", json={"identity": "Odin", "password": PASSWORD}
            ).status_code
            == 200
        )
        assert not client.get("/api/auth/registration").json()["enabled"]
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "Raven", "email": "raven@example.test", "password": PASSWORD},
            ).status_code
            == 503
        )
    assert "not-an-integer" not in caplog.text and PASSWORD not in caplog.text


def test_login_registration_and_reset_share_global_hash_budget(setup, monkeypatch):
    _, service, _, _ = setup
    service.auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN")
    service.settings = replace(service.settings, global_hashes=1)
    assert service.auth.login("Odin", PASSWORD, "first")[0]["username"] == "Odin"

    def no_hash(*_):
        raise AssertionError("Throttling must run before the expensive password operation")

    monkeypatch.setattr("hnefatafl.auth.verify", no_hash)
    with pytest.raises(HTTPException) as error:
        service.auth.login("Odin", PASSWORD, "another address")
    assert error.value.status_code == 429


def test_deployment_freeze_prevents_background_mail_writes(setup):
    import fcntl

    _, service, mailbox, _ = setup
    register(service)
    with service.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not service.process_one()
    assert not mailbox.messages
    assert service.process_one()


def test_address_binding_rejects_out_of_band_email_changes(setup):
    _, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)
    with service.store.connection() as c:
        c.execute("UPDATE accounts SET email='different@example.test'")
    with pytest.raises(HTTPException) as error:
        service.confirm("verify", token)
    assert error.value.status_code == 400
    assert account(service)["email_verified_at"] is None


def test_missing_mail_never_verifies_and_a_pending_intent_cannot_change_recipients(setup):
    _, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)
    original = service.settings
    service.settings = replace(original, smtp_host="")
    with pytest.raises(HTTPException) as error:
        service.confirm("verify", token)
    assert error.value.status_code == 503 and account(service)["email_verified_at"] is None
    service.settings = original
    service.request_mail("verify", "raven@example.test")
    with service.store.connection() as c:
        c.execute("UPDATE accounts SET email='different@example.test'")
    assert service.process_one()
    assert len(mailbox.messages) == 1


def test_reset_prevents_inflight_old_password_login_from_creating_a_new_session(setup, monkeypatch):
    _, service, mailbox, _ = setup
    register(service)
    service.request_mail("reset", "raven@example.test")
    deliver(service, mailbox)
    token = deliver(service, mailbox)
    original_verify = verify

    def racing_verify(password, encoded):
        matched = original_verify(password, encoded)
        service.confirm("reset", token, NEW_PASSWORD)
        return matched

    monkeypatch.setattr("hnefatafl.auth.verify", racing_verify)
    with pytest.raises(HTTPException) as error:
        service.auth.login("Raven", PASSWORD, "old-password-in-flight")
    assert error.value.status_code == 401
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_verification_is_bound_to_one_account_and_rejects_client_account_selection(setup):
    app, service, mailbox, _ = setup
    register(service)
    token = deliver(service, mailbox)
    register(service, "Other", "other@example.test")
    with TestClient(app, headers=ORIGIN) as client:
        wrong = client.post(
            "/api/auth/verify-email",
            json={"token": token, "account_id": account(service, "Other")["id"]},
        )
        assert wrong.status_code == 422 and token not in wrong.text
    service.confirm("verify", token)
    assert online_eligible(account(service)) and not online_eligible(account(service, "Other"))


def test_chunked_auth_bodies_are_bounded_without_reflecting_secrets(setup):
    app, _, _, _ = setup
    with TestClient(app, headers=ORIGIN) as client:
        response = client.post(
            "/api/auth/register",
            content=iter([b'{"password":"', b"x" * 9000, b'"}']),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413 and "x" * 100 not in response.text


def test_browser_fake_mail_guard_runs_before_application_import(tmp_path):
    import os
    from pathlib import Path

    data = tmp_path / "must-not-be-created"
    environment = {**os.environ, "HNEFATAFL_ENV": "production", "HNEFATAFL_DATA_DIR": str(data)}
    script = Path(__file__).resolve().parents[1] / "frontend/tests/start_registration_backend.py"
    result = subprocess.run(
        [sys.executable, str(script)], env=environment, text=True, capture_output=True
    )
    assert result.returncode != 0 and "isolated development storage" in result.stderr
    assert not data.exists()


def test_exhausted_hash_budget_does_not_allocate_legacy_login_keys(setup):
    _, service, _, _ = setup
    service.settings = replace(service.settings, global_hashes=1)
    service.auth.create_account("Odin", "odin@example.test", PASSWORD)
    service.auth.login("Odin", PASSWORD, "first")
    for index in range(3):
        with pytest.raises(HTTPException) as error:
            service.auth.login(f"Unknown{index}", PASSWORD, f"different-peer-{index}")
        assert error.value.status_code == 429
    with service.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 2
