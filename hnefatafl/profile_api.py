"""Owned profile updates and password changes using existing session/mail authority."""

import hmac
import unicodedata
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator

from .auth import password_hash, verify
from .account_policy import BIDI_CONTROLS, MIN_BIRTH_YEAR
from .security_context import session_identity

# ISO 3166-1 alpha-2 (including territories); labels are localized by the browser.
COUNTRY_CODES = tuple(
    """
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO
BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE
DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM
GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO
JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF
MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR
NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE
SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR
TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split()
)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: StrictStr | None = None
    country_code: StrictStr | None = None
    city: StrictStr | None = None
    birth_year: StrictInt | None = None
    show_display_name: StrictBool = False
    show_country: StrictBool = False
    show_city: StrictBool = False
    show_age: StrictBool = False

    @field_validator("display_name", "city")
    @classmethod
    def text(cls, value, info):
        if value is None:
            return None
        value = unicodedata.normalize("NFC", value)
        maximum = 80 if info.field_name == "display_name" else 100
        if any(unicodedata.category(char) == "Cc" or char in BIDI_CONTROLS for char in value):
            raise ValueError("Invalid profile text")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError("Invalid profile text")
        return value or None

    @field_validator("country_code")
    @classmethod
    def country(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip().upper()
        if value not in COUNTRY_CODES:
            raise ValueError("Invalid country code")
        return value

    @field_validator("birth_year")
    @classmethod
    def year(cls, value):
        if value is not None and not MIN_BIRTH_YEAR <= value <= datetime.now(timezone.utc).year:
            raise ValueError("Invalid birth year")
        return value


class ProfileResponse(BaseModel):
    # Response validation must also accept pre-existing optional names as stored.
    display_name: str | None
    country_code: str | None
    city: str | None
    birth_year: int | None
    show_display_name: bool
    show_country: bool
    show_city: bool
    show_age: bool
    username: str
    email: str
    email_verified: bool
    country_codes: list[str]


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: StrictStr
    new_password: StrictStr
    confirm_password: StrictStr

    @field_validator("current_password", "new_password", "confirm_password")
    @classmethod
    def bounded(cls, value):
        if not 1 <= len(value) <= 256:
            raise ValueError("Invalid password length")
        return value


class PasswordResult(BaseModel):
    message: str


def projection(account) -> dict:
    fields = ProfileUpdate.model_fields
    return {
        **{key: bool(account[key]) if key.startswith("show_") else account[key] for key in fields},
        "username": account["username"],
        "email": account["email"],
        "email_verified": account["email_verified_at"] is not None,
        "country_codes": list(COUNTRY_CODES),
    }


def attach_profiles(app, auth, registration):
    @app.get("/api/profile", response_model=ProfileResponse)
    def profile():
        with auth.store.connection() as c:
            c.execute("BEGIN")
            return projection(auth.current_account(c))

    @app.patch("/api/profile", response_model=ProfileResponse)
    def update_profile(body: ProfileUpdate):
        # Only explicitly supplied allowlisted fields change; one transaction also
        # rechecks session revocation, activation and ownership after acquiring the lock.
        changes = body.model_dump(exclude_unset=True)
        with auth.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            account = auth.current_account(c)
            if changes:
                c.execute(
                    f"UPDATE accounts SET {','.join(key + '=?' for key in changes)} WHERE id=?",
                    (*changes.values(), account["id"]),
                )
            return projection(auth.current_account(c))

    @app.post("/api/profile/password", response_model=PasswordResult)
    def change_password(body: PasswordChange, request: Request):
        registration.admit_ip(
            "password-change", request.client.host if request.client else "unknown"
        )
        with auth.store.connection() as c:
            c.execute("BEGIN")
            account = auth.current_account(c)
        # Persistent per-account/IP and global hashing budgets are shared with
        # existing sensitive account actions. Denials do not revoke the session.
        registration.admit_identity("password-change", account["id"], hashing=True)
        if body.new_password != body.confirm_password:
            raise HTTPException(422, "Passwords do not match.")
        if not verify(body.current_password, account["password_hash"]):
            raise HTTPException(422, "Current password is incorrect.")
        registration.admit_hash()
        try:
            encoded = password_hash(body.new_password)
        except ValueError:
            raise HTTPException(
                422, "Use 12–256 characters with at least four distinct characters."
            ) from None
        with auth.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            current = auth.current_account(c)
            if not hmac.compare_digest(current["password_hash"], account["password_hash"]):
                raise HTTPException(409, "Account changed. Please try again.")
            c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (encoded, current["id"]))
            c.execute(
                "DELETE FROM sessions WHERE account_id=? AND token_hash<>?",
                (current["id"], session_identity.get()),
            )
            for table in ("account_tokens", "account_mail"):
                c.execute(
                    f"DELETE FROM {table} WHERE account_id=? AND purpose='reset'", (current["id"],)
                )
        return {"message": "Password changed. Other sessions have been signed out."}
