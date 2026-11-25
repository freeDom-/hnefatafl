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


def online_ineligibility(account: dict | None) -> str | None:
    """The admission policy shared by online play and League operations."""
    if account is None:
        return "ineligible"
    active = account.get("active")
    if type(active) not in {bool, int} or active != 1:
        return "disabled"
    if not allowed(account, "play"):
        return "ineligible"
    if account.get("email_verified_at") is None:
        return "unverified"
    return None


def online_eligible(account: dict | None) -> bool:
    return online_ineligibility(account) is None
