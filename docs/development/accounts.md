# Accounts, profiles and mail implementation

[Development index](README.md) · [User management](../administration/user-management.md) · [API](api/README.md)

## Registration and verification

Public registration is exposed only when `HNEFATAFL_REGISTRATION_ENABLED=true` and
a usable SMTP configuration exists. Verification and reset tokens are single-use,
expiring and stored as hashes. The newest token replaces earlier ones; consuming a
token and updating the account commit in one transaction. Responses are generic and
do not reveal whether an identity exists.

Mail delivery is durable: queued messages, attempts, leases and retry state survive
restart. The queue is bounded; SMTP credentials come from protected environment
configuration, never Git or command arguments. Abuse budgets cover IP and identity
dimensions for registration, resend, reset and confirmation plus global request,
hash, mail and key limits.

## Profile publication

Optional profile fields are stored on the account. A non-empty field is public;
clearing it withdraws publication. Previously private legacy values remain private
until that specific field is edited or explicitly reconfirmed. The server derives
public age from a complete birth date using UTC; full dates and birth years never
enter public responses. Display names, cities and biographies are normalized,
validate Unicode control/bidirectional characters and enforce code-point limits.

Human seats in new account games store the immutable account ID. Reads resolve the
current public name/portrait; changing a profile does not rewrite game JSON, moves,
results or versions. Unlinked legacy human seats retain saved names; a matching name
never establishes an account link.

## Account lifecycle

- Administrators create accounts locally or through registration.
- Verification can be resent; an existing seat remains playable under the online
  policy even if verification is later removed.
- Password changes require the current password. Reset uses the same single-use
  token contract as verification.
- Logout and revocation clear session state; browser preferences do not grant access.

Migrations preserve account IDs, ownership, runes and historical records. Account
metadata changes do not backfill games or fabricate identities. See
[progression authority](progression-authority.md) for runes and awards and
[online authority](online-authority.md) for seat authorization.
