# Frontend test-toolchain security update

Updates the development-only Vitest runner from locked 3.2.7 (declared ^3.2.4) to
pinned **4.1.12**, with the corresponding locked dependency graph. Application
runtime dependencies, Vite, React, TypeScript, Playwright and jsdom are unchanged.
No game, database, route, configuration migration or production code changes.

## Reason and scope

Fresh baseline npm audit reported three affected development packages: Vitest
(critical), tinypool (critical) and @vitest/mocker (moderate). These are affected
package counts, not unique advisory counts. Production-only audit was already zero.

The selected 4.x release resolves the reported mocker advisories and removes the
old tinypool/vite-node graph. It is a smaller major-line migration than npm's
suggested latest-5.x force upgrade, and supports the repository's Node 22.12+/Vite 7
configuration. No `--force` or blanket test-reset flags are used.

Primary advisory references:

- [Mocker file-read issue](https://github.com/advisories/GHSA-82fw-gwwq-j7x9)
- [Mock registration injection](https://github.com/advisories/GHSA-qxg6-89mg-rqv8)
- [Tinypool prototype pollution](https://github.com/advisories/GHSA-5gmw-xhrv-c9v3)
- [Tinypool execution issue](https://github.com/advisories/GHSA-85c8-ppgw-ccpr)
- [Vitest 4 migration guidance](https://v4.vitest.dev/guide/migration.html)

## Fresh local verification

Node 24.22.2, isolated checkout; exact commands:

| Command | Result |
| --- | --- |
| `npm --prefix frontend ci` | Passed; 247 packages audited, zero vulnerabilities |
| `npm --prefix frontend test` | **1,312 passed, 93 files**, 175.37 seconds |
| `npm --prefix frontend run format:check` | Passed |
| `npm --prefix frontend run typecheck` | Passed |
| `npm --prefix frontend run build` | Passed, same baseline production asset hashes |
| `npm --prefix frontend audit --json` | **Zero findings, all dependencies** |
| `npm --prefix frontend audit --omit=dev --json` | **Zero findings, runtime dependencies** |

No unit-test assertions, mocks, timeouts or snapshots were modified to obtain this
result. Constructor/hoisted mocks, fake timers and restoration behavior are
covered by the full existing suite rather than assumed compatible. The complete
repository/browser CI is required on the PR; its exact result is recorded there.

The existing large-entry-chunk advisory and npm's esbuild install-script review
notice remain. They are not new runtime vulnerabilities and are not hidden by
raising limits or suppressing warnings. Security findings are time-sensitive;
repeat audits at release preparation instead of treating this record as permanent.

## CI audit enforcement and Node.js 22 compatibility

`.github/workflows/ci.yml` runs `npm run audit:ci` in a dedicated `security` job
against the committed lockfile, without installing dependencies. It tolerates
only the recorded, unexpired owner-approved advisory described below and fails
for any other finding, any runtime-reachable package or a dirty production audit.
No `--force`, blanket ignore list or relaxed threshold is used, and the browser job
keeps its existing unit, build and Playwright steps.

A dedicated `node22` job exercises the minimum supported Vite 7 line
(`node-version: "22.12"`) with the same npm cache configuration: `npm ci`,
`npm run typecheck`, `npm test` and `npm run build`. The full Playwright browser
suites stay on the existing Node 24 `browser` job so the compatibility check does
not duplicate them.

Local Node 22.12.0 (the pinned CI version) and Node 22.22.1 verification
(isolated checkout, fresh `npm ci`; unit suite limited to the CI runner worker
count):

| Command | Result |
| --- | --- |
| `npm ci` | Passed; 246 added, 247 audited, zero vulnerabilities |
| `npm audit --audit-level=moderate` | **Zero findings** (exit 0) |
| `npm run typecheck` | Passed |
| `npx vitest run --maxWorkers=4` | **1,312 passed, 93 files** |
| `npm run build` | Passed |

The full unit suite also passes on Node 24.11.1 on the same host, so the earlier
full-suite timeouts on a high-core development host are host oversubscription (two
long async table tests exceed the 20 s per-test timeout when more workers run than
CPU capacity supports), not a Node 22 or Vitest 4 regression.

## Vitest worker count

Vitest 4 already bounds `maxWorkers`: it defaults to
`max(availableParallelism() - 1, 1)`, not to an unbounded value. The reported
timeouts occur when a host reports more CPUs than the run can actually schedule,
so the default spawns more workers than the machine can service. The full unit
suite was measured on the 4-CPU verification host at several explicit worker
counts:

| Workers | Node 22.23.4 | Node 24.22.2 |
| --- | --- | --- |
| 3 (default) | **1,312 passed**, 199 s | **1,312 passed**, 179 s |
| 4 | **1,312 passed**, 203 s | not rerun |
| 8 | 1,310 passed, 2 failed, 220 s | 1,311 passed, 1 failed, 205 s |
| 16 | 1,298 passed, 14 failed, 229 s | not rerun |

The failures are the same two long async table tests exceeding the unchanged
20 s per-test timeout, not assertion differences, and they reproduce on both
Node lines. Oversubscription is also slower than the default, so more workers is
not a performance trade-off worth making. Durations are wall-clock on the
verification host and move with host load.

No `maxWorkers` bound is committed. The default already tracks the reported CPU
count, and no static cap can correct a host that over-reports capacity: a cap low
enough to keep the affected host green (four) would impose a large parallelism
loss on genuine high-core hosts, and a higher cap would not prevent the reported
failure. `frontend/vite.config.ts` therefore keeps Vitest's default and relies on
Vitest's built-in override for loaded or CPU-limited hosts:

```sh
npm --prefix frontend test -- --maxWorkers=4
```

The 20 s `testTimeout` is unchanged; contention is not hidden by raising it.

## Browser resize correction

The first full CI run passed the Python job but exposed one browser timing failure
in Play mode discovery (354 passed, 19 skipped, one failed). The test now waits for
the existing geometry assertions after a viewport resize instead of reading the
transitional layout immediately. The expected card count, stacked layout and
maximum document width are unchanged. This is the same correction validated in
the Portrait Hall full CI run. The updated PR CI result is recorded on the PR.

The second full run passed Python and all 1,312 unit tests; its main browser suite
had 354 passed, 19 skipped and one failure when a board temporarily disappeared
during navigation. The existing geometry poll now retries that transient missing
node instead of throwing before it can measure. Width/height assertions remain
unchanged, matching the correction already green in Portrait Hall CI.

## picomatch ReDoS advisory (GHSA-fc5q-39xc-vjpf)

This section supersedes the earlier zero-finding status above. A fresh audit
reports five affected development packages from one advisory: picomatch through
4.0.7 is vulnerable to a ReDoS through single-branch repeated extglob patterns
(CVE-2026-112366, high severity). GitHub keeps the advisory active; Snyk revoked
its entry after a maintainer dispute, and NVD remains awaiting analysis. npm still
has no release after 4.0.7, and Vite 8/Vitest 5 still depend on the affected line.

The installed dev-only graph is `vite@7.3.7 -> picomatch@4.0.7`,
`vite -> fdir@6.5.0 -> picomatch`, `vite -> tinyglobby@0.2.17 -> picomatch`, and
`vitest@4.1.12 -> picomatch`. Production-only audit is clean, the production bundle
contains no picomatch, application source never imports it, repository globs use
ordinary patterns, and no HTTP/account/game/analysis input reaches glob compilation.

The repository owner approved a temporary acceptance for exactly this advisory on
2026-11-17 in PR #94. PR #90 reuses that decision because its lockfile resolves the
same affected package set and exact versions. The acceptance is recorded in
`frontend/security/dependency-audit-exceptions.json`, expires 2026-12-17, and is
enforced fail-closed by `npm run audit:ci`. The complete exposure analysis and
comparison are recorded in the
[tournament validation record](player-tournament-refinement.md#dependency-audit-acceptance).
The advisory remains unresolved; remove the exception when a patched release ships.
