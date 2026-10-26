"""Database-backed opaque sessions and a fail-closed ASGI security boundary.

Only hashes of random session/CSRF tokens are retained. Identity and permissions
are reloaded on every request, including across processes and server restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import fcntl
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse
from uuid import uuid4
from typing import Callable

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Match

from .security_context import PERMISSIONS, allowed, principal, session_identity
from .account_policy import (
    normalize,
    username as validate_username,
    email as validate_email,
    validate_password,
)

COOKIE = "hnefatafl_session"
SAFE = {"GET", "HEAD", "OPTIONS"}
# This module owns policy; account_api is imported only after Auth is defined.
ACCOUNT_ACTIONS = {
    "/api/auth/register": "register",
    "/api/auth/verification/resend": "resend",
    "/api/auth/verify-email": "confirm",
    "/api/auth/password-reset/request": "reset",
    "/api/auth/password-reset/confirm": "confirm",
}
ACCOUNT_PAGES = {"/register", "/verify-email", "/forgot-password", "/reset-password"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str) -> str:
    validate_password(password)
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=32768, r=8, p=3, maxmem=67108864)
    return f"scrypt$32768$8$3${salt}${key.hex()}"


def verify(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt" or len(password) > 256:
            return False
        key = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=int(n), r=int(r), p=int(p), maxmem=67108864
        )
        return hmac.compare_digest(key.hex(), expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class SecuritySettings:
    production: bool = False
    public_url: str = ""
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    session_seconds: int = 43200

    @classmethod
    def environment(cls):
        environment = os.environ.get("HNEFATAFL_ENV", "development")
        if environment not in {"development", "production"}:
            raise ValueError("HNEFATAFL_ENV must be development or production")
        production = environment == "production"
        url = os.environ.get("HNEFATAFL_PUBLIC_URL", "").rstrip("/")
        hosts = tuple(
            h.strip()
            for h in os.environ.get(
                "HNEFATAFL_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
            ).split(",")
        )
        ttl = int(os.environ.get("HNEFATAFL_SESSION_SECONDS", "43200"))
        if not 60 <= ttl <= 604800:
            raise ValueError("Session lifetime must be between 60 and 604800 seconds")
        if production:
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or re.search(r"\s", url)
            ):
                raise ValueError("Production requires an HTTPS HNEFATAFL_PUBLIC_URL origin")
            if (
                not os.environ.get("HNEFATAFL_ALLOWED_HOSTS")
                or "*" in hosts
                or parsed.hostname not in hosts
            ):
                raise ValueError("Production requires explicit HNEFATAFL_ALLOWED_HOSTS")
            if (
                not os.environ.get("HNEFATAFL_TRUSTED_PROXIES")
                or "*" in os.environ["HNEFATAFL_TRUSTED_PROXIES"]
            ):
                raise ValueError("Production requires explicit HNEFATAFL_TRUSTED_PROXIES")
            for proxy in os.environ["HNEFATAFL_TRUSTED_PROXIES"].split(","):
                network = ipaddress.ip_network(proxy.strip(), strict=False)
                if network.prefixlen == 0:
                    raise ValueError("Trust individual proxies or narrow proxy networks")
        return cls(production, url, hosts, ttl)


def public_account(account: dict) -> dict:
    return {
        **{
            k: account[k]
            for k in (
                "id",
                "username",
                "email",
                "role",
                "active",
                "created_at",
                "last_login",
                "display_name",
            )
        },
        "permissions": sorted(PERMISSIONS.get(account["role"], ())),
        "email_verified": account.get("email_verified_at") is not None,
    }


class Auth:
    def __init__(self, store, settings: SecuritySettings):
        self.store, self.settings = store, settings
        self.gate_path = store.path.parent / ".deployment-gate"
        os.close(os.open(self.gate_path, os.O_CREAT | os.O_RDONLY, 0o644))
        self.worker_auth: Callable | None = None
        self.account_requests: Callable[[str, str], None] | None = None
        self.public_hash_budget: Callable[[sqlite3.Connection], None] | None = None
        # Same work for nonexistent identities; no per-request expensive setup.
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))

    def create_account(
        self, username: str, email: str, password: str, role="USER", *, bootstrap=False
    ) -> dict:
        username, email = validate_username(username), validate_email(email)
        if role not in PERMISSIONS:
            raise ValueError("Unknown role")
        encoded = password_hash(password)
        identifier = uuid4().hex
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if (
                bootstrap
                and c.execute("SELECT 1 FROM auth_meta WHERE key='bootstrapped'").fetchone()
            ):
                raise ValueError("Administrator already provisioned; use the local reset command")
            c.execute(
                "INSERT INTO accounts(id,username,email,password_hash,role,active,created_at,display_name) VALUES(?,?,?,?,?,1,?,?)",
                (identifier, username, email, encoded, role, time.time(), username),
            )
            if bootstrap:
                c.execute(
                    "INSERT INTO auth_meta(key,value) VALUES('bootstrapped',?)", (identifier,)
                )
        return self.account(identifier)

    def account(self, identifier: str) -> dict:
        with self.store.connection() as c:
            row = c.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Account not found")
        return dict(row)

    def login(self, identity: str, password: str, address: str) -> tuple[dict, str, str]:
        moment = time.time()
        identity = normalize(identity)
        keys = ["ip:" + digest(address), "identity:" + digest(identity)]
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM login_attempts WHERE started < ?", (moment - 900,))
            for key in keys:
                row = c.execute(
                    "SELECT attempts FROM login_attempts WHERE key=?", (key,)
                ).fetchone()
                if row and row[0] >= (30 if key.startswith("ip:") else 10):
                    raise HTTPException(429, "Too many login attempts. Try again later.")
            if self.public_hash_budget:
                # Share the transaction: denial must not allocate unbounded legacy
                # login-attempt keys for fresh attacker-controlled identities/IPs.
                self.public_hash_budget(c)
            for key in keys:
                c.execute(
                    "INSERT INTO login_attempts(key,started,attempts) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1",
                    (key, moment),
                )
            row = c.execute(
                "SELECT * FROM accounts WHERE username=? COLLATE NOCASE OR email=?",
                (identity, identity),
            ).fetchone()
        account = dict(row) if row else None
        valid = verify(password, account["password_hash"] if account else self.dummy_hash)
        if not valid or not account or not allowed(account, "play"):
            raise HTTPException(401, "Invalid username/email or password.")
        token = secrets.token_urlsafe(32)
        csrf = digest("csrf:" + token)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone()
            if (
                current is None
                or not allowed(dict(current), "play")
                or not hmac.compare_digest(current["password_hash"], account["password_hash"])
            ):
                raise HTTPException(401, "Invalid username/email or password.")
            c.execute("DELETE FROM sessions WHERE expires<=?", (moment,))
            c.execute(
                "INSERT INTO sessions(token_hash,account_id,expires,csrf_hash) VALUES(?,?,?,?)",
                (
                    digest(token),
                    account["id"],
                    moment + self.settings.session_seconds,
                    digest(csrf),
                ),
            )
            c.execute("UPDATE accounts SET last_login=? WHERE id=?", (moment, account["id"]))
        return self.account(account["id"]), token, csrf

    def session(self, token: str) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.store.connection() as c:
            row = c.execute(
                "SELECT a.*,s.csrf_hash FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?",
                (digest(token), time.time()),
            ).fetchone()
        account = dict(row) if row else None
        return account if allowed(account, "play") else None

    def revoke(self, token: str):
        with self.store.connection() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))

    def update_account(self, identifier: str, *, role=None, active=None, password=None):
        if role is not None and role not in PERMISSIONS:
            raise ValueError("Unknown role")
        encoded = password_hash(password) if password is not None else None
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("Account not found")
            if (
                row["role"] == "ADMIN"
                and row["active"]
                and (role not in (None, "ADMIN") or active is False)
            ):
                if (
                    c.execute(
                        "SELECT COUNT(*) FROM accounts WHERE role='ADMIN' AND active=1"
                    ).fetchone()[0]
                    <= 1
                ):
                    raise ValueError("Cannot disable or demote the last administrator")
            c.execute(
                "UPDATE accounts SET role=COALESCE(?,role),active=COALESCE(?,active),password_hash=COALESCE(?,password_hash) WHERE id=?",
                (role, active, encoded, identifier),
            )
            c.execute("DELETE FROM sessions WHERE account_id=?", (identifier,))
            if password is not None or active is False:
                for table in ("account_tokens", "account_mail"):
                    c.execute(
                        f"DELETE FROM {table} WHERE account_id=?"
                        + (" AND purpose='reset'" if active is not False else ""),
                        (identifier,),
                    )


# Exact method/path declarations: unknown routes remain denied, including future
# routes accidentally registered without a policy. Worker routes additionally
# validate their scoped bearer credential/lease in remote_training.py.
POLICY: dict[tuple[str, str], str] = {}


def grant(permission, method, *paths):
    for path in paths:
        POLICY[(method, path)] = permission


grant("public", "GET", "/api/health", "/api/auth/registration")
grant("public", "POST", "/api/auth/login", *ACCOUNT_ACTIONS)
grant("profile", "GET", "/api/auth/me")
grant("profile", "POST", "/api/auth/logout")
grant(
    "play",
    "GET",
    "/api/rulesets",
    "/api/ai/profiles",
    "/api/models",
    "/api/games",
    "/api/players",
    "/api/stats",
    "/api/personal/single-player",
    "/api/personal/single-player/{game_id}/replay",
    "/api/personal/online",
    "/api/personal/online/{game_id}",
    "/api/personal/online/{game_id}/replay",
    "/api/history/games",
    "/api/history/{category}/{game_id}/replay",
    "/api/games/{game_id}",
    "/api/games/{game_id}/analytics",
    "/api/games/{game_id}/replay",
)
grant(
    "play",
    "POST",
    "/api/games",
    "/api/players",
    *(
        f"/api/games/{{game_id}}/{action}"
        for action in ("analysis", "moves", "ai-move", "seek", "undo", "redo", "restart")
    ),
)
grant("play", "PUT", "/api/games/{game_id}/analytics")
grant(
    "play",
    "GET",
    "/api/online/games",
    "/api/online/games/{game_id}",
    "/api/online/games/{game_id}/replay",
    "/api/online/stats",
)
grant(
    "play",
    "POST",
    "/api/online/games",
    *(
        f"/api/online/games/{{game_id}}/{action}"
        for action in ("join", "cancel", "moves", "concede")
    ),
)
grant("accounts", "POST", "/api/online/games/{game_id}/void")
grant("accounts", "GET", "/api/online/timeout-gate")
grant("accounts", "GET", "/api/online/monitoring")
grant("play", "GET", "/api/online/availability")
grant("accounts", "PATCH", "/api/online/timeout-gate")
grant("play", "DELETE", "/api/games/{game_id}", "/api/players/{player_id}")
grant(
    "tournaments",
    "GET",
    "/api/tournaments",
    "/api/tournaments/{tournament_id}",
    "/api/tournaments/{tournament_id}/export",
    "/api/tournaments/{tournament_id}/games/{game_index}/replay",
)
grant("tournaments", "POST", "/api/tournaments", "/api/tournaments/{tournament_id}/cancel")
grant("tournaments", "DELETE", "/api/tournaments/{tournament_id}")
grant("models", "PATCH", "/api/models/{model_id}")
grant("models", "GET", "/api/models/{model_id}/usage", "/api/models/{model_id}/export")
grant("models", "POST", "/api/models/import")
grant("models", "DELETE", "/api/models/{model_id}")
grant(
    "training",
    "GET",
    "/api/training/device",
    "/api/training/presets",
    "/api/training/jobs",
    "/api/training/jobs/{job_id}",
    "/api/training/jobs/{job_id}/export",
    "/api/training/jobs/{job_id}/resume",
    "/api/training/workers",
)
grant(
    "training",
    "POST",
    "/api/training/jobs",
    "/api/training/jobs/import",
    "/api/training/jobs/{job_id}/resume",
    "/api/training/jobs/{job_id}/cancel",
    "/api/training/workers",
)
grant("training", "PATCH", "/api/training/jobs/{job_id}")
grant("training", "DELETE", "/api/training/jobs/{job_id}", "/api/training/workers/{worker_id}")
grant("accounts", "PATCH", "/api/training/workers/{worker_id}/version-override")
grant(
    "worker",
    "POST",
    "/api/worker/heartbeat",
    "/api/worker/claim",
    "/api/worker/jobs/{job_id}/progress",
    "/api/worker/jobs/{job_id}/artifacts/{kind}",
    "/api/worker/jobs/{job_id}/finish",
)
grant("worker", "GET", "/api/worker/jobs/{job_id}/resume")


class SecurityBoundary:
    def __init__(self, app, *, router, auth: Auth):
        self.app, self.router, self.auth = app, router, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        original_send = send

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (
                        b"referrer-policy",
                        b"no-referrer" if scope["path"] in ACCOUNT_PAGES else b"same-origin",
                    ),
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        # Only the content-addressed guest worker may compile Wasm.
                        # Account/online documents and APIs keep the strict policy.
                        (
                            b"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; worker-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                            if scope["method"] == "GET"
                            and re.fullmatch(
                                r"/assets/guest-[a-f0-9]{64}/worker\.js", scope["path"]
                            )
                            else b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
                        ),
                    ),
                ]
            await original_send(message)

        send = protected_send
        request = HTTPConnection(scope)
        host = request.url.hostname
        if host not in self.auth.settings.hosts:
            return await JSONResponse({"detail": "Invalid host"}, 400)(scope, receive, send)
        path, method = scope["path"], scope["method"]
        route = next((r for r in self.router.routes if r.matches(scope)[0] == Match.FULL), None)
        route_path = getattr(route, "path", "")
        permission = POLICY.get((method, route_path), "deny")
        online_mutation = method not in SAFE and route_path.startswith("/api/online/")
        if (method == "POST" and route_path.startswith("/api/auth/")) or online_mutation:
            size_error = (
                "Online request too large" if online_mutation else "Login request too large"
            )
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = 8193
            if not 0 <= length <= 8192:
                return await JSONResponse({"detail": size_error}, 413)(scope, receive, send)
            original_receive, received = receive, 0

            async def bounded_login_receive():
                nonlocal received
                message = await original_receive()
                received += len(message.get("body", b""))
                if received > 8192:
                    raise HTTPException(413, size_error)
                return message

            receive = bounded_login_receive
        # Only immutable frontend build assets and the login shell are public.
        shell = method == "GET" and (
            (
                route_path == "/{path:path}"
                and path
                in {
                    "/",
                    "/login",
                    "/guest",
                    "/guest/",
                    "/index.html",
                    "/favicon.svg",
                    *ACCOUNT_PAGES,
                }
            )
            or (route_path == "/assets" and path.startswith("/assets/"))
        )
        account = self.auth.session(request.cookies.get(COOKIE, ""))
        if permission == "worker":
            try:
                if self.auth.worker_auth is None:
                    raise HTTPException(403, "Worker authentication unavailable")
                self.auth.worker_auth(request.headers.get("authorization"))
            except HTTPException as error:
                return await JSONResponse({"detail": error.detail}, error.status_code)(
                    scope, receive, send
                )
        if shell:
            permission = "public"
        elif route_path == "/{path:path}" and not path.startswith("/api") and method == "GET":
            permission = (
                "training"
                if path.rstrip("/") == "/training" or path.startswith("/training/")
                else "tournaments"
                if path.rstrip("/") == "/tournaments" or path.startswith("/tournaments/")
                else "play"
            )
        if permission not in {"public", "worker"}:
            if account is None:
                response = (
                    JSONResponse({"detail": "Authentication required"}, 401)
                    if path.startswith("/api")
                    else RedirectResponse(
                        "/login?next="
                        + quote(
                            path + ("?" + request.url.query if request.url.query else ""), safe=""
                        )
                    )
                )
                return await response(scope, receive, send)
            if not allowed(account, permission):
                status = 404 if route_path == "/{path:path}" and path.startswith("/api") else 403
                return await JSONResponse({"detail": "Access denied"}, status)(scope, receive, send)
        if method not in SAFE and permission != "worker":
            origin = request.headers.get("origin")
            expected = self.auth.settings.public_url or str(request.base_url).rstrip("/")
            if origin and origin != expected:
                return await JSONResponse({"detail": "Invalid request origin"}, 403)(
                    scope, receive, send
                )
            if permission != "public" and (
                not account
                or not hmac.compare_digest(
                    digest(request.headers.get("x-csrf-token", "")), account["csrf_hash"]
                )
            ):
                return await JSONResponse({"detail": "CSRF token required"}, 403)(
                    scope, receive, send
                )
            if permission == "public" and (
                not origin
                or request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                return await JSONResponse(
                    {"detail": "Login requires JSON and a same-origin Origin header"}, 403
                )(scope, receive, send)
            if online_mutation and (
                not origin
                or request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                return await JSONResponse(
                    {"detail": "Online actions require JSON and a same-origin Origin header"}, 403
                )(scope, receive, send)

        context = principal.set(account)
        session_context = session_identity.set(
            digest(request.cookies.get(COOKIE, "")) if account else None
        )
        gate = None
        try:
            if method not in SAFE:
                gate = self.auth.gate_path.open("rb")
                try:
                    fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    return await JSONResponse(
                        {"detail": "Deployment in progress. Please retry shortly."}, 503
                    )(scope, receive, send)
            if method == "POST" and route_path in ACCOUNT_ACTIONS and self.auth.account_requests:
                try:
                    self.auth.account_requests(
                        ACCOUNT_ACTIONS[route_path],
                        request.client.host if request.client else "unknown",
                    )
                except HTTPException as error:
                    return await JSONResponse({"detail": error.detail}, error.status_code)(
                        scope, receive, send
                    )
            await self.app(scope, receive, send)
        finally:
            if gate is not None:
                gate.close()
            principal.reset(context)
            session_identity.reset(session_context)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


def attach_auth(app, store):
    auth = Auth(store, SecuritySettings.environment())
    app.state.auth = auth

    @app.exception_handler(RequestValidationError)
    async def safe_validation(request: Request, error: RequestValidationError):
        if request.url.path.startswith(("/api/auth/", "/api/online/")):
            # Pydantic's default errors include raw inputs, potentially passwords.
            return JSONResponse(
                {
                    "detail": "Invalid login request."
                    if request.url.path == "/api/auth/login"
                    else "Invalid online request."
                    if request.url.path.startswith("/api/online/")
                    else "Invalid account request."
                },
                422,
            )
        return await request_validation_exception_handler(request, error)

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response):
        account, token, csrf = auth.login(
            body.identity, body.password, request.client.host if request.client else "unknown"
        )
        auth.revoke(request.cookies.get(COOKIE, ""))
        response.set_cookie(
            COOKIE,
            token,
            max_age=auth.settings.session_seconds,
            httponly=True,
            secure=auth.settings.production,
            samesite="lax",
            path="/",
        )
        return {"account": public_account(account), "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(request: Request):
        account = principal.get()
        assert account is not None
        # Stable across tabs/reloads; never extends the fixed session expiry.
        csrf = digest("csrf:" + request.cookies[COOKIE])
        return {"account": public_account(account), "csrf_token": csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.revoke(request.cookies.get(COOKIE, ""))
        response.delete_cookie(
            COOKIE, path="/", secure=auth.settings.production, httponly=True, samesite="lax"
        )
        return {"logged_out": True}

    app.add_middleware(SecurityBoundary, router=app.router, auth=auth)
    return auth
