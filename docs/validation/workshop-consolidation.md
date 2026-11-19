# Workshop presentation and bounded job rendering

[Documentation index](../README.md) · [Training](../TRAINING.md)

Training and AI comparisons reuse Arena materials, typography and semantic error
surfaces. Task navigation opens existing creation/tuning, monitoring, models and
remote-worker controls. Advanced settings, resume, import/export, model protections
and backend training/tuning remain intact. The accessible page heading precedes
the visual introduction. AI comparisons remain separate from human tournaments.

Job navigation renders 20 rows per page and 20 options plus a placeholder or
selected off-page option. Every matching job remains reachable. Keyboard selection,
filtering/sorting, Show selected job and the selected dashboard are preserved.
Polling does not force the current page back to the beginning. Singular job/model
summary labels are corrected in English and German.

## Review follow-up after main integration

The PR now includes main `b630273` through merge commits, retaining its public
spectator, Portrait Hall, polling, localization, Norse terminology, workspace
navigation, browser helpers and security toolchain changes, plus the isolated
browser-analysis feasibility spike. The diff against that main revision remains
scoped to Workshop presentation, navigation, bounded rendering and their
validation.

Task links and the comparison return link use `surfaceLink`, which delegates to
the shell's existing `navigate` function. Same-hash clicks dispatch navigation
again, reopening collapsed Models/Workers and moving focus to the section.
Modifier clicks, external links, download links and other browsing targets retain
native anchor behavior. Both cross-route links use `workspaceDestinations()` to
respect the separate training and tournament permissions; API checks are unchanged.

The navigator persists its clamped page when polling removes pages, so expansion
does not restore a stale page. Unchanged polling does not reset the browsed page;
explicit selection and Show selected job still follow the selected job. Regression
cases cover 0, 1, 20, 21, 57 and 1,000 jobs, shrink/expand transitions and the selected
dashboard's history, tab and iteration range.

The [interactive browser matrix](../../frontend/tests/workshop-consolidation.spec.ts)
adds populated/completed/active comparisons, standard and fixed-role dialogs,
advanced creation/tuning and resume/import/export availability at 390 and 1920 CSS
pixels, in EN/DE and every theme. Behavioral and geometry assertions accompany
the captures; the original contrast, keyboard, geometry and timeout checks remain.
Setup and resume dialogs restore keyboard focus using the shared modal convention.
Separate desktop/mobile workflows exercise repeated task/shell links, Back/Forward,
cross-route navigation without a document request and all three permission
combinations. Existing real-backend tournament and training browser workflows
remain part of the complete suite.

The reproduced DOM measurement remains 20 rows, 21 options and 191 descendants
against 1,000 rows, 1,001 options and 8,027 descendants in the original baseline.
Exact complete-suite results and any remaining limitations are recorded on
[PR #92](https://github.com/freeDom-/hnefatafl/pull/92). The records below describe
the original PR validation before this follow-up, rather than current CI results.

## Focus-restoration corrections

Show selected job opens the collapsed desktop disclosure before restoring focus
and never falls back to an unrelated row. Each activation creates one bounded
request tied to the intended job id. Before applying focus the request confirms
that the job still exists, that the current selection still matches that id, and
that the intended target is connected, rendered and visible. A job that vanishes
or a selection that moves on discards the request safely, and a handled-token
guard prevents a completed or cancelled request from running again when later
polling delivers fresh summaries. The disclosure is uncontrolled: it opens on
desktop at mount and only Show selected job reopens it, so filtering, sorting,
page navigation and polling never expand it. A single bounded scan over the 20
rendered rows finds the row; no hidden row copies, extra requests or polling
effects are added.

The desktop branch reuses the existing `(max-width: 900px)` layout query rather
than adding a second breakpoint. On mobile it focuses the visible native selector
instead, including after a mobile-to-desktop resize that left the disclosure
collapsed. `scrollIntoView({ block: 'nearest' })` keeps the restored row
reachable in the scrollable list. The selected job, dashboard tab, iteration
range and training history are unchanged; restoration does not call `onSelect`
or refetch detail data.

Regression coverage adds the collapsed desktop disclosure, keyboard activation,
mobile-to-desktop resizing, a filtered-out selection, a job removed or a
selection changed during a pending request, no unrelated fallback focus, no
delayed focus stealing during later polling and no redundant selection events or
detail requests. The browser suite adds Playwright visibility and focus
assertions for the closed native details element, which jsdom cannot model.

Focused validation on this head:

| Check | Result |
| --- | --- |
| `TrainingJobNavigator` unit suite | 28 passed |
| Workshop DOM benchmark | 1 passed; 20 rows / 21 options / 191 descendants preserved |
| Comparison and focus Chromium regressions | 52 passed; mobile native-selector focus 1 passed |
| Security script tests | 25 passed |
| Frontend TypeScript (`tsc --noEmit`) | passed |
| Prettier check on changed files | passed |

The navigator suite's 1,000-job case exceeds the 20-second default timeout on this
shared host even for the reviewed component; it passes locally with unchanged
assertions under an extended run timeout and in CI.

Fresh CI on the corrected head `2c9a0ce` ([run 44486474020](https://github.com/freeDom-/hnefatafl/actions/runs/44486474020)):
the `python` job passed with **3,533 backend tests passed and 16 skipped** plus
ruff, mypy and `make docs` (152 files, 1,163 links); the `browser` job passed with
**1,499 unit tests** (103 files), **12** benchmarks, **76** public-production,
**620 main e2e passed / 200 skipped**, **14** registration, **24** online and
**34** personal browser tests, plus `test:scripts` **25 passed**, format,
typecheck and build; the `node22` job passed typecheck, **1,499 unit tests** and
build; and the `security` job passed with clean full and production-only lockfile
audits using no exceptions. All four jobs are green on the merged head.

Previous CI on head `25c9cfc` ([run 44448100804](https://github.com/freeDom-/hnefatafl/actions/runs/44448100804)):
the `python` job passed with **3,531 backend tests passed and 16 skipped** plus
ruff, mypy and `make docs` (152 files, 1,163 links); the `browser` job passed with
**1,497 unit tests** (103 files), **76** public-production, **572 main e2e passed /
152 skipped** including the four new focus regressions, **14** registration,
**24** online and **34** personal browser tests, plus `test:scripts` **25 passed**,
format, typecheck and build; the `node22` job passed typecheck, **1,497 unit
tests** and build; and the `security` job passed with clean full and
production-only lockfile audits using no exceptions. All four jobs were green.

Historical CI on head `0b40aea` ([run 44429024174](https://github.com/freeDom-/hnefatafl/actions/runs/44429024174)):
the `python` job passed with **3,531 backend tests passed and 16 skipped** plus ruff,
mypy and `make docs`; the `browser` job passed with **1,491 unit tests**, **76**
public-production, **568 main e2e passed / 148 skipped**, **14** registration,
**24** online and **34** personal-area browser tests, plus format, typecheck and
build; the `node22` job passed typecheck, **1,491 unit tests** and build; and the
`security` job passed with clean full and production-only lockfile audits using no
exceptions. All four jobs are green.

Compatibility limits: the automated matrix remains Chromium. Safari/WebKit and
physical-device verification were not performed, and the explicit viewport widths
are not real 200% browser zoom. These remain unverified rather than claimed.

## AI comparison spacing corrections

The AI comparison intro panel is the first child of `main`, which carries no top
padding, so it sat directly against the application header. The same missing
rhythm left the intro touching the tournament workspace below it and stacked the
two empty-state action buttons with no gap. The training page shared the
header-spacing cause.

The shared workshop intro now reuses `--gap-card` for its top spacing, and the
tournament workspace uses the same token for its top margin; a transient notice
uses `--gap-card` above and below so the rhythm holds when it appears. The
empty-state actions are grouped in `.tournament-welcome-actions` with the shared
`--gap-inline` between them. No hardcoded margins, theme-specific overrides or
new layout system were introduced; the existing containers and tokens are reused.

The visual acceptance matrix now asserts, in every theme and language at 320,
768, 1920 and 3840 CSS pixels, that the header-to-intro and intro-to-workspace
gaps are present, that the intro and workspace share the same left/right edges,
and that the page does not overflow horizontally. A populated-state matrix adds
completed and running tournaments at 1920 and checks the same rhythm plus aligned
history/detail panels. The eight committed training captures at 1920 and 3840
were regenerated after the training intro spacing changed. Local focused runs on
this head: the eight training captures passed, the 32 empty-state comparison
cases passed, the 16 populated/running cases passed and the focus regressions
passed.

### Integrated security corrections

The registry now reports zero findings, so the earlier `GHSA-fc5q-39xc-vjpf`
acceptance became unused and the fail-closed gate correctly rejected it. That
correction was reviewed and merged to main (PR #100). A follow-up hardening
(PR #102) requires string approval identifiers before the GitHub-handle, GHSA and
CVE patterns are applied, so `RegExp.test()` can no longer coerce arrays, numbers
or booleans into matching values. `origin/main` `711978e` is merged into this
branch, so all 25 security script tests and the `security` job pass here. This PR
does not modify audit governance, the exception file or dependency versions.

## Measured rendering

Identical 1,000 job summaries, no filter or selection, React/jsdom render:

| Measurement | Main baseline | Candidate |
| --- | ---: | ---: |
| Job rows | 1,000 | 20 |
| Select options | 1,001 | 21 |
| Navigator descendants | 8,027 | 191 |

[Recorded result](../benchmarks/workshop-dom.json). Reproduced on the integrated
branch: one benchmark test passed. Run `node frontend/benchmarks/workshop-dom.mjs`.
The harness reads the original component from reviewed main `2579a86`, uses the
same fixture and removes its temporary source files. The 97.62% smaller descendant
count is **not a frame-time, memory, server or network benchmark**. Filtering and
sorting still process all supplied summaries; server paging is not introduced.

## Original validation and visuals

Integrated focused unit suite: **134 passed, five files**. TypeScript, formatting
and build passed. Candidate browser validation: eight new presentation cases
passed across the combined run and isolated King's Table retry; four existing
scroll/polling/keyboard regressions passed. An initial matrix timeout under CPU
contention was rerun without raising its 90-second limit. Original sticky/scroll
and keyboard assertions remain. Pagination tests visit the actual last/first page.
Full repository CI is required separately on the PR.

The **88 captures** cover all themes and EN/DE: Training at 320, 390, 768, 1024,
1366, 1920, 3840; comparisons at 320, 768, 1920, 3840. Checks cover overflow,
bounded rows/options, restoring selection, task/deep links and contrast >=4.5:1.
Desktop targets assert Default at 1920 and Large at 3840. CI retains originals
before later configurations clear output.

| Theme | Contact | 1920 | 3840 |
| --- | --- | --- | --- |
| King's Table | [Reviewed](../screenshots/workshop/contact-kings-table.webp) | [Capture](../screenshots/workshop/kings-table-1920.webp) | [Capture](../screenshots/workshop/kings-table-3840.webp) |
| Iron & Ash | [Reviewed](../screenshots/workshop/contact-iron-ash.webp) | [Capture](../screenshots/workshop/iron-ash-1920.webp) | [Capture](../screenshots/workshop/iron-ash-3840.webp) |
| Saga Hall | [Reviewed](../screenshots/workshop/contact-saga-hall.webp) | [Capture](../screenshots/workshop/saga-hall-1920.webp) | [Capture](../screenshots/workshop/saga-hall-3840.webp) |
| Classic Table | [Reviewed](../screenshots/workshop/contact-classic-table.webp) | [Capture](../screenshots/workshop/classic-table-1920.webp) | [Capture](../screenshots/workshop/classic-table-3840.webp) |

Existing chunk warning remains: entry 1,017.90 kB minified /311.40 kB Vite gzip.
Route splitting is separate. Evidence shows populated Training and empty comparison
pages, not every tuning/resume/import/worker dialog. Physical devices/Safari,
true 200% zoom and merged cross-route acceptance remain later work. No backend,
model, checkpoint, schema or dependency changes; no release or merge.

## Original integrated browser verification

The matrix now isolates each theme, language and viewport, with one locale
initializer and explicit document-language assertions. Four separate keyboard
workflows preserve the original paging, selection and model/worker link checks.
The same 88 visual combinations retain every geometry/contrast assertion. Desktop
screenshots are native 1920×1080/Default and 3840×2160/Large; narrower screenshots
remain full-page. Capture-only animations are disabled for reproducible images.

The complete integrated run had **91 passed and one 768px timeout**
during a managed tool-runtime restart. Its unchanged isolated recheck passed in
1.9 seconds (6.8-second run). Earlier native-desktop validation had 31 passed and
one 4K image-generation stall; its unchanged isolated recheck passed in 2.9 seconds.
The original full-page 4K image-generation timeout and earlier protocol stalls are
retained as failures, not hidden by increasing the 90-second test or 10-second
assertion limits. All 88 current captures are preserved. Full CI must establish a
clean full-suite result before merging.

First full CI: backend verification passed; **1,319 unit tests** passed and main
browser validation had **446 passed, 111 skipped, one failure**. The failure was
the existing shared typography contract: new display introductions lacked the
established `arena-hero-headline` marker that distinguishes display headings from
normal sections. Both introductions now reuse that class. The shared typography
selector and every assertion remain unchanged; the scoped Workshop styling keeps
the reviewed appearance. The full local typography sweep progressed without the
original mismatch but reached its existing180-second limit later while applying
interface scale. That timeout is retained; updated full CI is required.
