"""Explicit public account routes, protected by the existing auth boundary."""

import time
import logging
import os

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .account_mail import AccountSettings
from .registration import Registration


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: StrictStr = Field(min_length=1, max_length=254)


class RegisterRequest(EmailRequest):
    username: StrictStr = Field(min_length=1, max_length=256)
    password: StrictStr = Field(min_length=1, max_length=256)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: StrictStr = Field(min_length=43, max_length=128)


class ResetRequest(TokenRequest):
    password: StrictStr = Field(min_length=1, max_length=256)


def attach_accounts(app, auth, *, settings=None, transport=None, clock=time.time):
    if settings is None:
        try:
            settings = AccountSettings.environment()
        except ValueError:
            logging.getLogger(__name__).warning(
                "Invalid account mail configuration; email delivery and registration are unavailable."
            )
            settings = AccountSettings(
                enabled=os.environ.get("HNEFATAFL_REGISTRATION_ENABLED") == "true"
            )
    service = Registration(auth, settings, transport, clock)
    app.state.registration = service
    auth.account_requests = service.admit_ip
    auth.public_hash_budget = service.admit_hash

    def safely(action):
        try:
            return action()
        except ValueError as error:
            # These are shared policy messages, never raw payloads or SMTP errors.
            message = str(error)
            safe_messages = {
                "Username must contain 2–64 letters, digits, dots, underscores or hyphens",
                "A valid email address is required",
                "This username is unavailable.",
                "Use 12–256 characters with at least four distinct characters.",
                "A new password is required",
            }
            raise HTTPException(
                422, message if message in safe_messages else "Invalid account request."
            ) from None

    @app.get("/api/auth/registration")
    def configuration():
        available = service.settings.enabled and service.ready()
        return {
            "enabled": available,
            "email_available": service.ready(),
            "message": "Registration is available."
            if available
            else "Registration is not available yet.",
        }

    @app.post("/api/auth/register", status_code=202)
    def register(body: RegisterRequest):
        return safely(lambda: service.register(body.username, body.email, body.password))

    @app.post("/api/auth/verification/resend", status_code=202)
    def resend(body: EmailRequest):
        return safely(lambda: service.request_mail("verify", body.email))

    @app.post("/api/auth/verify-email")
    def verify_email(body: TokenRequest):
        return safely(lambda: service.confirm("verify", body.token))

    @app.post("/api/auth/password-reset/request", status_code=202)
    def request_reset(body: EmailRequest):
        return safely(lambda: service.request_mail("reset", body.email))

    @app.post("/api/auth/password-reset/confirm")
    def reset_password(body: ResetRequest):
        return safely(lambda: service.confirm("reset", body.token, body.password))

    return service
