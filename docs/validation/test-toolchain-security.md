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
suggested latest-5.x force upgrade, and supports the repository's Node 22+/Vite 7
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

## Browser resize correction

The first full CI run passed the Python job but exposed one browser timing failure
in Play mode discovery (354 passed, 19 skipped, one failed). The test now waits for
the existing geometry assertions after a viewport resize instead of reading the
transitional layout immediately. The expected card count, stacked layout and
maximum document width are unchanged. This is the same correction validated in
the Portrait Hall full CI run. The updated PR CI result is recorded on the PR.
