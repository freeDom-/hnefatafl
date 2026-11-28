# Interface guide

[Game index](../README.md) · [Documentation index](../../README.md)

This guide explains the Arena pages by visible label and route. Each page guide
follows the same structure: purpose, access, current screenshot, functionality,
controls/restrictions and links to conceptual documentation.

| Surface | Route | Guide |
| --- | --- | --- |
| The Great Hall, Battlefield, play screen, Online Play, Watch Hall | `/dashboard`, `/play`, `/games/{id}`, `/online`, `/watch` | [Arena](arena.md) |
| Statistics, Leaderboard, Leagues, Player tournaments | `/statistics`, `/leaderboard`, `/leagues`, `/player-tournaments` | [Competition](competition.md) |
| Hall of records, replay and Hall of Renown | `/archive`, `/portraits` | [Records and portraits](records.md) |
| Login, registration, verification, recovery, My Profile | `/login`, `/register`, `/verify-email`, `/forgot-password`, `/reset-password`, `/profile` | [Account and profile](account.md) |
| AI Workshop, Training, Models, Workers, AI Testing | `/training`, `/tournaments` | [Workshop](workshop.md) |

## Shared shell

The authenticated Arena has a persistent desktop rail, a compact top bar on smaller
screens and a bottom navigation bar on phones. Destinations are grouped under
**Trials of Valor** where applicable. Exactly one primary navigation is visible at a
time; the active destination carries `aria-current="page"`.

The header contains the language selector, account menu and theme selector. The
footer links licence, third-party notices and support. The Workshop is a separate
permission-gated workspace reached through the **AI Workshop** entry.

Shared states: transient notifications appear top-centre; field validation and
authentication errors stay inline; loading pages use explicit labels rather than
blank placeholders. Dialogs close with Escape and restore focus to the opener.

## Screenshots

All screenshots come from the running application with disposable fixtures. They are
UI examples, not benchmark or model-strength evidence. Regenerate Arena captures with
`tests/documentation-screenshots.docs.ts` and the theme matrix with
`tests/arena-final.spec.ts`; see
[documentation guidelines](../../development/documentation-guidelines.md#screenshots).
