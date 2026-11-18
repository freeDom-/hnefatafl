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

The PR now includes main `e717138` through merge commits, retaining its public
spectator, Portrait Hall, polling, localization, browser helpers and security
toolchain changes, plus the isolated browser-analysis feasibility spike. The diff
against that main revision remains scoped to Workshop presentation, navigation,
bounded rendering and their validation.

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

## Final focus-restoration corrections

Show selected job now transfers keyboard focus after the restored view renders:
on desktop to the selected job row, on mobile to the visible native job selector.
The responsive branch reuses the existing `(max-width: 900px)` layout query rather
than adding a second breakpoint. Focus is requested only by the explicit button
activation, so background polling, filtering/sorting, ordinary navigation and
unrelated rerenders do not move it. A single bounded scan over the 20 rendered
rows finds the row; no hidden row copies, extra requests or polling effects are
added. If the selected job disappears between activation and rendering, focus is
left untouched instead of throwing or landing on a removed node.
`scrollIntoView({ block: 'nearest' })` keeps the restored row reachable in the
scrollable list. The selected job, dashboard tab, iteration range and training
history are unchanged; restoration does not call `onSelect` or refetch detail data.

Focused validation on the corrected head:

| Check | Result |
| --- | --- |
| `TrainingJobNavigator` unit suite | 21 passed |
| Workshop DOM benchmark | 1 passed; 20 rows / 21 options / 191 descendants preserved |
| Frontend TypeScript (`tsc --noEmit`) | passed |
| Prettier check on changed files | passed |

The browser regression adds a desktop assertion that the restored row is focused
and a mobile-project assertion that the native selector is focused.

Full CI on head `3816f88` ([run 44412918517](https://github.com/freeDom-/hnefatafl/actions/runs/44412918517)):
the `python` job passed with **3,531 backend tests passed and 16 skipped** plus ruff,
mypy and `make docs`; the `browser` job passed with **1,491 unit tests**, **76**
public-production, **568 main e2e passed / 148 skipped**, **14** registration,
**24** online and **34** personal-area browser tests, plus format, typecheck and
build; the `node22` job passed typecheck, **1,491 unit tests** and build. The
`security` job is red only for the outstanding dependency correction described
below.

Compatibility limits: the automated matrix remains Chromium. Safari/WebKit and
physical-device verification were not performed, and the explicit viewport widths
are not real 200% browser zoom. These remain unverified rather than claimed.

### Outstanding security integration

The `security` job fails only because the registry now reports zero findings while
the reviewed `GHSA-fc5q-39xc-vjpf` acceptance is still present; the fail-closed gate
correctly rejects the unused exception. This is fixed by the independent security PR
targeting main, not by changing audit governance or the exception file in this PR.
Until that PR is reviewed and merged, the latest `origin/main` (and therefore the
security fix) cannot be integrated here, and the security gate stays red. No
dependency, threshold or governance change is made in this branch.

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
