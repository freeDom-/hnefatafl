# Registration, verification and password recovery

[Documentation index](README.md) · [Security](SECURITY.md) ·
[Deployment and backups](DEPLOYMENT.md) · [Phase 1 validation](validation/account-registration-phase1.md)

Registration extends the existing account/session system and is **disabled by
default**. The online admission gate is `security_context.online_eligible`: an
active USER/ADMIN account and a verified email. It does not restrict single-player
functionality; an unverified account may explicitly log in normally. See
[online gameplay](ONLINE.md) for the separate multiplayer routes and operational
gates. Email-address changes and account deletion are not provided.

## Account and token policy

- Usernames retain the existing 2–64 ASCII grammar after NFKC/trim normalization;
  uniqueness is case-insensitive while display casing is preserved. Public signup
  reserves `admin`, `administrator`, `support`, `system`, `moderator`, `api`, `login`,
  `register`, `hnefatafl`, including normalized/case variants. Existing/local CLI
  accounts are grandfathered. Accounts are never merged or renamed automatically.
- Email retains whole-address NFKC/trim/casefold normalization. Plus suffixes and
  dots remain. Validation bounds length at 254 and excludes whitespace, controls
  and mailbox-list/header syntax. International addresses depend on the SMTP
  server's SMTPUTF8 support; production interoperability has not been tested here.
- Passwords retain salted scrypt and the 12–256 character/four-distinct-character
  policy. They are never trimmed or normalized. New public accounts always get
  role USER, active status and unverified email. Registration creates no session.
- Legacy IDs, roles, hashes, ownership, display snapshots and sessions survive
  forward migration. Legacy verification starts unknown/unverified. Administrators
  use the same future-online verification gate. Disabled accounts are never
  reactivated by verification/reset; local CLI administration remains available.
- Tokens contain 32 random bytes, use digest-only storage and are bound to purpose,
  account, email version and an exact normalized-address digest. Verification lasts
  24 hours, reset 30 minutes. At `now >= expires`, the token is invalid. Timestamps
  use server Unix seconds (UTC), consistent with existing sessions.
- Resend invalidates the previous token immediately, before delivery. Tokens are
  consumed transactionally; reset also changes the password and revokes all sessions
  in the same transaction. One current reset token per account/purpose means there
  are no other outstanding reset tokens after successful consumption. Pending reset
  mail is removed too. Reset never logs in automatically or implies verification.
  Login rechecks current hash/status inside its session-creation transaction, so
  an in-flight old-password login cannot recreate a session after reset commits.
- CLI password reset invalidates pending reset links/mail; disabling invalidates
  all account links/mail. Last-administrator safeguards remain enforced.
- Registration/email acknowledgments are generic; a username collision may report
  an unavailable name. Registering an existing email does not create another account
  or automatically send mail; use verification resend or recovery. No pending
  accounts are automatically deleted.

## Enable and configure mail

Configure the service environment; the application does not load `.env` itself.
Use a protected environment file/secret configuration. Never put mail credentials
in Git, command arguments, browser code, diagnostic dumps or logs. Example keys
and non-secret values are in [the environment example](../.env.example).

| Setting | Default / meaning |
| --- | --- |
| `HNEFATAFL_REGISTRATION_ENABLED` | `false`; only literal `true` enables new public accounts |
| `HNEFATAFL_PUBLIC_URL` | Trusted HTTPS origin, no path/query/fragment/userinfo; reused for email links and origin checks |
| `HNEFATAFL_ACCOUNT_SENDER` | Required bare normalized sender address |
| `HNEFATAFL_ACCOUNT_SMTP_HOST` | Required SMTP server |
| `HNEFATAFL_ACCOUNT_SMTP_PORT` | `587` |
| `HNEFATAFL_ACCOUNT_SMTP_TLS` | `starttls`, or `tls` for implicit TLS (normally port 465); no plaintext fallback |
| `HNEFATAFL_ACCOUNT_SMTP_USERNAME`, `HNEFATAFL_ACCOUNT_SMTP_PASSWORD` | Both configured or both absent for an authenticated-by-network relay |
| `HNEFATAFL_ACCOUNT_TIMEOUT` | `10` seconds per socket operation, maximum 30 |
| `HNEFATAFL_ACCOUNT_MAX_ATTEMPTS` | `4`, maximum 10 |
| `HNEFATAFL_ACCOUNT_RETRY_SECONDS` | `60`; exponential retries capped at 3600 seconds |
| `HNEFATAFL_ACCOUNT_LEASE_SECONDS` | `60`, at least four times socket timeout |
| `HNEFATAFL_ACCOUNT_POLL_SECONDS` | `5`; one work item per poll/process |
| `HNEFATAFL_ACCOUNT_QUEUE_LIMIT` | `1000` retained delivery rows, maximum 10000 |

In development, the public origin may be HTTP on loopback only. Production keeps
the existing HTTPS/allowed-host/trusted-proxy requirements. SMTP uses certificate
verification and STARTTLS before authentication or implicit TLS; there is no
environment-configurable development capture transport. The fake mailbox lives
only in the guarded browser-test bootstrap on separate loopback ports.

Missing/invalid mail settings disable email work and fail closed for new
registration while preserving existing login and local CLI recovery. An invalid
configuration emits a constant warning without values. Registration availability
is returned by `GET /api/auth/registration`. Disabling signup does not disable
verification/recovery when mail remains configured. Verification fails closed
while mail configuration is unavailable; valid reset tokens remain usable without
SMTP, and never verify the account. Restore configuration and restart to resume
queued delivery. Nothing is marked verified by successful/failed email delivery.

## Durable delivery and crash behavior

[registration.py](../hnefatafl/registration.py) stores an account/purpose intent in
the same transaction as a new account or resend request, with no plaintext token
or token-bearing message body. A worker claims a bounded lease, generates a raw
token in memory and commits its digest before sending. SMTP runs outside the
database transaction and independently of the initiating browser request. Both
HTTP and background writes respect the existing deployment freeze.

| Situation | Result |
| --- | --- |
| Failure before claim | Intent remains pending and is found after restart |
| Crash after digest commit / before send | Lease expires; retry rotates the digest and sends a new token |
| Crash after SMTP acceptance / before acknowledgment | Duplicate mail is possible; retry invalidates the previous link |
| SMTP exception / rejection / timeout | Exception text is suppressed; invalidate that attempt's token and schedule bounded backoff |
| Resend/reset races a worker | Generation/lease guards prevent the old finalizer from changing the replacement or recreating consumed work |
| Retry limit reached | Failed status, no usable token; a later explicitly requested resend can create fresh work |

Email delivery is **not exactly once**. A delayed older email may arrive after a
newer one; only the current link works. No broker or request-lifetime background
task is used. A daemon supervised by FastAPI's lifespan polls durable rows and
joins on shutdown. Socket timeouts bound individual SMTP operations, not a strict
end-to-end wall-clock deadline; a stalled operation can outlive a lease. Expired
leases and stale-finalizer guards handle that case across processes. The supported
single API-worker deployment is unchanged; multiple processes share SQLite lease
and limiter protection but are not a new deployment recommendation.

Expired tokens and sent/failed intents older than 24 hours are pruned during mail
work/account requests. There are at most two token rows per account and a bounded
delivery queue. Live/pending accounts are not purged. Expired limiter rows are
removed on the next admission/mail-budget check. With mail stopped and no account
traffic, expiry is still enforced at use time; physical cleanup waits for work.
For a full queue, admission returns a generic service-unavailable response before
account lookup, including unknown addresses; there is no account-existence signal.
Turn off registration to reduce incoming work while delivery drains.

Operators can inspect queue **counts**, statuses, attempts and next-attempt/lease
timestamps through the protected local database. Do not dump account/token tables
or enable SMTP debug logging. Repair transport settings, restart, and let durable
leases/retries reconcile. Failed rows can be replaced by an explicit resend after
capacity is available. No SMTP error/recipient data is returned to clients.

## Abuse budgets

Every setting below is configurable with prefix `HNEFATAFL_ACCOUNT_`; the table
shows the remaining suffix. Default window is 900 seconds, fixed from a bucket's
first attempt (not a sliding inactivity window).

| Suffix | Default |
| --- | ---: |
| `WINDOW_SECONDS` | 900 |
| `REGISTER_IP` / `REGISTER_IDENTITY` | 5 / 3 |
| `RESEND_IP` / `RESEND_IDENTITY` | 5 / 3 |
| `RESET_IP` / `RESET_IDENTITY` | 5 / 3 |
| `CONFIRM_IP` / `CONFIRM_IDENTITY` | 20 / 10 |
| `GLOBAL_REQUESTS` | 600 new-account-route requests |
| `GLOBAL_HASHES` | 100 shared login/registration/reset password operations |
| `GLOBAL_MAIL` | 100 delivery attempts |
| `LIMIT_KEYS` | 10000 retained limiter keys, maximum 100000 |

IP/global request admission occurs before parsing, with an 8192-byte auth-body
limit including chunked requests. Identity/hash admission occurs before expensive
password work; malformed/policy-invalid attempts may still spend their budgets.
The original login IP/identity limits also remain. Private CLI provisioning and
one startup dummy hash are outside public hashing budgets. Delivery attempts spend
the global mail budget before generating/sending a token.
Global login admission shares the legacy-attempt transaction: once exhausted,
denied requests cannot keep allocating new login-attempt keys.

Limiter state is transactionally persisted, shared across connections/processes
and retained on compatible restores. Keys use SHA-256 of peer/address/token
identities, never raw email/IP/token text. Hashing is pseudonymization, not strong
anonymity for guessable addresses. Live buckets are not evicted to admit new keys;
capacity exhaustion fails closed. Request time is authoritative; IP comes from
`request.client` and the existing Uvicorn trusted-proxy configuration, never a
handler's interpretation of arbitrary forwarded headers. No CAPTCHA/external
limiter service is required. Global caps trade availability for bounded work;
operators must tune them for expected traffic. Do not promise exact timing
indistinguishability or immunity to distributed denial of service.

## Link safety and public endpoints

| Method/path under `/api/auth` | Purpose |
| --- | --- |
| `GET /registration` | Public availability; no account lookup |
| `POST /register` | Username/email/password signup; generic 202 acknowledgment |
| `POST /verification/resend` | Generic verification request |
| `POST /verify-email` | Explicit token consumption |
| `POST /password-reset/request` | Generic recovery request |
| `POST /password-reset/confirm` | Explicit token/new-password consumption |

All public mutations require JSON and a matching Origin. Existing authenticated
mutations still require CSRF tokens. Unknown methods/routes remain denied by the
exact [endpoint registry](API_PERMISSIONS.json). Client-supplied role, activation,
verification or account binding is rejected. Auth validation errors suppress raw
inputs; SMTP errors and unknown policy errors are sanitized. The existing account
projection is still authenticated-only; no public account-profile endpoint is added.

Email links use `/verify-email#token=...` and `/reset-password#token=...`, built
solely from configured origin. Fragments never enter HTTP request/access-log URLs.
The client removes the fragment promptly and holds the token only in memory,
including same-tab fragment navigation. GET/scanner navigation never consumes it;
the user must submit the form. Confirmation pages use no third-party assets and
no-referrer policy. Do not configure proxies/analytics to log auth request bodies
or browser URL fragments; these contain secrets. Tokens/passwords never enter
browser persistence. Reopening an expired/replaced link provides a resend path.

## Migration and rollback boundary

The current overall database is schema 12 after [online-core integration](ONLINE.md),
the [rating pool and start-rating snapshots](RATINGS.md). The account upgrade below remains schema 9;
schema-12-aware recovery additionally preserves current online authority and pauses
its timeout gate. Review the
[online compatibility boundary](ONLINE.md#migration-backup-and-recovery) too.

Schema 9 adds nullable `email_verified_at`, `email_version` (default 1), purpose-bound
digest rows, delivery intents and limiter buckets. The migration preflights NFKC/
trim/casefold username/email collisions, normalizes legacy stored identifiers and
preserves display casing, account IDs/hashes/roles/status and ownership. Run the
read-only operator check **before** a planned upgrade, against a stopped-writer
snapshot or during a maintenance window:

```sh
.venv/bin/python -m hnefatafl.accounts check-collisions --data-dir /absolute/data
```

It opens SQLite `mode=ro`, does not initialize/migrate/create a database, and reports
only collision fields/account IDs. Nonzero status means resolve the identified
accounts manually before migration; never merge identities or ownership by email.
Take a complete stopped-writer backup as described in
[deployment](DEPLOYMENT.md#data-and-configuration), then use the normal separately
authorized upgrade procedure. Development/tests only exercised disposable fixtures;
this work did not execute a production migration.

Schema 8 code refuses schema 9. Automatic restore also refuses differing account
columns. **An additive upgrade is not an automatic downgrade path.** Prefer a
forward fix using schema-9-capable code. Compatible schema-9 restores retain live
password/role/status/verification and limiter state, revoke sessions, and discard
all saved verification/reset tokens and pending mail. Users must request fresh
links. Existing worker-credential revocation on restore is unchanged.

If an operator must return to schema 8, stop all writers, preserve the complete
current data/security state and the pre-upgrade code/data snapshot separately,
and perform manual security reconciliation in an isolated restore root. Never
overwrite recent password resets, disabled status, new accounts or ownership with
an old snapshot. Restore only the matching code/data pair after reconciling latest
account security fields/IDs, revoking sessions/tokens, disabling public registration
and validating readiness in isolation. If that reconciliation cannot be established,
do not cut over; retain the live root and repair forward. No automatic schema
downgrade, data merge or production cutover is implemented here.

## Operator choices and next phase

Before deployment, choose/validate sender, public origin, SMTP credentials/provider,
privacy wording, retention, support/recovery policy, quotas and monitoring. Real
SMTP/provider deliverability and physical mobile clients remain operator validation,
not claims made by fake-transport tests. The [registration browser suite](../frontend/registration-tests/accounts.spec.ts)
exercises English/German flows and mobile emulation with an in-memory fake mailbox.

Before enabling online admission, review the implemented participant, deadline and
result policies in [online operations](ONLINE.md). Timeout finalization additionally
requires a separately approved outage procedure and an enabled persistent gate.
Publishing or installing a release does not enable these settings.

Authentication forms share heading, content and action spacing across login,
registration, verification and password recovery.
