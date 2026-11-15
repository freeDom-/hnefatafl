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
identity and the complete asset fingerprint matched. Full branch CI is pending. These results
do not establish physical-device performance, merged cross-feature visual
acceptance or release readiness. Separate tournament, spectator and league work
is outside this branch.
