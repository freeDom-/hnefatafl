"""Registration, single-use tokens, durable limiter and leased mail delivery."""

from __future__ import annotations

import fcntl
import secrets
import sqlite3
import threading
import time
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException

from .account_mail import AccountSettings, MailTransport, SMTPTransport
from .account_policy import email, normalize, username
from .auth import Auth, digest, password_hash

ACKNOWLEDGMENT = "If the address is eligible, an email will arrive. Only the newest link works."
UNAVAILABLE = "Account email service is unavailable. Please try later."
INVALID_LINK = "This link is invalid, expired or replaced. Request a new link."


class Registration:
    def __init__(
        self,
        auth: Auth,
        settings: AccountSettings,
        transport: MailTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.auth, self.store, self.settings = auth, auth.store, settings
        self.transport = transport or SMTPTransport(settings)
        self.clock = clock
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def ready(self) -> bool:
        return self.settings.mail_ready(self.auth.settings.production)

    def require_mail(self, *, registering: bool = False) -> None:
        if registering and not self.settings.enabled:
            raise HTTPException(403, "Registration is not available yet.")
        if not self.ready():
            raise HTTPException(503, UNAVAILABLE)

    def _limits(self, c, budgets: list[tuple[str, int]], now: float) -> None:
        c.execute(
            "DELETE FROM account_limits WHERE started<=?", (now - self.settings.window_seconds,)
        )
        missing = 0
        for key, maximum in budgets:
            row = c.execute("SELECT attempts FROM account_limits WHERE key=?", (key,)).fetchone()
            missing += row is None
            if row and row[0] >= maximum:
                raise HTTPException(429, "Too many account requests. Try again later.")
        if (
            c.execute("SELECT COUNT(*) FROM account_limits").fetchone()[0] + missing
            > self.settings.limit_keys
        ):
            raise HTTPException(429, "Too many account requests. Try again later.")
        for key, _ in budgets:
            c.execute(
                "INSERT INTO account_limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1",
                (key, now),
            )

    def admit_ip(self, action: str, address: str) -> None:
        group = action if action in {"register", "resend", "reset"} else "confirm"
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._limits(
                c,
                [
                    ("requests", self.settings.global_requests),
                    (group + ":ip:" + digest(address), getattr(self.settings, group + "_ip")),
                ],
                self.clock(),
            )

    def admit_hash(self, connection: sqlite3.Connection | None = None) -> None:
        if connection is not None:
            self._limits(connection, [("hashes", self.settings.global_hashes)], self.clock())
            return
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._limits(c, [("hashes", self.settings.global_hashes)], self.clock())

    def admit_identity(self, action: str, identity: str, *, hashing: bool = False) -> None:
        group = action if action in {"register", "resend", "reset"} else "confirm"
        budgets = [
            (
                group + ":identity:" + digest(normalize(identity)),
                getattr(self.settings, group + "_identity"),
            )
        ]
        if hashing:
            budgets.append(("hashes", self.settings.global_hashes))
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._limits(c, budgets, self.clock())

    def _cleanup(self, c, now: float) -> None:
        c.execute("DELETE FROM account_tokens WHERE expires<=?", (now,))
        c.execute(
            "DELETE FROM account_mail WHERE status IN ('sent','failed') AND created_at<=?",
            (now - 86400,),
        )

    def _queue(self, c, account: dict, purpose: str, now: float) -> None:
        self._cleanup(c, now)
        existing = c.execute(
            "SELECT 1 FROM account_mail WHERE account_id=? AND purpose=?", (account["id"], purpose)
        ).fetchone()
        if (
            not existing
            and c.execute("SELECT COUNT(*) FROM account_mail").fetchone()[0]
            >= self.settings.queue_limit
        ):
            raise HTTPException(503, UNAVAILABLE)
        c.execute(
            "DELETE FROM account_tokens WHERE account_id=? AND purpose=?", (account["id"], purpose)
        )
        c.execute(
            "INSERT INTO account_mail(account_id,purpose,generation,email_version,address_hash,status,created_at,next_attempt) VALUES(?,?,?,?,?,'pending',?,?) "
            "ON CONFLICT(account_id,purpose) DO UPDATE SET generation=excluded.generation,email_version=excluded.email_version,address_hash=excluded.address_hash,status='pending',attempts=0,created_at=excluded.created_at,next_attempt=excluded.next_attempt,lease_id=NULL,lease_until=NULL",
            (
                account["id"],
                purpose,
                uuid4().hex,
                account["email_version"],
                digest(account["email"]),
                now,
                now,
            ),
        )

    def _capacity(self, c, now: float) -> None:
        self._cleanup(c, now)
        if (
            c.execute("SELECT COUNT(*) FROM account_mail").fetchone()[0]
            >= self.settings.queue_limit
        ):
            raise HTTPException(503, UNAVAILABLE)

    def register(self, name: str, address: str, password: str) -> dict:
        self.require_mail(registering=True)
        self.admit_identity("register", address, hashing=True)
        name, address = username(name, public=True), email(address)
        encoded = password_hash(password)  # Same work even for an already registered email.
        now, identifier = self.clock(), uuid4().hex
        try:
            with self.store.connection() as c:
                c.execute("BEGIN IMMEDIATE")
                self._capacity(c, now)
                if c.execute(
                    "SELECT 1 FROM accounts WHERE username=? COLLATE NOCASE", (name,)
                ).fetchone():
                    raise HTTPException(409, "This username is unavailable.")
                if c.execute("SELECT 1 FROM accounts WHERE email=?", (address,)).fetchone():
                    return {"message": ACKNOWLEDGMENT}
                c.execute(
                    "INSERT INTO accounts(id,username,email,password_hash,role,active,created_at,display_name) VALUES(?,?,?,?,'USER',1,?,?)",
                    (identifier, name, address, encoded, now, name),
                )
                account = dict(
                    c.execute("SELECT * FROM accounts WHERE id=?", (identifier,)).fetchone()
                )
                self._queue(c, account, "verify", now)
        except sqlite3.IntegrityError:
            # The UNIQUE constraints, not only the precheck, arbitrate racing inserts.
            with self.store.connection() as c:
                collision = c.execute(
                    "SELECT 1 FROM accounts WHERE username=? COLLATE NOCASE", (name,)
                ).fetchone()
            if collision:
                raise HTTPException(409, "This username is unavailable.") from None
            with self.store.connection() as c:
                email_collision = c.execute(
                    "SELECT 1 FROM accounts WHERE email=?", (address,)
                ).fetchone()
            if not email_collision:
                raise HTTPException(503, UNAVAILABLE) from None
        return {"message": ACKNOWLEDGMENT}

    def request_mail(self, purpose: str, address: str) -> dict:
        self.require_mail()
        if purpose not in {"verify", "reset"}:
            raise ValueError("Unsupported token purpose")
        self.admit_identity("resend" if purpose == "verify" else "reset", address)
        address = email(address)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._capacity(c, self.clock())
            row = c.execute("SELECT * FROM accounts WHERE email=?", (address,)).fetchone()
            if (
                row
                and row["active"] == 1
                and (purpose == "reset" or row["email_verified_at"] is None)
            ):
                self._queue(c, dict(row), purpose, self.clock())
        return {"message": ACKNOWLEDGMENT}

    def confirm(self, purpose: str, token: str, password: str | None = None) -> dict:
        if purpose not in {"verify", "reset"}:
            raise ValueError("Unsupported token purpose")
        if purpose == "verify":
            self.require_mail()
        self.admit_identity("confirm", token, hashing=purpose == "reset")
        encoded = None
        if purpose == "reset":
            if password is None:
                raise ValueError("A new password is required")
            encoded = password_hash(password)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self.clock()
            row = c.execute(
                "SELECT t.*,a.active,a.email AS current_address,a.email_version AS current_version FROM account_tokens t JOIN accounts a ON a.id=t.account_id WHERE t.token_hash=? AND t.purpose=?",
                (digest(token), purpose),
            ).fetchone()
            if (
                not row
                or row["consumed_at"] is not None
                or row["expires"] <= now
                or row["active"] != 1
                or row["email_version"] != row["current_version"]
                or row["address_hash"] != digest(row["current_address"])
            ):
                raise HTTPException(400, INVALID_LINK)
            identifier = row["account_id"]
            c.execute(
                "UPDATE account_tokens SET consumed_at=? WHERE token_hash=?", (now, digest(token))
            )
            if purpose == "verify":
                c.execute("UPDATE accounts SET email_verified_at=? WHERE id=?", (now, identifier))
            else:
                c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (encoded, identifier))
                c.execute("DELETE FROM sessions WHERE account_id=?", (identifier,))
            c.execute(
                "DELETE FROM account_mail WHERE account_id=? AND purpose=?", (identifier, purpose)
            )
        return {
            "message": "Email verified. You can log in."
            if purpose == "verify"
            else "Password reset. Log in with your new password."
        }

    def process_one(self) -> bool:
        if not self.ready():
            return False
        # Background writes obey the same deployment freeze as HTTP mutations.
        with self.auth.gate_path.open("rb") as gate:
            try:
                fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return self._deliver_one()

    def _deliver_one(self) -> bool:
        settings = self.settings
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            now = self.clock()
            self._cleanup(c, now)
            row = c.execute(
                "SELECT m.*,a.email,a.active,a.email_verified_at,a.email_version AS current_version FROM account_mail m JOIN accounts a ON a.id=m.account_id "
                "WHERE (m.status='pending' AND m.next_attempt<=?) OR (m.status='leased' AND m.lease_until<=?) ORDER BY m.created_at,m.account_id,m.purpose DESC LIMIT 1",
                (now, now),
            ).fetchone()
            if not row:
                return False
            mail = dict(row)
            identifier, purpose = mail["account_id"], mail["purpose"]
            if (
                mail["attempts"] >= settings.max_attempts
                or mail["active"] != 1
                or mail["email_version"] != mail["current_version"]
                or mail["address_hash"] != digest(mail["email"])
                or (purpose == "verify" and mail["email_verified_at"] is not None)
            ):
                c.execute(
                    "UPDATE account_mail SET status='failed',lease_id=NULL,lease_until=NULL WHERE account_id=? AND purpose=?",
                    (identifier, purpose),
                )
                c.execute(
                    "DELETE FROM account_tokens WHERE account_id=? AND purpose=?",
                    (identifier, purpose),
                )
                return True
            try:
                self._limits(c, [("mail", settings.global_mail)], now)
            except HTTPException:
                return False
            lease, token = uuid4().hex, secrets.token_urlsafe(32)
            c.execute(
                "UPDATE account_mail SET status='leased',attempts=attempts+1,lease_id=?,lease_until=? WHERE account_id=? AND purpose=?",
                (lease, now + settings.lease_seconds, identifier, purpose),
            )
            c.execute(
                "INSERT INTO account_tokens VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(account_id,purpose) DO UPDATE SET token_hash=excluded.token_hash,email_version=excluded.email_version,address_hash=excluded.address_hash,created_at=excluded.created_at,expires=excluded.expires,consumed_at=NULL",
                (
                    identifier,
                    purpose,
                    digest(token),
                    mail["email_version"],
                    mail["address_hash"],
                    now,
                    now + (86400 if purpose == "verify" else 1800),
                ),
            )
        link = (
            settings.public_origin
            + ("/verify-email" if purpose == "verify" else "/reset-password")
            + "#token="
            + token
        )
        succeeded = True
        try:
            self.transport.send(mail["email"], purpose, link)
        except Exception:
            # Never log an SMTP exception: it can contain recipients, credentials or mail.
            succeeded = False
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute(
                "SELECT * FROM account_mail WHERE account_id=? AND purpose=? AND lease_id=? AND generation=?",
                (identifier, purpose, lease, mail["generation"]),
            ).fetchone()
            if current:
                terminal = succeeded or current["attempts"] >= settings.max_attempts
                c.execute(
                    "UPDATE account_mail SET status=?,next_attempt=?,lease_id=NULL,lease_until=NULL WHERE account_id=? AND purpose=? AND lease_id=?",
                    (
                        "sent" if succeeded else "failed" if terminal else "pending",
                        self.clock()
                        + min(settings.retry_seconds * 2 ** (current["attempts"] - 1), 3600),
                        identifier,
                        purpose,
                        lease,
                    ),
                )
                if not succeeded:
                    c.execute(
                        "DELETE FROM account_tokens WHERE account_id=? AND purpose=? AND token_hash=?",
                        (identifier, purpose, digest(token)),
                    )
        return True

    def start(self) -> None:
        if not self.ready():
            return
        self.stop.clear()

        def run():
            while not self.stop.is_set():
                try:
                    self.process_one()
                except Exception:
                    # Retry durable work next poll; no exception/secret-bearing payload logs.
                    pass
                self.stop.wait(self.settings.poll_seconds)

        self.thread = threading.Thread(target=run, name="account-mail", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=self.settings.timeout * 4 + 2)
