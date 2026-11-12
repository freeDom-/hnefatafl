# Account menu and profile

[Documentation index](README.md) · [Accounts and security](SECURITY.md) · [Appearance](PERSONALIZATION.md#color-tokens-and-boards)

The header keeps the existing English/German language selector immediately beside
the username menu. **My Profile** opens `/profile`; **Log out** uses the existing
session logout. The menu supports click/touch, outside dismissal, Escape, arrows,
Home/End, Space/Enter activation and Tab navigation. Long names are truncated in
the desktop header and remain available to assistive technology. In a compact
header the trigger hides the username; opening the same dropdown shows the full
name as non-interactive, wrapping identity text above its two actions. Desktop
does not repeat this label. Anonymous authentication pages retain their language
selector and existing navigation.

## Personal details and sharing

All details are optional. Display names allow 80 Unicode code points, cities 100,
counted after NFC normalization and trimming. Native HTML length constraints count
UTF-16 code units, so these fields deliberately use the shared code-point validator
on submission instead of `maxlength`. Typing and pasting retain the full input;
oversized values produce localized errors rather than silent truncation. Emoji
joiners and combining marks count as code points, not as whole visible symbols.
C0/C1 control characters, Unicode
`Bidi_Control` characters and deprecated directional formatting controls are
rejected before trimming, including embedding, override and isolate sequences.
Ordinary Arabic/Hebrew/Persian text, combining marks, variation selectors and emoji
joiners remain supported. Frontend/backend tests share the same multilingual and
unsafe-control corpus. Error messages are localized and do not echo rejected text.
Countries use the API's ISO 3166-1 alpha-2 catalog, with browser-localized labels.
Birth dates are optional strict ISO calendar dates (`YYYY-MM-DD`), from
1900-01-01 through the current UTC date. Invalid dates, incomplete dates, times
and future dates are rejected. Empty text and
explicit null clear optional values. Login usernames, account IDs, roles,
verification status, ownership and training records are unaffected.

A populated optional field is public; clearing it withdraws its public value.
There are no sharing checkboxes or permanent visibility section. A display name
is shown with **(@username)** attribution, country and city appear in the detailed
leaderboard, and a complete date of birth produces only an integer age. The full
birth date stays private. Display names remain editable and non-unique; no role is
inferred from a name. Equal display name/username values omit duplicate attribution.

Previously private values are a transition exception: they remain unpublished
until that specific field is edited or explicitly reconfirmed. Only such fields
show a temporary **Publish saved … on save** action beside their existing input.
It queues that one field for the normal Save Profile submission. Unrelated edits
and Save without edits do not publish other saved private values. These actions
disappear after confirmation or clearing; they are not permanent sharing controls.

Leaderboard entries (including `me`), online participants and account-linked human
game/replay players include the public `username` alongside their current `name`.
Single-player history summaries include the same optional attribution. Older
responses without that field still render. Hall and online personal summaries
retain their existing string fields and append
the canonical username when needed. History's administrator player filter also
matches that username, the visible `@username` attribution, and a copied complete
**Display Name (@username)** label, with the same literal substring matching and
authorization rules. The search and response projection reuse the same formatter;
no extra per-record database reads are added. The player query allows 148 Unicode
code points (80 for the display name, 64 for the username and four for attribution
punctuation). The input uses that same code-point bound instead of UTF-16
`maxlength`, so a complete Unicode label can be pasted intact. Current private
display names do not enter the search projection.
These read projections do not modify ratings, ownership, permissions or records.
The compact Dashboard leaderboard keeps its four columns; the full leaderboard
adds country, city and age. Ranking, filters, tie-breaking and pagination
are unchanged.

## Names in games and history

New account-backed human seats persist the immutable `accounts.id` and canonical
username, never a mutable display-name snapshot. At read time, games, lists,
statistics, replays and history resolve the current eligible public name.
Changing **Thor** to **Freya** updates existing and active games on their next
read; clearing the name restores the canonical username. Unconfirmed legacy-private
names continue to use the canonical username until explicitly updated.
Old saved display names on a verified account link are neither presented nor
searched. Account identity and authorization never depend on the displayed text.

A single-player link is trusted only when its human `account_id` equals the
persisted game owner. Online seats use their authoritative account IDs. Legacy
human seats without a verified link keep their saved names; matching a username
or display name never establishes a link. AI, model and tournament provenance
remain historical snapshots. A linked account that is unavailable displays the
existing localized **Unassigned** fallback rather than resurrecting a saved
private display name.

Profile saves update account columns only: they never rewrite game JSON, moves,
analysis, results, seeds, timestamps or versions. Existing linked game blobs stay
byte-for-byte unchanged. The schema-15 transition changes account metadata only;
matching-schema backup/restore retains current live profile and publication state. List projections batch account resolution,
while paginated history joins accounts without loading full game histories.

Navigation reloads the current identity. Existing dashboard, lobby, leaderboard
and replay read cycles also wake on a successful profile save. A mounted
single-player game refreshes identity on focus, visibility or reconnect without
adding continuous polling; a background identity read cannot replace newer game
state. Another browser session's profile changes appear on the next normal read
or focus refresh. A disconnected client retains its last successful view until
it can read again.

## Exact age and private birth dates

The detailed leaderboard returns `age` as an integer number of completed years,
calculated on the server using the current UTC date. The birthday must have
occurred this year before the age advances. February 29 birthdays advance on
**March 1** in non-leap years and on February 29 in leap years. A person born today
has age `0`; a missing, invalid or cleared full date produces no age field.

The same UTC date is sampled once for entries and the caller's `me` standing, so
crossing midnight during a response cannot produce inconsistent ages. Neither
birth dates nor birth years enter public leaderboard, game or history responses.
The old range response is replaced by `age`; clients should deploy together with
the updated backend. The compact Dashboard leaderboard retains its four columns.

A historical birth year alone cannot establish an exact age. It remains private,
available to the owner as read-only `legacy_birth_year`, until a full-date edit or
explicit date clear supersedes it. A temporary input hint identifies the saved
year without inventing a birthday. Entering the full date publishes age under the
new policy; clearing it withdraws age immediately on the next fresh request.
Responses retain `Cache-Control: no-store`; no per-player account lookup or
leaderboard cache is introduced. Ratings and historical ownership use account IDs.

## Account security

The profile shows the authenticated email and verification status. Unverified
accounts can use the existing verification resend endpoint; the Dashboard warning
links to `/profile#email-verification`, whose section receives focus. Existing
single-use, expiring tokens, generic acknowledgments, durable SMTP delivery and
rate limits are reused without exposing links or tokens in the profile API.
Mail configuration remains necessary; no email-delivery success is assumed.

Verification lives in the existing Auth context. Successful private profile reads
and saves merge their verification observation into that context without another
`/auth/me` request. Profile and authentication reads share request-order revisions:
an earlier read cannot replace a later successful observation. Failed reads do not
advance that revision or discard a known account; expired/revoked authentication
(401/403) still clears the session. Generation/account guards and aborts prevent
late responses crossing logout, account switches or page unmounting. Successful
email confirmation emits the existing `permissions-changed` event. Invalidations
while an auth refresh is active coalesce into one follow-up read. Existing focus
and periodic auth refresh continue to observe confirmation in another session;
no new polling timer or verification store is introduced.

Password changes require the current password, a matching confirmation and the
existing 12–256 character/four-distinct-character policy. They use the existing
scrypt implementation. Password length and distinctness count Unicode code points;
passwords are never normalized or truncated. Profile changes, registration and
password reset share the frontend policy validator. Login/current-password inputs
retain the existing nonempty, at-most-256-code-point bounds without imposing a
new-password policy on existing credentials. Native password `minlength` and
`maxlength` are omitted so non-BMP characters have the same limits as on the server.
Successful changes retain the submitting session, revoke
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
| `GET /api/profile` | Own optional fields, read-only legacy transition metadata, username, email, verification status, country catalog and the effective `portrait_id` |
| `PATCH /api/profile` | Atomic update of explicitly supplied `display_name`, `country_code`, `city`, `date_of_birth`; omitted fields stay unchanged |
| `PATCH /api/profile/portrait` | Select an earned portrait by stable catalog ID; unknown, retired, unearned or URL-shaped IDs are rejected |
| `POST /api/profile/password` | `current_password`, `new_password`, `confirm_password`; safe acknowledgment only |

These routes use the existing `profile` permission for both ordinary users and
administrators. There is no account-ID parameter or administrative override for
editing another profile. Strict request allowlists reject privileged/unknown
fields. Mutations retain CSRF, origin checks and deployment freeze protection,
and are limited to 8192 bytes. Validation responses suppress raw inputs. Session,
activation and permissions are reloaded inside the update transaction; password
replacement additionally compares the previously verified hash under the lock.

The published `portrait_id` is the effective portrait: the stored selection while
the catalog still offers it, otherwise the always-available default. Achievement
detail and entitlements stay private to
[portraits and achievements](PROGRESSION.md#api-contract).

Schema **15** adds nullable `date_of_birth` and an internal `profile_private_fields`
JSON list to `accounts`. Its one-time, idempotent migration records populated
schema-14 fields whose old consent flag was false. Previously public display
names/countries/cities stay public; hidden values stay hidden. Existing data,
legacy birth years, flags, roles, password hashes, verification and sessions are
retained. New accounts have an empty exception list. Runtime public projections
use this migration list, not the obsolete sharing flags.

The client tracks edited fields and submits only those fields. An explicit PATCH
removes only its supplied fields from the exception list, including confirmation
of the same saved value. The client cannot assign transition metadata or old
`show_*` flags; these and `birth_year` writes are rejected. A full-date edit/clear
also clears the superseded legacy year. The original schema-13 clock threshold
is unchanged, so this migration cannot resume a paused timeout gate.

Deploy backend and frontend together. Take a stopped-writer backup using the
[deployment procedure](DEPLOYMENT.md#data-and-configuration). Automatic restore
requires matching schema 15/account columns and retains live account data,
including the private birth date and legacy publication exceptions. Schema-14
snapshots require explicit manual reconciliation or migration of a disposable
copy; cross-schema automatic restore and running older code on schema 15 are
refused. No production migration or deployment is performed by tests.

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
