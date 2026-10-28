# Phase 1 account registration implementation brief

This Phase 0 brief is retained as design context. The implemented Phase 1 defaults,
operations and remaining deployment decisions are documented in
[Registration and recovery](../REGISTRATION.md); Phase 0 recommendations alone
did not approve production activation.

[Documentation index](../README.md) · [Repository assessment](online-phase0-assessment.md) ·
[Online design](online-phase0.md) · [Validation/handoff](../validation/online-phase0.md)

Scope: extend existing [Auth](../../hnefatafl/auth.py), SQLite accounts/sessions,
local [account CLI](../../hnefatafl/accounts.py), fail-closed permission registry
and [security tests](../../tests/test_security.py). No replacement identity
framework. Phase 0 implements no registration handler, token schema or email delivery.

## Confirmed boundary and approval checklist

Confirmed: public registration is a later phase; preserve existing secure hashing,
sessions, authorization, ownership and training compatibility. Keep private emails
and security data out of public game/account projections. Registration must not
enable multiplayer before the lifecycle/permission integration is ready.

Recommendations requiring owner approval before the Phase 1 implementation:

- Continue the existing 2–64 ASCII username grammar after NFKC/trim normalization;
  use case-insensitive uniqueness and preserve display casing. Reserve `admin`,
  `administrator`, `support`, `system`, `moderator`, `api`, `login`, `register` and
  project/service names; approve the final list. Existing reserved usernames are
  grandfathered; do not silently rename accounts.
- Retain existing whole-email NFKC/trim/casefold policy for compatibility, with a
  pragmatic bounded syntax check. Do not strip plus suffixes/dots or invent
  provider-specific equivalence; email delivery verification establishes control,
  not syntax alone. More restrictive international address policy needs a decision.
- Require verified email before online create/join, keeping existing account
  single-player usable. Pending registration must not acquire a session that
  grants online eligibility. Verification status and `active` are distinct.
- Suggested verification TTL 24 hours; reset TTL 30 minutes; resend invalidates
  earlier tokens. Set explicit resend/daily/address/account/IP budgets. Approve
  retention periods, privacy text, delivery provider and support/recovery policy.
- Legacy accounts begin with unknown/unverified email; preserve login, roles,
  single-player and ownership, then verify on demand for future online entry.
  CLI administrators follow the same online gate; emergency CLI password reset
  remains available. Never bulk-verify addresses without evidence.

## Prerequisites

Approve the policy above, public origin/from-address and email service credentials.
Decide how pending accounts expire, how address changes are verified, and whether
registration opens broadly or initially uses invitations. Inventory existing
normalized username/email collisions in an operator-approved, read-only migration
check; do not access production data during this phase's development. Define
account-schema compatibility for [rollback](../DEPLOYMENT.md#rollback-and-recovery)
and backup recovery before writing the separately reviewed production migration.

## Ordered implementation tasks

1. Define shared normalization/validation functions around existing `normalize`,
   `Auth.create_account` and scrypt. Preserve current password policy (12–256
   characters, at least four distinct); never trim or normalize passwords. Bound
   body sizes before hashing, suppress raw input in validation/logs and use
   constant-time checks. Keep hash parameters and upgrade behavior explicit.
2. Introduce a reviewed migration for verification state and purpose-bound token
   records plus durable delivery attempts. Retain stable account IDs and all
   ownership. Preflight normalized collisions; refuse/report ambiguous legacy
   entries for manual resolution, never merge owners by name or email.
3. Build registration as a transactional insert using existing UNIQUE constraints,
   handling racing collisions after insert, not only a precheck. A username clash
   may return a generic unavailable-name response; email registration/reset/resend
   acknowledgments should not reveal account existence or active/verified state.
   Apply reserved-name checks to normalized names. React text rendering remains
   escaped; never render a name as HTML, Markdown or a filesystem path.
4. Add IP/account/email (hashed keys) throttles for registration, token attempts,
   resend and reset requests, plus bounded global delivery/hash-work budgets.
   Rate-limit before expensive scrypt or mail work. Use configured trusted proxy
   addresses, not spoofable forwarded headers. Do not treat arbitrary browser
   address strings as trustworthy. Consider CAPTCHA only if abuse warrants it.
5. Generate at least 32 cryptographically random bytes for purpose-bound
   verification/reset tokens. Store only token digests, account/purpose, expiry
   and consumed state; compare/consume under one transaction. Never log token
   values or include them in API errors. Use trusted configured-origin links,
   not request Host. GET opens a confirmation form; a CSRF/origin-protected POST
   consumes the token so mail link scanners do not spend it. Add `no-referrer`
   behavior to token pages and remove secrets from the address bar promptly.
6. Verification consumes the token and updates only its bound account/address
   version. Reset consumes the token, changes the scrypt hash and invalidates all
   account sessions in the **same transaction**. Reuse the account-update session
   invalidation behavior, adapted to accept a connection so nested transactions
   cannot break atomicity. Deny expired/replayed/wrong-purpose/replaced tokens.
   Password reset does not implicitly verify a different address or enable a
   disabled account; inactive/legacy-account behavior needs explicit policy.
7. Configure delivery via secret runtime configuration (transport credentials,
   sender, public origin, timeouts). Use a bounded SQLite pending-delivery record
   committed with account/token intent and retry tracking, not a broker.
   Preserve digest-only token storage: on delivery failure invalidate the old
   digest and generate a new token for retry. Claim delivery work with a lease;
   keep raw token only in memory while sending, commit its digest before sending,
   and rate-limit retry rotation. A crash may cause a duplicate email or require
   a resend; only the latest digest is usable. Define this behavior in UX/tests;
   do not persist plaintext tokens in a generic mail outbox. If choosing encrypted
   delivery payloads instead, separately approve key management and expiry.
8. Extend explicit `POLICY` declarations and auth-shell routing, preserving
   same-origin JSON/body-size protections for public mutations. Add sanitized
   public errors, account verification UI, delivery retry/status messaging and
   token confirmation pages. Never turn existing `public_account` into a public
   profile; continue session/CSRF protection on authenticated mutations.
9. Document operators' email configuration, disabled-delivery/failure behavior,
   retry recovery, token cleanup, rate-limit recovery, backup/rollback and legacy
   verification. Failure must not leave accounts falsely verified or grant play
   to disabled users. Do not leak SMTP errors, recipients or credentials.

## Tests and acceptance criteria

Use isolated SQLite roots, a fake mail transport and explicitly controlled time;
no external emails or production data. Test:

- NFKC/case/whitespace variants, username/email collision races, reserved names,
  length/syntax boundaries, safe hostile-name rendering and unchanged owner IDs.
- Existing password vectors, untrimmed passwords, scrypt verification, body-size
  limits and no password/token reflection in logs/errors.
- Anonymous registration allowed only through declared policy; cross-origin and
  CSRF rules, fixed session expiry, login rotation and current ownership checks.
- Uniform unknown/existing/reset/resend responses; reasonable timing behavior
  without promising exact network timing indistinguishability; throttle windows,
  burst/concurrency/global caps and spoofed address headers.
- Verification/reset expiry boundaries, single use, concurrent consumption,
  wrong-purpose/address/account binding, resend invalidation and cleanup.
- Atomic password change/token use/session revocation, including other tabs,
  processes and restart; disabled accounts and last-admin safeguards preserved.
- Delivery timeout/rejection, retry leases, crashes before/after send, duplicate
  mail, configuration failure, no false verification and safe resend recovery.
- Legacy accounts continue login/single-player without invented verification;
  future online gate denies unverified creation/joining, including admins.
- Forward migration, rollback security-state preservation and backup/recovery
  compatibility using disposable old-schema fixtures.

Acceptance: a new account can register, verify, log in and recover a password via
the configured test transport; reset revokes old sessions; tokens are expiring,
purpose-bound, digest-only and single-use; uniqueness holds under races; all
existing security/ownership tests pass. `make check`, `make docs`, registration
browser flows and deployment/rollback tests must pass with actual results recorded.
Opening multiplayer, production email delivery and production migration execution
remain separate authorized deployment steps.
