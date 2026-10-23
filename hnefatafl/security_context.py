"""Trusted request identity, propagated into FastAPI's synchronous thread pool."""

from contextvars import ContextVar

PERMISSIONS = {
    "USER": frozenset({"play", "profile"}),
    "ADMIN": frozenset(
        {"play", "profile", "training", "tournaments", "models", "accounts", "all_data"}
    ),
}
principal: ContextVar[dict | None] = ContextVar("principal", default=None)


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
