# Account menu and profile

[Documentation index](README.md) · [Accounts and security](SECURITY.md) · [Appearance](PERSONALIZATION.md#color-tokens-and-boards)

The header keeps the existing English/German language selector immediately beside
the username menu. **My Profile** opens `/profile`; **Log out** uses the existing
session logout. The menu supports click/touch, outside dismissal, Escape, arrows,
Home/End and normal Tab navigation. Long names are truncated in the header and
remain available to assistive technology. Anonymous authentication pages retain
their language selector and existing navigation.

## Personal details and sharing

All details are optional. Display names allow 80 Unicode characters, cities 100;
both are trimmed and normalized to NFC, and control characters are rejected.
Countries use the API's ISO 3166-1 alpha-2 catalog, with browser-localized labels.
Birth years are integers from 1900 through the current UTC year. Empty text and
explicit null clear optional values. Login usernames, account IDs, roles,
verification status, ownership and training records are unaffected.

Each of display name, country, city and age has its own sharing checkbox, off by
default. The leaderboard uses the username unless display-name sharing is enabled
and the name is nonempty. Shared names also appear in current game/history views.
Existing saved game snapshots are retained, including names already saved there.
The compact Dashboard leaderboard keeps its four columns; the full leaderboard
adds country, city and age range. Ranking, filters, tie-breaking and pagination
are unchanged.

Birth years are never returned in leaderboard entries. Explicitly shared age is
an inclusive range from `max(0, current UTC year − birth year − 1)` to
`max(0, current UTC year − birth year)`, because the birthday is unknown. The same
projection applies to leaderboard entries and the caller's `me` standing. Private
country/city/age keys are absent, including immediately after sharing is disabled.
Responses retain the existing `Cache-Control: no-store`; no leaderboard cache or
per-row profile query is introduced. Historical results continue to use account IDs.

## Account security

The profile shows the authenticated email and verification status. Unverified
accounts can use the existing verification resend endpoint; the Dashboard warning
links to `/profile#email-verification`, whose section receives focus. Existing
single-use, expiring tokens, generic acknowledgments, durable SMTP delivery and
rate limits are reused without exposing links or tokens in the profile API.
Mail configuration remains necessary; no email-delivery success is assumed.

Password changes require the current password, a matching confirmation and the
existing 12–256 character/four-distinct-character policy. They use the existing
scrypt implementation. Successful changes retain the submitting session, revoke
all other sessions and invalidate pending password-reset tokens/mail in the same
transaction. Other profile values and verification state are untouched. The
existing reset/login hash recheck still prevents an old-password login race.

Password changes share the existing durable `CONFIRM_IP`, `CONFIRM_IDENTITY`,
global request and global hash budgets. Identity is the authenticated account ID;
the default limits are 20 per IP and 10 per account per 900 seconds. A successful
change charges both verification and replacement hashing against the global
budget. Invalid current passwords return a form error without ending the session.

## API and persistence

| Method/path | Behavior |
| --- | --- |
| `GET /api/profile` | Own optional fields, visibility flags, username, email, verification status and country catalog |
| `PATCH /api/profile` | Atomic partial update of optional details/visibility only; omitted fields stay unchanged |
| `POST /api/profile/password` | `current_password`, `new_password`, `confirm_password`; safe acknowledgment only |

These routes use the existing `profile` permission for both ordinary users and
administrators. There is no account-ID parameter or administrative override for
editing another profile. Strict request allowlists reject privileged/unknown
fields. Mutations retain CSRF, origin checks and deployment freeze protection,
and are limited to 8192 bytes. Validation responses suppress raw inputs. Session,
activation and permissions are reloaded inside the update transaction; password
replacement additionally compares the previously verified hash under the lock.

Schema **14** adds nullable country/city/birth year and four false-by-default
visibility flags to `accounts`, reusing its existing nullable display name. It
preserves existing accounts, roles, hashes, verification, sessions and ownership.
New accounts no longer infer a display name from the username. Restarting is
idempotent. The clock migration's original schema-13 threshold stays unchanged,
so this profile upgrade cannot enable a paused timeout gate.

Take a stopped-writer backup using the normal [deployment procedure](DEPLOYMENT.md#data-and-configuration).
Older code refuses schema 14. Automatic restore requires matching account columns
and overall schema; it preserves current profiles/privacy settings with the live
account state. An older snapshot requires manual reconciliation, not an automatic
schema downgrade. No production migration or deployment is performed by tests.

## Device preferences and implementation boundaries

Appearance controls reuse the existing provider, catalogs, CSS and startup script,
now embedded directly in the profile. System, Light, Dark and Wood, piece themes,
board themes and interface scale remain supported. Language selection remains in
the header, with no duplicate on the profile. Both settings apply immediately.

Appearance stays in `hnefatafl-appearance` and language in `hnefatafl-language` on
this browser, including across login/logout. Server persistence is deliberately
not added: the existing startup script and anonymous behavior already use device
preferences, and a second authority would require account-switch and pre-paint
synchronization. Existing defaults, fallback behavior and blocked-storage support
remain in place. These preferences do not synchronize between devices.

`profile_api.py` owns explicit profile/password schemas, validation and endpoints;
`profile_migrations.py` adds account columns. Existing account/mail, authentication,
rating, routing, localization, notifications and theme mechanisms are reused.
`ProfilePage` loads optional data only while mounted; security and appearance
remain usable if that load fails. Failed saves retain edits and allow retry;
in-flight actions disable duplicate submissions and abort when leaving the page.

Focused regression coverage lives in `tests/test_profile.py`, account-menu/profile
component tests, the registration browser profile flows, and the personal-browser
leaderboard sharing test. Existing authentication, appearance, tournament and
localization browser tests exercise their new entry points. Browser evidence uses
Chromium desktop/phone emulation and a fake mailbox, not physical devices or live
SMTP delivery.
