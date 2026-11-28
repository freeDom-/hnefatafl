# Security implementation

[Development index](README.md) · [Security administration](../administration/security.md) · [API](api/README.md)

This page describes the implemented security boundary. Operational configuration
and incident handling are in [administration security](../administration/security.md).

## Accounts and sessions

- Passwords use the project's password policy and a memory-hard hash. No default
  account or password exists; administrators are created locally.
- Browser sessions use HttpOnly cookies with an expiry (`HNEFATAFL_SESSION_SECONDS`
  default 43200). Session state is server-owned and can be revoked.
- Unsafe browser requests require the session CSRF token and an allowed `Origin`.
  Worker requests use a scoped bearer token instead.
- Login, registration, verification, reset and public spectator routes are the only
  public surfaces. Account creation and mail delivery are off by default.

## Permissions and ownership

Every registered route has a permission in `docs/API_PERMISSIONS.json`:
`public`, `session`, `accounts`, `profile`, `play`, `training`, `models`,
`tournaments` or `worker`. `tests/test_security.py` derives the matrix from the real
route table, so a new route without a permission fails the test.

Route permission is not sufficient by itself. Game, job, model, worker, tournament
and analysis resources are resolved through owned IDs; clients cannot supply
filesystem paths. Administrators may read all data where the API explicitly grants
`accounts`; ordinary accounts cannot read another account's private records.

## Tokens and account changes

- Email verification and password-reset tokens are single-use, expiring and stored
  hashed. Only the newest link works. Consumption and the account mutation commit
  in one transaction.
- Account identity is the immutable authorization key. Display names are editable,
  non-unique presentation data and never establish ownership.
- Profile publication is per optional field. Clearing a field withdraws its public
  value; private birth dates never enter public responses.
- Account switches, logout, permission changes and revocation invalidate dependent
  browser state. A background response from an older session cannot replace newer
  game state.

## Browser analysis boundary

Browser-local analysis is opt-in and deployment-controlled. When enabled, the
server delivers a pinned, digest-verified runtime and validated published model
artifacts. It adds only `script-src 'self' 'wasm-unsafe-eval'` and
`worker-src 'self'` to the CSP; it does not enable broad eval, inline scripts,
wildcard workers or cross-origin scripts. Disabled deployments keep the previous
strict policy byte-for-byte. Published models are explicit administrator actions;
invalid or unvalidated artifacts are rejected. See
[browser analysis](browser-analysis.md).

## Explicit exceptions and tests

The security matrix covers every registered API route. Intentional exceptions are
listed in [security administration](../administration/security.md#dependency-audit).
Dependency-audit exceptions live in
`frontend/security/dependency-audit-exceptions.json` and currently contain none.
Do not add a suppression, ignore list or route bypass without tests and documented
approval.
