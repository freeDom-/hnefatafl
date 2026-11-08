"""Shared account normalization; passwords are deliberately never normalized."""

import re
import unicodedata

MIN_BIRTH_YEAR = 1900
# Unicode Bidi_Control plus the deprecated directional formatting controls.
# Joiners, variation selectors and ordinary combining marks remain permitted.
BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f"
    + "".join(map(chr, range(0x202A, 0x202F)))
    + "".join(map(chr, range(0x2066, 0x2070)))
)

RESERVED_NAMES = frozenset(
    {
        "admin",
        "administrator",
        "support",
        "system",
        "moderator",
        "api",
        "login",
        "register",
        "hnefatafl",
    }
)


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def username(value: str, *, public: bool = False) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{1,63}", value):
        raise ValueError("Username must contain 2–64 letters, digits, dots, underscores or hyphens")
    if public and normalize(value) in RESERVED_NAMES:
        raise ValueError("This username is unavailable.")
    return value


def email(value: str) -> str:
    value = normalize(value)
    if len(value) > 254 or not re.fullmatch(
        r"[^\s@<>(),;:\"\\\x00-\x1f\x7f]+@[^\s@<>(),;:\"\\\x00-\x1f\x7f]+\.[^\s@<>(),;:\"\\\x00-\x1f\x7f]+",
        value,
    ):
        raise ValueError("A valid email address is required")
    return value


def validate_password(value: str) -> None:
    if len(value) < 12 or len(value) > 256 or len(set(value)) < 4:
        raise ValueError("Use 12–256 characters with at least four distinct characters.")


def public_name(account) -> str:
    """Account IDs stay stable; an editable name requires explicit sharing."""
    return str(
        (account["display_name"] if account["show_display_name"] else None) or account["username"]
    )[:80]


def attributed_name(name: str | None, username: str | None) -> str | None:
    """Label a non-unique shared name with its public canonical account identity."""
    if name and username and name != username:
        return f"{name} (@{username})"
    return name
