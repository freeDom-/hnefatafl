# UI development guidelines

These rules apply to every frontend change. The [system inventory](frontend.md)
describes the Arena/Workshop shell, themes, tokens and notification layer; this
page states the requirements a change must satisfy.

## Reuse before adding

1. Use an existing page shell (`ArenaShell` / `WorkshopShell`), `PageHeading`,
   `Tabs`, `Modal`, button/field classes and table/list primitives.
2. Prefer shared classes in `style.css`, `ui.css` and the surface-specific CSS
   already used by the page. Do not introduce a parallel spacing, colour or
   typography system.
3. Reuse the notification layer for transient errors/success messages. Keep field
   validation and authentication errors inline. See [notifications](notifications.md).
4. Reuse the shared date/time, duration, model-label, side-glyph, portrait and
   rune helpers instead of formatting locally.
5. Put user-visible copy in the i18n dictionaries using the established
   terminology; do not hard-code a second English string in a component.

## Themes and display scaling

- Every surface must work in all four UI themes: **The King's Table**,
  **Iron Ash**, **Saga Hall** and **Classic Table**.
- Use theme variables/tokens, never fixed hex colours for text, surfaces, borders
  or status states. Board and token artwork follow the appearance catalog.
- Respect the independent board, token and interface-scale settings. Do not
  hard-code pixel sizes that ignore display scaling.
- Preserve first paint: initialize appearance before rendering and avoid a flash of
  the wrong theme.
- Do not modify runtime assets (boards, tokens, portraits, runes) as part of an
  unrelated documentation or UI change.

## Responsive layout

- Support phone, tablet, desktop and 4K viewports. Reflow rather than shrinking
  text below legibility.
- On phones, the play screen keeps the board in place while the Moves / Analytics /
  My games panel switches below it; landscape places the panel beside the board.
- No horizontal page overflow. Tables become labelled cards or scroll within a
  bounded region with fixed row/column headers where appropriate.
- Test short viewports and browser chrome; avoid nested scroll containers.
- Mobile emulation is not physical-device validation. State that limitation.

## Accessibility

- Preserve semantic headings, landmarks, table captions, form labels and
  `aria-current` navigation state.
- Keyboard operation must cover selection, dialogs, tabs, tooltips, replay and
  focus return. Escape closes transient surfaces; focus is restored to the opener.
- Use `role="alert"` / `role="status"` appropriately and never move focus for a
  notification.
- Provide accessible alternatives for visual-only structures (for example a
  tournament bracket list) and data tables for charts.
- Maintain contrast in every theme and at every scale; do not rely on colour alone.

## Terminology and content

Use the current English labels: **The Great Hall**, **Battlefield**, **Online
Play**, **Statistics**, **Trials of Valor** (Leaderboard, Player tournaments,
Leagues), **Hall of records**, **Hall of Renown**, **My Profile**, **AI Workshop**
(Training, AI testing, Models, Workers). Do not reintroduce retired labels such as
"Dashboard" for The Great Hall or "AI workshop" as a separate navigation item.

Distinguish rated online games, local 1v1 and single-player. Never present an
unfinished game as a draw or an uncalibrated winning chance as a measured
probability. Do not show an unearned rank, season reward or opponent.

## Security and data

- The backend enforces authorization; hidden controls are not a security boundary.
- Never render email, private birth dates, CSRF tokens, worker tokens or private
  moderation data in a public or unrelated authenticated surface.
- Do not trust client-supplied asset paths, model IDs or filesystem paths. Use
  allowlisted IDs and server projections.
- Preserve optimistic version guards and do not retry a mutation blindly.

## Verification

- Unit tests for logic and rendering states; Playwright for real browser flows.
- Check all four themes and EN/DE for a changed visible surface.
- Check phone and desktop; add 4K or 200% zoom where layout risk exists.
- Update the affected [interface documentation](../game/interface/README.md) and
  screenshots when labels, layout or controls change.
- Do not claim physical Safari, Firefox or WebGPU validation from Chromium mobile
  emulation.

## What not to do

- Do not redesign the application as part of a documentation or cleanup change.
- Do not add a second modal, notification or theme system.
- Do not weaken an accessibility check or skip a browser test to make a change pass.
- Do not replace a shared component with a page-local copy because it is convenient.
