# My Profile

[Game index](README.md) · [Appearance](personalization.md#appearance) · [Account security](../administration/security.md) · [Interface guide](interface/README.md)

**My Profile** (`/profile`) manages your account identity, privacy choices, security
and appearance. All optional profile fields can be left empty.

## Personal information

| Field | Public behavior |
| --- | --- |
| Display name | Shown with **(@username)** when it differs from the username; non-unique |
| Country | Shown in the detailed leaderboard |
| City | Shown in the detailed leaderboard |
| Date of birth | Full date stays private; only a completed age may be published |

A populated optional field is public; clearing it withdraws its public value. There
are no permanent sharing checkboxes. Previously saved private values stay private
until you edit or explicitly reconfirm that specific field.

Date of birth is an optional ISO calendar date from 1900-01-01 through today. Age is
calculated by the server in UTC: the birthday must have occurred this year, and a
29 February birthday advances on 1 March in non-leap years. A historical birth year
alone cannot produce an exact age and remains private.

Display names, cities and biographies reject unsafe control/bidirectional
characters. Length limits are counted in Unicode code points. Oversized or invalid
input shows a localized error; it is not silently truncated.

## Names in games and history

Account-backed games, replays, statistics and history show your current public name
or username, not a frozen display-name snapshot. Renaming yourself updates those
surfaces on the next read. Your account ID remains the authorization identity;
changing your name never changes ownership, results or records.

Older records without a verified account link keep their saved names. AI and model
labels are historical snapshots and do not change when a model is renamed.

## Account security

The profile shows your email verification state and provides:

- **Resend verification** when the address is unverified.
- **Change password** with the current password and the shared password policy.
- Single-use password-reset links requested from the login screen.

Verification and reset links expire; only the newest link works. The profile never
shows the raw token or a verification link. See
[user management](../administration/user-management.md) for operator configuration.

## Selected portrait and appearance

Choose an earned portrait for your profile, leaderboard and online presence.
Portrait selection is cosmetic; locked portraits cannot be selected and a missing
asset falls back safely. **Open Hall of Renown** shows the full catalog and
achievement progress.

The **Appearance** section controls the UI theme, board, tokens and interface scale.
Preferences are saved per browser/device, not on the account. See
[personalization](personalization.md).

## Account menu

The header account menu opens **My Profile** and **Log out**. It supports mouse,
touch, keyboard arrows, Home/End, Enter/Space, Escape and outside dismissal. In the
compact header the trigger shows only the account icon; opening the menu shows the
full username above the actions.

## Related

- [Progression](progression.md): achievements, portraits and tiers.
- [Personalization](personalization.md): themes, language, tokens and records.
- [Account implementation](../development/accounts.md): profile persistence,
  publication rules, sessions and token handling.
