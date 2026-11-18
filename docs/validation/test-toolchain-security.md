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

`.github/workflows/ci.yml` now runs `npm audit --audit-level=moderate` immediately
after the `browser` job's `npm ci`; moderate or higher findings fail that job. No
`--force`, audit suppression, blanket ignore list or relaxed check is used, and the
committed lockfile reports zero findings.

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

## picomatch ReDoS advisory (GHSA-fc5q-39xc-vjpf), 2026-11-17

This section supersedes the earlier zero-finding audit status above. A fresh
`npm audit --audit-level=moderate` exits 1 with five affected development packages,
all from one advisory: picomatch through 4.0.7 is vulnerable to a ReDoS through
single-branch repeated extglob patterns (CVE-2026-112366, high severity).

### Installed graph

`npm ls picomatch --all` on the committed lockfile:

| Dependency path | Role |
| --- | --- |
| `vite@7.3.7 -> picomatch@4.0.7` | build tool |
| `vite@7.3.7 -> fdir@6.5.0 -> picomatch@4.0.7` | build file discovery |
| `vite@7.3.7 -> tinyglobby@0.2.17 -> picomatch@4.0.7` | build globbing |
| `vitest@4.1.12 -> picomatch@4.0.7` | test runner |

The audit counts five affected packages (picomatch, fdir, tinyglobby, vite,
vitest) rather than five unique advisories. `npm audit --package-lock-only
--omit=dev` reports **0 vulnerabilities**: production dependencies are only React,
React DOM and React Markdown, and the production `dist/` bundle contains no
picomatch code.

### Conflicting database status

The sources disagree, so the current status is **disputed but not withdrawn**:

| Source | Current status |
| --- | --- |
| GitHub Advisory GHSA-fc5q-39xc-vjpf | **Reviewed, high severity, not withdrawn**, no first patched version. Last reviewed 2026-11-17 after the Snyk revocation. |
| NVD CVE-2026-112366 | **Awaiting Analysis**; the published CVSS vectors are secondary (VulnCheck) at 7.5/8.7, CISA SSVC says exploitation none, and NVD has not added a disputed tag. |
| OSV.dev | Mirrors the GitHub advisory (affected `<= 4.0.7`, no fixed event). |
| Snyk SNYK-JS-PICOMATCH-21248802 | **Revoked**: “doesn’t affect any version”, amendment “This was deemed not a vulnerability”, and it notes the CVE was disputed by the maintainer. |
| npm registry | Latest release is still **4.0.7** (2026-08-24); no patched release exists. |

Snyk revoked its entry after the maintainer disputed the report. GitHub re-reviewed
the same report on 2026-11-17 and kept it, and NVD has not completed its analysis.
A revoked Snyk entry therefore does not invalidate the active GitHub advisory, and
the advisory has not been formally disputed on GitHub. `npm audit` consumes the
GitHub Advisory Database, so the repository's gate follows the stricter, still-active
assessment until GitHub withdraws the advisory or a patched release appears. This is
not evidence of a false positive; it is unresolved disagreement between databases.

### Exposure verification

- The lockfile marks `picomatch` as `"dev": true`; `npm ls picomatch --omit=dev`
  is empty, production dependencies are only React, React DOM and React Markdown,
  and the built `dist/` contains no picomatch code. The advisory has no
  `vulnerable_functions`, so there is no exported function list that would narrow
  the finding further.
- Vite's `tinyglobby`/`fdir` and Vitest import picomatch and compile globs during
  builds and tests, so the library is exercised by trusted tooling. Repository
  configuration uses ordinary patterns such as `src/**/*.test.{ts,tsx}` and no
  config or script contains the vulnerable leading-bare-quantifier shape
  (`+(*a)`, `*(*a)`, `?(*a)`).
- The application source never imports picomatch, and no HTTP, account, game or
  analysis input is passed to glob compilation. An attacker cannot supply the glob
  patterns the advisory requires.
- Vite compiles `import.meta.glob` patterns from application source in the
  development/build tool; this repository does not use `import.meta.glob`
  anywhere. Vite's `server.fs.deny` pattern list is static configuration and uses
  no repeated single-branch extglob. Plugin include/exclude patterns come from
  repository configuration, and package-export patterns come from locked,
  trusted dependencies.
- Glob *subjects* (module IDs, request paths and file names) are matched against a
  compiled pattern; they are never compiled as patterns. A crafted file name or
  URL therefore cannot trigger the vulnerable parser path.
- Production serves the static `frontend/dist` bundle through FastAPI; Vite and
  Vitest are never started in the deployed service. Deployed runtime exposure is
  absent.
- Practical exposure is therefore a development/CI tooling denial of service only
  if a trusted developer, CI input or compromised dependency supplies a malicious
  pattern. That is the scope of the approved acceptance below, not a remote or
  product-runtime vulnerability.

### Upstream patch status

- [PR #238](https://github.com/micromatch/picomatch/pull/238) (literalize unsafe
  leading extglobs) has been open since 2026-10-22 with one commit and passing
  author-run tests, and is not merged. Its author documents a remaining nested-group
  limitation. [PR #246](https://github.com/micromatch/picomatch/pull/246) is also
  open. Maintainers have not merged or released either.
- Vite 8 and Vitest 5 still declare `picomatch: ^4.0.7`, so a major upgrade would
  not resolve this. npm's suggested `audit fix --force` would downgrade to Vitest
  3.1.4 and Vite 6.2.7, a breaking change that still resolves vulnerable picomatch.
- The only official guidance is Snyk's: do not use the risky pattern and do not let
  untrusted users define regular expressions. Both conditions already hold here.

### Approved risk acceptance (active until 2026-12-17)

No supported technical fix exists, and no fork, override, unmerged patch, downgrade
or blanket audit disable is used. On 2026-11-18 the repository owner approved a
narrowly scoped, time-limited risk acceptance for exactly GHSA-fc5q-39xc-vjpf in
the development-only Picomatch path.

`frontend/security/dependency-audit-exceptions.json` (schema 3) records the exact
advisory/package, CVE, severity, development scope, approver handle, approval date,
review reference, durable approval reference, rationale, expiry, affected package
set, every reviewed package version and the reviewed advisory range. The script rejects unknown fields, invalid
GitHub handles, invalid or future approval dates, missing/malformed or non-repository approval
references, invalid expiry dates, windows longer than 90 days, severities or ranges that changed, package-version changes,
affected-set changes, runtime reach, stale exceptions and expired exceptions. This
prevents silent broadening or extension: any such change is a visible file diff and
fails until re-reviewed.

`frontend/scripts/auditDependencies.mjs` (`npm run audit:ci`) runs the full lockfile
audit and a production-only audit. It is fail-closed:

- Every reported vulnerable package must be attributed to an accepted advisory.
  Advisory identities use GHSA when present; otherwise the npm source or URL is
  retained. A non-GitHub advisory URL is never discarded and therefore fails as
  unaccepted.
- String-only `via` entries must reference reported packages inside the accepted
  component. Dangling references or extra vulnerable packages fail.
- Malformed vulnerability/advisory entries, missing titles, ranges or severities,
  error responses, missing JSON, unexpected exit statuses, unsupported audit report
  versions, missing vulnerability/metadata maps and metadata/severity count
  mismatches fail.
- The production-only audit must be clean, and every affected package must be
  `dev: true` in the lockfile with the reviewed resolved version.

The dedicated `security` CI job runs that script and prints the actual advisory
id, affected packages and versions, approver, review reference, approval reference,
rationale and expiry. It passes only through the recorded acceptance, not because
the finding was disproved. The advisory remains unresolved. Fourteen Node tests
cover the positive case and every fail-closed and approval-reference case.

**Approval evidence:** the durable owner decision is the approval comment at
[PR #94, comment 6974974794](https://github.com/freeDom-/hnefatafl/pull/94#issuecomment-6974974794),
recorded 2026-11-18 by `freeDom-`. It identifies the advisory, the development-only
dependency paths, the residual development/CI denial-of-service risk, the absence of
a supported upstream patch and the 2026-12-17 expiry, and states the removal and
no-extension conditions. The JSON records that decision; it is not the evidence
itself. The schema requires an https `approvalReference` hosted in this repository,
so a missing, malformed or external reference fails. Approval applies only to the
exact advisory, versions, range and package set recorded; any change requires a new
owner decision.

### Required main-branch checks (owner action, not applied)

The active **Main branch rules** ruleset (id `25820905`, targeting the default
branch) currently has `deletion`, `non_fast_forward` and `pull_request` rules but
**no required status checks** and **zero required approving reviews**. The four CI
jobs being green is therefore voluntary: GitHub does not block a merge when a check
is red.

Recommended owner settings:

- Add a `required_status_checks` rule with the exact GitHub Actions contexts
  `security`, `python`, `node22` and `browser`.
- Enable `strict_required_status_checks_policy` (require the branch to be up to
  date) if the owner wants merge-order guarantees.
- Set `required_approving_review_count` to at least 1 as a separate owner decision.
- Keep the existing deletion, non-fast-forward and pull-request rules.

Repository ruleset shape (conceptual; apply in Settings → Rules → Main branch
rules, not by editing this repository):

```json
"required_status_checks": {
  "strict_required_status_checks_policy": true,
  "required_status_checks": [
    { "context": "security" },
    { "context": "python" },
    { "context": "node22" },
    { "context": "browser" }
  ]
}
```

Exact owner steps:

1. Repository **Settings → Rules → Rulesets → Main branch rules → Edit**.
2. Enable **Require status checks to pass**, then add `security`, `python`,
   `node22` and `browser` from the GitHub Actions source.
3. Optionally enable **Require branches to be up to date before merging**.
4. Optionally set **Required approvals** to 1 under the pull-request rule.
5. Save and confirm the ruleset still targets the default branch.

The API equivalent is a `PUT /repos/freeDom-/hnefatafl/rulesets/25820905` that
preserves the existing rules and adds the `required_status_checks` rule above;
only an owner token should perform it.

Verification after the owner applies it:

- `gh pr checks 94` lists the four contexts with the GitHub Actions source.
- A throwaway PR with one required job forced to fail cannot merge.
- Pending checks block merging until they finish.
- A complete green run plus any required approval permits merging.
- Renaming a workflow job requires updating the ruleset context at the same time;
  the current job names are stable literals in `.github/workflows/ci.yml`.

This document records the recommendation only; repository protection settings were
not modified. Until the owner applies it, a green matrix is a voluntary observation,
not a GitHub-enforced merge gate.

### Follow-up

When upstream publishes a patched picomatch release, refresh the lockfile
(`npm --prefix frontend update picomatch`), remove the exception entry in the same
change, rerun the unit/browser suites and re-audit; the script then passes without
an acceptance. Treat a GitHub withdrawal or a formal dispute as a separate outcome:
re-check the advisory before removing the exception, because a Snyk revocation alone
does not clear it. Do not extend the expiry silently; re-approve and record a new
date. Repeat the audit at release preparation because advisories are time-sensitive.
