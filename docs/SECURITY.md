# Accounts and API security

[Documentation index](README.md) · [Deployment](DEPLOYMENT.md) · [Endpoint inventory](API_PERMISSIONS.json)

## Accounts and local administration

Accounts have stable random IDs, normalized unique usernames/emails, password
hashes, roles, enabled status, creation/login timestamps and optional display names.
The default initial username is **Odin**; email and password are always supplied.
Passwords require 12–256 characters with four distinct characters and use salted scrypt (N=32768, r=8, p=3,
32 MiB memory). Python/OpenSSL implements hashing and constant-time comparison.
There are no default passwords or remote bootstrap endpoints. Public registration
is configurable and disabled by default; [verification/recovery operations](REGISTRATION.md)
document SMTP, token, quota, migration and deployment requirements.

From the OS account owning the data directory (root in production):

```sh
.venv/bin/python -m hnefatafl.accounts init-admin --data-dir /absolute/data
.venv/bin/python -m hnefatafl.accounts create-user --data-dir /absolute/data
.venv/bin/python -m hnefatafl.accounts reset-password --username Odin --data-dir /absolute/data
.venv/bin/python -m hnefatafl.accounts set-role --username Raven --role ADMIN --data-dir /absolute/data
.venv/bin/python -m hnefatafl.accounts disable --username Raven --data-dir /absolute/data
.venv/bin/python -m hnefatafl.accounts enable --username Raven --data-dir /absolute/data
```

Passwords are prompted with echo disabled and confirmation. The CLI refuses a
terminal that cannot hide password input. Automation may send
one JSON credentials object through protected stdin with `--credentials-stdin`
for initial/create operations; never place passwords in argv or shell history.
Initial provisioning records a transactional one-time database marker. Repeating
it leaves existing accounts/passwords intact. Explicit local account management
remains available to the OS operator. The last active administrator cannot be
disabled or demoted. Public account management is intentionally absent.

## Sessions, CSRF and revocation

Login accepts username or email, returns a safe account projection and CSRF token,
and sets a random HttpOnly SameSite=Lax cookie. Only SHA-256 hashes of session and
CSRF tokens are stored in SQLite. The cookie is Secure in production and host-only.
Passwords/session tokens are never written to browser storage; CSRF stays in memory.
Identity or effective-permission changes remount protected UI state; logout also
clears private game/setup preferences.
Login rechecks the current password hash/status under the session-creation writer
lock, so an old password checked concurrently with reset cannot grant a new
session after reset commits. Public login, signup and reset share a durable global
password-work budget in addition to route/IP/identity protections.
`GET /api/auth/me` restores the account/CSRF token after a reload. Authentication
requires no signing key: entropy is generated with Python `secrets`, while the
protected database is the shared persistent session authority.

Sessions expire after 12 hours, with no sliding renewal. Set
`HNEFATAFL_SESSION_SECONDS` to 60–604800 seconds to change this. Login creates a new
session and invalidates the browser's previous session; logout deletes its session.
Accounts and effective permissions are reloaded for every request. Local password,
role or status changes revoke all browser sessions. Disabled/unknown-role accounts
cannot log in or use retained sessions. Concurrent requests already authorized may
finish; revocation applies to subsequent requests. Authentication works across
restarts/workers; process-owned training/catalog coordination still requires one
API worker. API worker count is unrelated to training parallelism.

Cookie-authenticated mutations require `X-CSRF-Token`. If supplied, Origin must
exactly equal the public HTTPS origin (or the request origin during development).
Login itself requires same-origin Origin and JSON, preventing login CSRF through
forms. No cross-origin API access is enabled. Login throttling is transactional
and shared across workers: ten attempts per normalized identity and thirty per
connecting IP per fifteen minutes, including successful attempts. Failures do
not disclose account existence. Configure trusted proxies correctly so IP limiting
uses verified client addresses.

## Central permissions and object ownership

`security_context.py` defines role permissions, and `auth.py` declares every
method/path grant. The ASGI boundary checks authentication/permissions before body
parsing, upload handling or endpoint execution. New unclassified routes are denied;
unknown roles receive no permissions. Request roles, browser account IDs, custom
headers, query parameters and machine tokens cannot upgrade browser permissions.

| Permission | USER | ADMIN |
| --- | --- | --- |
| play, profile | Yes | Yes |
| training, tournaments, models management | No | Yes |
| accounts, all_data | No | Yes (account management is local CLI only) |

SQLite queries scope games and local players to the authenticated account. New
records receive identity from the trusted request context. Mutations first load
that scoped object; stats/history use the same scoped query. Administrators
explicitly have global data access. Users can play all existing modes, configure
bounded inference opponents/advisors, delete their own games/local profiles, and
select existing models. The ordinary model catalog exposes only ID/name/ruleset/rune;
paths, training summaries, checkpoints, renames/imports/exports/deletion are admin-only.

Schema 8 introduced accounts, sessions, login attempts, bootstrap metadata, nullable
game/player ownership and a future account-participation relation. Existing game
JSON, players, runes, statistics, jobs, tournaments and checkpoints remain intact.
Historical NULL-owned records are administrator-only and are never assigned to
Odin or given invented passwords. Local humans and AI identities are separate
from login accounts. Future online participation needs explicit enrollment and
authorization; the reserved relation does not confer access today.
Schema 9 adds unknown/unverified legacy email state, version/address-bound token
digests, leased mail intents and limiter state. IDs, ownership and existing sessions
survive forward migration; safe rollback has an explicit account-schema boundary.

Schema 10 adds independent [online match/game/result/gate records](ONLINE.md).
Create/join require active, verified accounts, including administrators; an admitted
seat retains play after verification changes with an active account/current session.
Online mutations reload the session, role/status and participants inside their
SQLite write transaction, after middleware checks. Admin privilege cannot take a
turn or obtain running-game assistance. All online mutations also require matching
Origin, JSON, CSRF, strict bodies and bounded size. Dedicated projections omit
account/security metadata and private administrative notes; local-game analysis,
AI and shared-cursor/export adapters reject online IDs for all roles.
Timeout controls are persisted, administrator-restricted and audited, with production
finalization disabled until an approved outage procedure and explicit gate resume.
Compatible recovery retains live online authority and pauses the gate; incompatible
online schema rollback is refused before root replacement.

Authenticated online creation is additionally bounded per account: five waiting
lobbies and ten new matches per rolling fifteen minutes by default, without an
administrator exemption. Limits serialize with creation and survive cancellation,
restart and compatible recovery. Quota denials use fixed-size persistent counters
and sanitized, rate-limited logs; aggregate monitoring requires administrator
permission and exposes no account/security identifiers. See
[creation quotas and monitoring](ONLINE.md#creation-quotas-and-monitoring).

## Coverage and explicit exceptions

The complete [JSON inventory](API_PERMISSIONS.json) records every API method/path,
required authentication/permission, possible persistence changes and user-data scope.
Regression tests compare registered API routes with exact policy declarations;
an added route without a policy fails the suite and remains denied.
Run `.venv/bin/python scripts/audit_api.py` to check the generated inventory;
after reviewing new permissions, regenerate with `--write`. Persistence flags
conservatively include GET routes that can reserve identity runes or reconcile jobs.

Login, registration availability, the five declared registration/verification/reset
POST routes, minimal database readiness, the auth shell and compiled `/assets/`
files are public. Mutating public routes require same-origin JSON and bounded
request bodies; signup cannot assign roles or verification and creates no session.
Token pages require explicit POST confirmation and exclude tokens from URL request
paths through fragment transport. The build directory is
the only static root. API schema/interactive docs are disabled. Page requests for
training/tournaments also require their permissions. Unknown API paths fail closed;
slash variants and unsupported methods do not grant access. No SSE or WebSocket
routes currently exist; all WebSocket handshakes are rejected until explicit
authenticated support is implemented. Health reports only `{"status":"ok"}`.

Worker routes use a separate scoped machine credential and existing job leases,
validated before request parsing and again by the handler. A worker token never
authenticates gameplay, training management or browser sessions. Enrollment requires
an administrator; access also checks that enrolling account remains active with
training permission. Legacy workers without an enrolling account must be re-enrolled.
Logs/checkpoints remain preserved. Browser logout leaves delegated machine credentials
valid; revoke a worker separately to stop machine access. Account disablement or
demotion prevents future worker requests.

## Operational boundaries

Production requires an HTTPS public origin, exact allowed hosts, explicit trusted
proxies and an initialized administrator. There is no remote auth-off configuration.
Development uses HTTP cookies on explicit local hosts, retaining login, CSRF,
ownership and permissions. Do not expose development servers.

Uploads retain bounded extraction, tensor-only loading and contained storage paths.
Training and inference are computationally expensive; use LXC, systemd and proxy
limits. Permitted users can consume inference resources; multi-tenant scheduling
and per-user compute quotas remain future work. SQLite and the OS account are
trusted; root/data-owner access can change accounts directly. TLS, network
restriction to the application port, patched dependencies and off-host backups
remain operator responsibilities. MFA is not implemented. SMTP/provider delivery,
privacy/retention and operational recovery policy require owner validation before
enabling signup. These are tested controls, not a claim of immunity.
