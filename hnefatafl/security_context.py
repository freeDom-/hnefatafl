"""Trusted request identity, propagated into FastAPI's synchronous thread pool."""

from contextvars import ContextVar

PERMISSIONS = {
    "USER": frozenset({"play", "profile"}),
    "ADMIN": frozenset(
        {"play", "profile", "training", "tournaments", "models", "accounts", "all_data"}
    ),
}
principal: ContextVar[dict | None] = ContextVar("principal", default=None)
# Trusted middleware context; domain mutations reload this session under their
# write transaction instead of trusting an account snapshot read before the lock.
session_identity: ContextVar[str | None] = ContextVar("session_identity", default=None)


def allowed(account: dict | None, permission: str) -> bool:
    return bool(
        account
        and type(account.get("active")) in {bool, int}
        and account.get("active") == 1
        and permission in PERMISSIONS.get(str(account.get("role", "")), ())
    )


def owner_scope() -> str | None:
    account = principal.get()
    return account["id"] if account and not allowed(account, "all_data") else None


def online_eligible(account: dict | None) -> bool:
    """Future online gate only; existing single-player permissions stay unchanged."""
    return bool(
        allowed(account, "play") and account and account.get("email_verified_at") is not None
    )
