# Frontend feature splitting

[Documentation index](../README.md) · [Architecture](../../ARCHITECTURE.md)

## Delivered behavior

Keep the Arena shell and live board eager; load Workshop, AI tournaments,
model-bearing setup, archive/review, online management, profile/statistics,
Markdown rules and analysis presentation when requested. No Pyodide, neural
runtime or model download enters the initial graph. The canonical analysis hook
retains its existing behavior independently of presentation loading.

The shared loader caches module promises, never account data. Loading has an
accessible status; import/render failures offer Retry and Reload. Unmount guards
prevent stale updates. Existing permission checks run before requesting Workshop
code. Browser tests cover English/German errors, navigation during failure,
recovery and frozen gameplay clocks.

The New Game flow keeps its shared modal shell eager and defers only the form, so
close, Escape and focus management work during loading and failure.

## Bundle evidence and budgets

Measured baseline is main `2579a86b82ed1eca91f39e7d8064196597e873ed`.
Compression uses gzip level 9 separately for each file; this is a transfer-size
proxy, not deployment compression, parse time or total application size.

| Initial graph | Baseline bytes | Candidate bytes | Reduction |
| --- | ---: | ---: | ---: |
| JavaScript raw, including first paint | 1,015,562 | 537,391 | 47.08% |
| JavaScript gzip | 309,626 | 170,276 | 45.01% |
| CSS raw | 176,657 | 113,605 | 35.69% |
| CSS gzip | 32,599 | 22,006 | 32.49% |

The build walks transitive eager manifest imports, deduplicates assets, checks
missing files, and enforces JS 600,000 raw/200,000 gzip and CSS 150,000 raw/35,000
gzip budgets. Dynamic features are excluded until requested. Vite's existing
500 kB chunk warning remains visible; its threshold was not raised.

Reproduce from a clean committed checkout with Node 22+ and installed Chromium:

```sh
npm --prefix frontend run build
node frontend/benchmarks/feature-network.mjs /tmp/feature-network.json
```

The production probe verifies matching clean Git revisions and an asset
fingerprint before serving local production assets. It inventories actual initial
JavaScript requests and verifies Training arrives only after Workshop navigation.
API fixtures allow GET only. This proves the production loading graph; it does
not measure authentication, backend capacity or real-network loading speed.
Source archives remain buildable but cannot produce verified benchmark evidence.

## Validation and limitations

Local formatting, TypeScript and production build passed. Four bundle-guard and
eight build-identity Node tests passed. Focused Vitest: 94 tests across loader,
model selection, analysis, App and Dashboard; another 26 online/account/navigation
tests passed. The feature-loading browser suite passed all 10 cases.

Gameplay/desktop/history browser suite: 28 passed, 3 intentional skips and one
real-training startup timeout. All 22 Play auto-resume/Browser Back cases passed.
The unchanged isolated training/resume/model-protection case then passed in 1.4
minutes. The failed run completed real training/checkpoint writes after unusually
slow startup; it is not reported as an all-green suite. No timeout or gameplay,
geometry or accessibility assertion was weakened.

Clean production revision `64819d6ebd16d61f46f8873676b994c1c91bbb4a` passed
[the actual request probe](measurements/frontend-feature-network.json): only the
1,862-byte first-paint script and 535,529-byte application entry arrived initially.
Workshop navigation subsequently requested Training and its four shared chunks;
all nine excluded feature families were absent initially. Source/build Git
identity and the complete asset fingerprint matched. Full branch CI was later
recorded green: run `44438662412` passed all four jobs at the reviewed head
`7558e7d` (see [CI](#ci)). These results do not establish physical-device
performance, merged cross-feature visual acceptance or release readiness.
Separate tournament, spectator and league work is outside this branch.

## Reviewed main integration, 18 November

Merged main `0c5f270` into the existing branch without rewriting shared history.
Preserved the newer progression provider, tiered achievements, human tournament
scheduling, public entry isolation, polling and analysis-admission safeguards.
The extracted analysis hook matches main's implementation exactly.

Verified clean integrated runtime `7c48153` produced this
[actual production request inventory](measurements/frontend-feature-network-integrated.json):
565,320 bytes JavaScript / 180,407 gzip. The complete Dashboard eager CSS graph
is 114,187 bytes / 23,803 gzip. These totals include the immediately selected
AuthenticatedArena graph, shared shell chunk and first-paint script. The budget
checker now explicitly counts that route-selected dynamic entry, with tests
rejecting missing entries and oversized authenticated graphs. Public-only entry
size is not substituted for authenticated Dashboard size. Earlier measurements
above remain historical results against the original PR #84 baseline.

Focused integration verification: TypeScript, 69 Vitest tests in three files,
12 build-identity tests, six bundle-budget tests and documentation passed.
Both previously CI-failing field-error browser cases passed (desktop and phone,
22.6 seconds total) after using the existing initial-job readiness helper before
reading disclosure state. Error text, focus, contrast and geometry checks remain.
The production build and clean-source/fingerprint request probe passed. Full
integrated CI is recorded green at the reviewed head `7558e7d` by run
`44438662412`; the earlier run had 363 main browser cases pass and two
field-readiness failures, so only the later run is green.

## Review follow-up: modal shell, recovery and network evidence, 19 November

The deferred New Game component used to own its modal, so a pending or failed
import rendered a bare section without the close button, Escape handling, focus
management or cancellation. `NewGame` is now an eager shell that renders the
shared `Modal` immediately and defers only `NewGameForm`. Loading and import
failure feedback render inside the dialog; cancellation unmounts the form so a
late import cannot reopen a closed dialog, and navigation closes a setup dialog
that belongs to another route. Query-driven setup and ruleset selection, model
controls, validation and submission are unchanged.

The production probe now derives the expected initial graph from the Vite
manifest (`scripts/bundleGraph.mjs`, shared with the budget checker), compares it
with the actual JavaScript and CSS responses, verifies status and content type,
detects delayed unexpected assets after Dashboard network idle, verifies deferred
chunks arrive on first use, checks public/authenticated entry isolation, and
proves Retry recovery by aborting the real hashed New Game form chunk. The budget
tests reject incorrect manifests, missing assets, shared eager dependencies and
unexpected delayed loads.

Fresh clean production evidence at `59dddff` (source revision and fingerprint in
[the measurement](measurements/frontend-feature-network-review.json)):

| Initial graph | Integrated reference | Review measurement | Difference |
| --- | ---: | ---: | ---: |
| JavaScript raw | 565,320 | 565,637 | +317 (+0.06%) |
| JavaScript gzip | 180,407 | 180,533 | +126 (+0.07%) |
| CSS raw | 114,187 | 114,187 | 0 |
| CSS gzip | 23,803 | 23,803 | 0 |

The increase is the eager dialog shell (modal, close, Escape and focus) that
keeps the creation flow usable while the form is deferred. Budgets are unchanged.

Validation: 1,465 Vitest tests, 33 script tests, 12 benchmark tests, a clean
`audit:ci`, and 40 focused browser cases (`new-game-modal` 22,
`bundle-loading` 10, `game-setup` 8) across desktop and mobile in English and
German. The production build and probe passed with Retry recovery and public
entry isolation. This reviewed head `7558e7d` was verified by run `44438662412`,
which passed all four GitHub Actions jobs; that result is historical evidence for
the state before the final hardening below.

## Final hardening: setup lifecycle and production recovery, 19 November

### Asynchronous New Game setup lifecycle

`useNewGameSetup` owns the setup operation with a generation counter, an
`AbortController` and an ownership check. `App` starts it from the Dashboard's
"Play vs AI" action or the `?setup=single-player` deep link. The load callback
returns the fetched options, and the hook publishes them through an owned commit
only after it verifies the generation and cancellation, so a stale promise
cannot overwrite rulesets or profiles even if it resolves despite an abort. Only
the newest generation may publish, open the dialog or report a failure.
Cancelling, navigating (including Browser Back/Forward), switching account,
logging out or unmounting advances the generation and aborts the pending option
request; the hook's unmount cleanup does not depend on a later route or account
effect and does not set state. Route equality remains a secondary guard.

The dialog records the generation that opened it (`session()`), and
`owns(session)` binds a game-creation completion to that specific operation
rather than to whichever dialog is open when the POST settles. `NewGameForm`
checks the same guard before persisting preferences, notifying, publishing game
state, closing the dialog or navigating, in addition to its existing
mounted-state and StrictMode protections. A dismissed or superseded creation
cannot affect a newer dialog; the committed server game is preserved in the
account's history and the client never retries or duplicates the POST.

Tests: `useNewGameSetup.test.tsx` (overlapping, reverse-order, stale commits,
session-bound ownership, cancellation, unmount abort and late rejection,
StrictMode), `NewGameSetup.test.tsx` (repeated clicks, navigation away, navigate
away/back, stale options not overwriting newer state, keyed account remount,
explicit reopen, navigation during creation, late completion from an older
generation, preference preservation, query-driven creation, StrictMode) and two
`new-game-modal` browser cases (repeated pending clicks, navigation while POST
`/games` is pending). The existing modal, Escape, focus and deferred-feature
tests are retained.

### Production asset failure and recovery

The production probe (`benchmarks/feature-network.mjs`) verifies against the
real hashed assets and their manifest relationships:

| Scenario | First attempt | Recovery |
| --- | --- | --- |
| New Game form JavaScript chunk | aborted | Retry |
| Transitive preload dependency (`ConfigImportButton`) | aborted | Retry |
| Training deferred stylesheet | aborted | Reload application |
| Training stylesheet retried while failed | aborted | error panel retained, no mount |
| Navigate away and back after stylesheet failure | aborted | error panel retained, no mount |
| Permanently missing hashed form chunk | aborted | Reload application |

When a deferred stylesheet fails, Vite dispatches `vite:preloadError` and keeps
the failed link in the document; a later module import can still succeed because
the helper skips the already-attempted link. `deferredFeature` records the
failed stylesheet per feature and refuses to mount or cache the module while the
link is unresolved, so Retry keeps the accessible error panel with the explicit
Reload action instead of rendering an unstyled feature. The Reload case verifies
computed layout after recovery (`.workshop-global` computes `display: flex`).
Only the existing promise-based loader is used: no automatic reload loops,
cache-busting imports, remote scripts or weaker CSP.

Remaining browser limitation: a failed stylesheet is only retried by a full
document reload; same-document Retry deliberately keeps the error state. A
permanently missing asset is recovered by unblocking it and reloading the
document, which models a fixed or updated deployment; a missing asset with no
new deployment cannot recover.

### Asset observation and forbidden-module detection

The initial window no longer depends on `networkidle`. The probe waits for the
Dashboard to leave its loading state and for every manifest-graph asset to
produce a response or a recorded failure, then observes for a bounded 750 ms
settle. It records JavaScript and CSS requests, responses, HTTP status, MIME type
and requests that fail before any response, and rejects any failed asset in the
initial window. A Vite `writeBundle` plugin writes a build-only module ownership
map to `frontend/.cache/bundle-modules.json` (outside `dist`, never shipped and
never fingerprinted). The probe checks every eager chunk's modules against the
prohibited feature list, so a feature folded into a shared eager chunk is caught
even without its filename prefix; intentionally shared lightweight dependencies
remain allowed. The filename-prefix check is retained as an independent
cross-check. Script tests cover folded features, shared dependencies, failed or
missing responses, and public/authenticated entry isolation.

Limitations: the finite window cannot prove that an arbitrarily future scheduled
import never occurs, and the ownership map must be regenerated by the build.

### Final bundle evidence

Clean production revision `30353c8fce37230f82f6cf9044f72167de857e0e`
(fingerprint `5cf02c0d73aefc4ef1352637e9803246bb0fe5983a36295234cab052c70849af`),
[measurement](measurements/frontend-feature-network-hardening.json):

| Initial graph | Integrated reference | Review (`59dddff`) | Final (`30353c8`) | Budget |
| --- | ---: | ---: | ---: | ---: |
| JavaScript raw | 565,320 | 565,637 | 567,076 | 600,000 |
| JavaScript gzip | 180,407 | 180,533 | 181,047 | 200,000 |
| CSS raw | 114,187 | 114,187 | 114,187 | 150,000 |
| CSS gzip | 23,803 | 23,803 | 23,803 | 35,000 |

The +1,439 raw / +514 gzip bytes over the reviewed head are the setup-lifecycle
hook, its eager shell and the stylesheet-failure guard; no budget changed. The
heavy NewGameForm, Training, Tournaments, recorded review and analysis chunks
remain deferred.

### CI

Reviewed head `946e448`: GitHub Actions run `44466537774` passed all four jobs
(python, security, node22 and browser, including `npm test`, the benchmark and
script suites, the production build and every browser suite). This is historical
evidence for the state before the stylesheet and setup-ownership hardening.

Source head `2627ee1` (the stylesheet and setup-ownership fix): run `44483899080`
passed all four jobs.

Earlier in the branch: source head `29ee2a0` passed in run `44457030473`; the
docs-only follow-up `4efda34` passed in run `44461520565`. An intermediate run
`44450317436` on `5fed3f3` failed `test:e2e` because React StrictMode's
development remount left the completion guard disabled; the guard now resets on
every mount. Historical: run `44438662412` passed all four jobs on `7558e7d`.
