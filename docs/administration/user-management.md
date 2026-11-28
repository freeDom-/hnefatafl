# User management and registration

[Administration index](README.md) · [Security](security.md) · [Account implementation](../development/accounts.md) · [Configuration](../installation/configuration.md)

## Accounts

Administrators create the first local account during installation. Ordinary
accounts can be created by registration (when enabled), and administrators can
manage access through the existing account controls. There is no default password,
public default administrator or authentication bypass.

Users can change their password in **My Profile** after entering the current
password. Forgotten passwords use the email reset flow. Verification and reset
links are single-use and expire; only the newest link works.

## Enable registration and mail

Registration is off by default. To enable it:

1. Configure a reachable SMTP service and sender.
2. Export `HNEFATAFL_REGISTRATION_ENABLED=true` plus the `HNEFATAFL_ACCOUNT_*`
   mail settings.
3. Restart the service and verify registration, verification, resend and reset on a
   disposable account.
4. Keep credentials in protected service configuration, never Git or command
   arguments.

Required mail settings and defaults are listed in
[configuration](../installation/configuration.md#registration-and-mail). SMTP
delivery is durable; a temporary outage queues bounded retries rather than losing
the request. A mailbox provider must accept the sender and the public URL must be
reachable by the recipient.

## Verification and recovery

- An unverified account can resend verification from the login/profile surface.
- Only the newest verification/reset link works; an older link is rejected.
- Reset responses do not reveal whether an account exists.
- An existing online seat remains playable under the server policy even if
  verification is later removed; new rated/admission actions recheck eligibility.
- Changing email or password is an account-security action; do not edit the live
  database manually.

## Abuse budgets and limits

Registration, resend, reset and confirmation endpoints are rate-limited by IP and
identity, with global caps on requests, hashes, mail and limit keys. Defaults are
documented in [configuration](../installation/configuration.md#registration-and-mail).
The account browser regression harness raises the registration-IP budget for its
disposable fake mailbox; production defaults are unchanged. Do not disable rate
limits to work around a client retry loop; fix the client or provider.

## Migration and rollback

Account migrations are additive and preserve account IDs, ownership, runes and
history. A schema upgrade is not a reason to delete data. Before upgrading, take a
consistent backup of the complete data root; after restoring, use the compatible
release and verify login, records, progression and model references. See
[updating and recovery](../installation/updating.md).

Mail-queue/token internals and the exact migration behavior are in
[account implementation](../development/accounts.md).

## Operator choices

- Whether public registration is available.
- Whether online admission is enabled independently of registration.
- Whether mail is configured for verification/recovery.
- Session lifetime, trusted proxies and public origin.

Document the chosen policy for users. Do not promise email delivery when no SMTP
service is configured.
