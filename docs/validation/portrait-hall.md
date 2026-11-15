# Portrait Hall implementation and acceptance

Adds the authenticated `/portraits` gallery to the four-theme Arena. It uses the
existing portrait/achievement authority; no schema, achievement, XP, currency or
new art is introduced. [Progression](../PROGRESSION.md#portrait-hall-and-player-artwork)
explains server confirmation and permanent awards.

The gallery shows selected, unlocked, ready-to-claim and locked portraits, actual
progress and all achievements including those without portrait rewards. Explicit
confirmation and selection use existing CSRF-protected APIs. Mutation generations,
abort controllers and account-keyed poll state reject stale updates/notices. Profile
and Dashboard link to the gallery. Published leaderboard/online DTOs include only
the effective portrait ID, batch-resolved with default/retirement fallback.

## Browser evidence

Inspected all four themes on desktop and phone in English and German. The browser
spec checks real catalog assets, server-shaped progress/confirmation/selection,
keyboard actions, paragraph contrast >=4.5:1, no horizontal overflow, profile and
Dashboard propagation and links. It scrolls/decode-checks all twelve lazy artworks
before full-page capture. The standalone backend tests drive real awards/selection;
visual tests use controlled authoritative responses for all four collectible states.

- [Desktop four-theme contact sheet](../screenshots/portrait-hall/desktop-themes.jpg)
- [Mobile four-theme contact sheet](../screenshots/portrait-hall/mobile-themes.jpg)
- [King's Table 1920×1080 / Default](../screenshots/portrait-hall/kings-table-1920.webp)
- [King's Table 3840×2160 / Large](../screenshots/portrait-hall/kings-table-3840.webp)

| Theme | 1920×1080 | 3840×2160 |
| --- | --- | --- |
| King's Table | [Capture](../screenshots/portrait-hall/kings-table-1920.webp) | [Capture](../screenshots/portrait-hall/kings-table-3840.webp) |
| Iron & Ash | [Capture](../screenshots/portrait-hall/iron-ash-1920.webp) | [Capture](../screenshots/portrait-hall/iron-ash-3840.webp) |
| Saga Hall | [Capture](../screenshots/portrait-hall/saga-hall-1920.webp) | [Capture](../screenshots/portrait-hall/saga-hall-3840.webp) |
| Classic Table | [Capture](../screenshots/portrait-hall/classic-table-1920.webp) | [Capture](../screenshots/portrait-hall/classic-table-3840.webp) |

The contact sheets are viewport reviews; native images are full-page captures.
The CI `arena-final-visual-acceptance` artifact also retains all 32 full gallery
captures (four themes × two languages × four desktop/phone sizes) before later
browser configurations clean temporary output. The added sidebar current-page
marker is covered by a further focused desktop run and the King's Table images.
Physical Safari/device testing and the final all-route seven-breakpoint/200% zoom
acceptance remain separate completion-program work, not claimed by these captures.

## Local validation

- Gallery browser matrix: **8 passed**; focused sidebar-current-page case: **1 passed**.
- Gallery unit tests: **11 passed**; integration selection covering gallery,
  Dashboard, Profile and leaderboard: **72 passed** before the last two additional
  gallery cases; final full CI supplies complete counts.
- Progression/ratings/online core/online API/public API: **212 passed**, one existing
  Starlette/httpx deprecation warning. New test checks selection propagation,
  private-field exclusion, retirement fallback and one batched selection lookup.
- Frontend formatting, TypeScript, production build, Ruff, mypy and docs: passed.
- Existing large-entry-chunk advisory remains; splitting is a separate workstream.

An initial new backend test called `list` instead of `listing`; corrected before
the clean 212-case run. An initial browser computed-style read detached during
React replacement; the assertion now retries connected elements with the unchanged
4.5:1 requirement. No layout or accessibility threshold was lowered.

## Full-CI correction pass

Initial CI found six backend exact-projection/restore assertions not yet updated
for published portraits, and two transition-time browser measurements. Unavailable
accounts now retain their original anonymous seat shape without portrait metadata;
active public DTO allowlists explicitly include only `portrait_id`. The restore
fixture captures current profile metadata after selecting the retained portrait,
then still compares the full restored game and every retained table.

The browser measurements keep every geometry threshold and expected card count,
retrying a settled resize and returning false while a board is temporarily absent
instead of throwing out of `expect.poll`. Corrected affected backend suites:
**69 passed**, one existing warning. Corrected browser regressions: **3 passed,
1 intentional desktop skip**. Complete CI is rerun on the final head.
