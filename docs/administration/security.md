# Security administration

[Administration index](README.md) · [Installation](../installation/README.md) · [Security implementation](../development/security.md) · [API](../development/api/README.md)

## Production checklist

Before exposing the service:

1. Use HTTPS and the production reverse-proxy configuration.
2. Set `HNEFATAFL_ENV=production`, the exact public URL, allowed hosts and trusted
   proxies.
3. Create the initial administrator locally with
   `python -m hnefatafl.accounts init-admin`. There is no default password.
4. Keep registration and online admission disabled until configured and tested.
5. Back up the complete data directory and verify a restore separately.
6. Configure host monitoring and log retention.

Invalid/contradictory production settings fail at startup rather than being
silently relaxed. See [configuration](../installation/configuration.md) and
[installation](../installation/installation.md).

## Accounts and permissions

Roles/permissions are server-enforced:

| Capability | Ordinary account | Administrator |
| --- | --- | --- |
| Play, history, profile, progression | Yes | Yes |
| Training, models, workers, AI Testing | No | Yes |
| All accounts/records, online void/rebuild, league recovery | No | Yes |

Permissions are not inferred from display names or UI visibility. Every registered
route has an explicit permission; `docs/API_PERMISSIONS.json` is generated from the
route table and checked by tests. See
[central permissions](../development/security.md#permissions-and-ownership).

## Sessions and request protection

- Browser sessions are HttpOnly cookies with a configurable lifetime.
- Unsafe requests require the session CSRF token and an allowed `Origin`.
- Logout and permission/account changes invalidate dependent browser state.
- Worker calls use scoped bearer tokens, not browser sessions.

Operational symptoms: a `403` on login/POST usually means a mismatched Host/Origin,
missing CSRF token or an incorrectly configured reverse proxy. Preserve the original
Host header and forwarded scheme at the proxy. See
[reverse proxy](../installation/installation.md#reverse-proxy-and-access).

## Registration and mail

Registration is off by default and requires a usable sender/SMTP configuration.
Verification and reset links are single-use and expire; only the newest link works.
See [user management](user-management.md) for enablement, rates and recovery.

## Online and public exposure

- Online admission is off by default; enable it only after verifying accounts,
  clocks, result finalization and quotas.
- Public Watch Hall discovery is **enabled by default** on the shipped configuration.
  Set `HNEFATAFL_PUBLIC_ONLINE_ENABLED=false` to disable the whole public namespace.
- Public reads expose only an allowlisted projection: no private history, email,
  full birth date, analysis or mutation.
- Automatic timeout losses/rating updates are enabled by default. Pause finalization
  only during an incident or restore.

See [online operations](online-operations.md) and
[public API](../development/api/public.md).

## Browser-local analysis

Optional browser analysis is deployment-controlled and disabled unless configured.
It adds narrowly scoped CSP sources only when delivery is enabled; disabled
deployments keep the strict policy. Published models are explicit administrator
actions and are digest-validated. See
[browser analysis](../development/browser-analysis.md) and
[the active roadmap](../development/roadmap/browser-analysis.md).

## Dependency audit

CI runs a moderate-or-higher npm audit. The committed
[`dependency-audit-exceptions.json`](../../frontend/security/dependency-audit-exceptions.json)
currently has no active exceptions. The historical withdrawal record is in
[the security advisory](../development/security-advisory.md). A new exception needs
review, a repository-hosted approval reference and an expiry; never add a blanket
ignore list or suppress findings.

## Incident boundaries

- Preserve logs, database/WAL and checkpoints before attempting repair.
- Do not delete rows to force a retry; use supported recovery/rebuild operations.
- A failed deployment or rollback is fail-closed; inspect the transaction journal
  and restore the last compatible release/data.
- No runtime authentication bypass exists. If access is lost, recover the account
  database from a verified backup rather than editing an active database manually.

Implementation details, token consumption, CSRF internals and permission coverage
are in [security implementation](../development/security.md).
