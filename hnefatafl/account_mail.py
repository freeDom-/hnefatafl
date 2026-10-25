"""Bounded SMTP configuration and transport; no raw mail/token storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
import os
import re
import smtplib
import ssl
from typing import Any, Protocol
from urllib.parse import urlsplit

from .account_policy import email


@dataclass(frozen=True)
class AccountSettings:
    enabled: bool = False
    public_origin: str = ""
    sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls: str = "starttls"
    smtp_username: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    timeout: int = 10
    max_attempts: int = 4
    retry_seconds: int = 60
    lease_seconds: int = 60
    poll_seconds: int = 5
    queue_limit: int = 1000
    window_seconds: int = 900
    register_ip: int = 5
    register_identity: int = 3
    resend_ip: int = 5
    resend_identity: int = 3
    reset_ip: int = 5
    reset_identity: int = 3
    confirm_ip: int = 20
    confirm_identity: int = 10
    global_requests: int = 600
    global_hashes: int = 100
    global_mail: int = 100
    limit_keys: int = 10000

    def mail_ready(self, production: bool = False) -> bool:
        try:
            url = urlsplit(self.public_origin)
            if url.port is not None and not 1 <= url.port <= 65535:
                return False
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            origin_ok = url.scheme == "https" or (not production and local and url.scheme == "http")
            return bool(
                origin_ok
                and url.hostname
                and not url.username
                and not url.password
                and not url.path
                and not url.query
                and not url.fragment
                and not re.search(r"\s", self.public_origin)
                and email(self.sender) == self.sender
                and self.smtp_host
                and self.smtp_tls in {"starttls", "tls"}
                and bool(self.smtp_username) == bool(self.smtp_password)
                and 1 <= self.smtp_port <= 65535
            )
        except (ValueError, TypeError):
            return False

    @classmethod
    def environment(cls) -> AccountSettings:
        values: dict[str, Any] = {}
        for name, option in cls.__dataclass_fields__.items():
            key = "HNEFATAFL_ACCOUNT_" + name.upper()
            if name == "enabled":
                value = os.environ.get("HNEFATAFL_REGISTRATION_ENABLED", "false")
                if value not in {"true", "false"}:
                    raise ValueError("HNEFATAFL_REGISTRATION_ENABLED must be true or false")
                values[name] = value == "true"
            elif name == "public_origin":
                values[name] = os.environ.get("HNEFATAFL_PUBLIC_URL", "").rstrip("/")
            elif isinstance(option.default, int):
                try:
                    number = int(os.environ.get(key, str(option.default)))
                except ValueError:
                    raise ValueError(f"{key} must be an integer") from None
                if not 1 <= number <= (65535 if name == "smtp_port" else 100000):
                    raise ValueError(f"{key} is outside its supported bounds")
                values[name] = number
            else:
                values[name] = os.environ.get(key, str(option.default))
        settings = cls(**values)
        if settings.timeout > 30 or settings.lease_seconds < settings.timeout * 4:
            raise ValueError("Account mail timeout must be <=30s and lease >=4 times timeout")
        if (
            settings.max_attempts > 10
            or settings.queue_limit > 10000
            or settings.limit_keys > 100000
        ):
            raise ValueError("Account mail/limiter capacity exceeds supported bounds")
        return settings


class MailTransport(Protocol):
    def send(self, recipient: str, purpose: str, link: str) -> None: ...


class SMTPTransport:
    def __init__(self, settings: AccountSettings):
        self.settings = settings

    def send(self, recipient: str, purpose: str, link: str) -> None:
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.sender
        message["To"] = recipient
        message["Subject"] = "Hnefatafl: " + (
            "verify email / E-Mail bestätigen"
            if purpose == "verify"
            else "reset password / Passwort zurücksetzen"
        )
        message.set_content(
            "Open this link, then explicitly confirm. Only the newest link works.\n"
            "Öffne den Link und bestätige ausdrücklich. Nur der neueste Link ist gültig.\n\n"
            + link
            + "\n\nIf you did not request this, ignore this message.\n"
            "Falls du dies nicht angefordert hast, ignoriere diese Nachricht.\n"
        )
        context = ssl.create_default_context()
        client = (
            smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.timeout, context=context
            )
            if settings.smtp_tls == "tls"
            else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.timeout)
        )
        with client as smtp:
            if settings.smtp_tls == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message, from_addr=settings.sender, to_addrs=[recipient])
