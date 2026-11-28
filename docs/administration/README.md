# Administration

Administration covers accounts, authorization, online-game operation and the
checks an operator needs after deployment.

| Task | Canonical page |
| --- | --- |
| Create administrators, understand sessions, CSRF, permissions and API coverage | [Security](security.md) |
| Enable registration, verify email, recover passwords and tune abuse budgets | [User management](user-management.md) |
| Enable online admission, monitor quotas, resolve deadlines, pause finalization and recover games | [Online operations](online-operations.md) |
| Inspect service health, logs, releases and storage | [Monitoring](monitoring.md) |
| Install, update, back up and roll back the service | [Installation](../installation/README.md) |

## Role model

The backend owns permissions. Administrators can manage accounts and the
training workshop; ordinary accounts can play, review their own records and earn
progression. Hiding a navigation item is presentation only; every route checks
the account again. See [security](security.md#central-permissions-and-object-ownership).

## Required operator actions

Before exposing an instance publicly:

1. Complete the production HTTPS/reverse-proxy configuration in
   [installation](../installation/installation.md#reverse-proxy-and-access).
2. Create the initial administrator locally; there is no default password.
3. Decide whether registration and online admission are enabled. Both default
   off.
4. Back up the complete data directory and verify a restore in a disposable
   location.
5. Configure monitoring and log retention outside the application.

The [release-readiness checklist](../releases/readiness.md) records outstanding
combined-acceptance gates. A merged feature or a green pull-request check is not
deployment authorization.
