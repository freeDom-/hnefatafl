"""Account validation and public identity projections; passwords are never normalized."""

import json
import re
import unicodedata
from datetime import date, datetime, timezone

MIN_BIRTH_YEAR = 1900
LEGACY_PRIVATE_FIELDS = frozenset({"display_name", "country_code", "city"})
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
    """Populated names are public, except unreconfirmed legacy-private values."""
    return str(
        (account["display_name"] if "display_name" not in unpublished_fields(account) else None)
        or account["username"]
    )[:80]


def unpublished_fields(account) -> frozenset[str]:
    """One-time migration exceptions, never client-editable sharing preferences."""
    try:
        fields = json.loads(account["profile_private_fields"])
        if not isinstance(fields, list) or any(
            field not in LEGACY_PRIVATE_FIELDS for field in fields
        ):
            return LEGACY_PRIVATE_FIELDS
        return frozenset(fields)
    except (ValueError, TypeError):
        return LEGACY_PRIVATE_FIELDS


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def birth_date(value: str | None, today: date | None = None) -> str | None:
    """Only complete ISO calendar dates; empty values explicitly clear a date."""
    if value is None or value == "":
        return None
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if not date(MIN_BIRTH_YEAR, 1, 1) <= parsed <= (today or utc_today()):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Invalid date of birth") from None
    return parsed.isoformat()


def public_profile_fields(account, today: date) -> dict:
    """Return eligible optional fields; full birth dates never enter public data."""
    pending = unpublished_fields(account)
    result = {
        field: account[field]
        for field in ("country_code", "city")
        if account[field] and field not in pending
    }
    try:
        value = birth_date(account["date_of_birth"], today)
    except ValueError:
        value = None
    if value:
        born = date.fromisoformat(value)
        # February 29 advances on March 1 in non-leap years.
        result["age"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return result


def attributed_name(name: str | None, username: str | None) -> str | None:
    """Label a non-unique shared name with its public canonical account identity."""
    if name and username and name != username:
        return f"{name} (@{username})"
    return name


def case_fold(value: str | None) -> str | None:
    """SQLite adapter for Unicode-aware case-insensitive substring matching.

    SQLite's built-in ``lower()`` folds ASCII only, so a published name such as
    ``JÖRG MÜLLER`` never matches the query ``müller``. Python's ``casefold()``
    applies full Unicode case folding, including ``ß`` -> ``ss``.
    """
    return None if value is None else str(value).casefold()


def public_account_name(username, display_name, profile_private_fields):
    """SQLite adapter for the same public naming policy used by API projections."""
    if username is None:
        return None
    return public_name(
        {
            "username": username,
            "display_name": display_name,
            "profile_private_fields": profile_private_fields,
        }
    )


def public_identity(account) -> dict:
    """Only public attribution; never resurrect a missing account's saved profile."""
    if account is None or account["username"] is None:
        return {"name": ""}
    return {"name": public_name(account), "username": account["username"]}


def resolve_accounts(connection, account_ids) -> dict[str, dict]:
    """Batch authoritative seat IDs; callers remain responsible for record access."""
    identifiers = list(dict.fromkeys(identifier for identifier in account_ids if identifier))
    if not identifiers:
        return {}
    return {
        row["id"]: public_identity(row)
        for row in connection.execute(
            "SELECT id,username,display_name,profile_private_fields FROM accounts "
            "WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(identifiers),),
        )
    }


def game_accounts(connection, game_ids) -> dict[str, dict]:
    """Resolve owners of already-authorized games once, independently of saved JSON."""
    identifiers = list(dict.fromkeys(game_ids))
    if not identifiers:
        return {}
    return {
        row["id"]: {"owner_account_id": row["owner_account_id"], **public_identity(row)}
        for row in connection.execute(
            "SELECT g.id,g.owner_account_id,a.username,a.display_name,a.profile_private_fields "
            "FROM games g LEFT JOIN accounts a ON a.id=g.owner_account_id "
            "WHERE g.id IN (SELECT value FROM json_each(?))",
            (json.dumps(identifiers),),
        )
    }


def game_players(game, owners) -> dict[str, dict]:
    """Project verified human seats without modifying persisted gameplay or AI snapshots."""
    owner = owners.get(game["id"], {})
    players = {}
    for side, recorded in game["players"].items():
        player = dict(recorded)
        if player.get("kind") == "human":
            player.pop("username", None)
            identifier = player.get("account_id")
            if (
                isinstance(identifier, str)
                and identifier
                and identifier == owner.get("owner_account_id")
            ):
                player.update(
                    {key: value for key, value in owner.items() if key != "owner_account_id"}
                )
        players[side] = player
    return players


def account_player_sql(side: str) -> tuple[str, str]:
    """Summary-only SQL equivalent of game_players; aliases games and a are the owner join."""
    if side not in ("attackers", "defenders"):
        raise ValueError("Unknown player side")
    path = f"$.players.{side}"
    linked = (
        f"json_extract(games.data,'{path}.kind')='human' "
        f"AND json_extract(games.data,'{path}.account_id')=games.owner_account_id"
    )
    name = (
        f"CASE WHEN {linked} THEN public_account_name(a.username,a.display_name,a.profile_private_fields) "
        f"ELSE substr(json_extract(games.data,'{path}.name'),1,80) END"
    )
    username = f"CASE WHEN {linked} THEN a.username END"
    return name, username
