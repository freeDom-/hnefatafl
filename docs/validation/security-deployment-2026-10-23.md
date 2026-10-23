# Secure accounts and deployment implementation — 2026-10-23

[Validation index](../VALIDATION.md) · [Security architecture](../SECURITY.md) · [Operator guide](../DEPLOYMENT.md)

This records the implementation checks performed before preparing v0.22.0. Historical
release notes retain their original evidence. No target Proxmox installation was
performed. Publication and version metadata are covered in the
[v0.22.0 release notes](../releases/v0.22.0.md).

## 1. Completed features

Persistent accounts, mandatory login, USER/ADMIN permissions, private account-owned
history/statistics, administrator-only training/tournaments/model management,
local initial-administrator and recovery commands, an unavailable-registration
placeholder, production configuration, a hardened systemd service, native Debian
and Proxmox installers, stable-release update/backup/recovery tools and regression
tests are integrated. Ordinary inference/gameplay remains available to users.

## 2. Authentication and RBAC architecture

Accounts use random immutable IDs, unique normalized usernames/emails and salted
scrypt password hashes (N=32768, r=8, p=3). Initial username defaults to Odin; no
password/email defaults exist. Interactive passwords require confirmation and
masked terminal input; the CLI refuses an unmasked fallback.

Random opaque sessions are validated through SQLite using FastAPI/Starlette
request/cookie primitives. Only session/CSRF token hashes are stored. Production
cookies are Secure, HttpOnly, host-only and SameSite=Lax. Sessions have fixed
expiration, logout invalidation, same-origin/CSRF protection, and shared database
login throttling. Every request reloads account status and permissions. Local
password/role/status updates revoke browser sessions. Disabled and unknown-role
accounts cannot authenticate. Independent app instances share session authority.

The ASGI boundary checks exact method/path policies before body parsing or job
execution. SQLite queries enforce game/player ownership. Backend policy remains
authoritative when clients forge IDs, roles, cookies, headers or query parameters.
Frontend account/permission changes discard protected component state and private
setup caches. Production still uses one API process for existing catalog/job
coordination; training subprocess count is independent of API worker count.

## 3. Complete API coverage and exceptions

All **57 registered API method/path combinations** have explicit grants in
[the inventory](../API_PERMISSIONS.json). The route-derived tests fail for missing
policies, and unknown routes remain denied. Run `scripts/audit_api.py` to check or
review/regenerate the inventory with `--write`.

Public API exceptions are POST login, GET minimal readiness and GET the registration
unavailable message. The login shell and compiled assets are public. API schema
and interactive documentation are disabled. No public account-creation API exists.

All training/tournament routes, including reads, cancellation, deletion and worker
enrollment, require administrator permissions. Model inference catalogs expose
only safe gameplay fields to USER; management/transfers require ADMIN. Users may
modify/delete their own explicitly supported games/local profiles. Administrators
have explicit global-data access, including legacy records.

Six worker routes use scoped machine credentials and existing job leases, with
an active enrolling administrator required. Machine tokens cannot authenticate
browser/gameplay/management routes. Legacy workers need re-enrollment. No current
WebSocket/SSE handlers exist; WebSocket handshakes fail closed.

## 4. Database migration and preservation

Schema **7 → 8** is additive and transactional. It adds accounts, sessions,
login-attempt records, bootstrap metadata, nullable game/player ownership indexes,
and a reserved future account-participation relation. Existing game JSON, IDs,
players/tombstones, runes, statistics sources, jobs, tournaments, models and
checkpoints are retained. Historical records keep NULL ownership and are visible
to administrators; they are never assigned to Odin or given invented passwords.

Training outputs retain the existing persistent jobs layout. New releases replace
code/venvs/frontend builds, while `/var/lib/hnefatafl` and `/etc/hnefatafl` remain
persistent. Backups use SQLite's backup API with explicitly closed connections and
a standalone database file; transient WAL/SHM files are excluded from its manifest.

## 5. Files created or modified

The following is the source-file change inventory for the implementation phase,
before the release notes and version metadata were prepared:

- `.env.example`
- `ARCHITECTURE.md`
- `PROGRESS.md`
- `README.md`
- `deployment/hnefatafl.service`
- `deployment/nginx.conf.example`
- `docs/API_PERMISSIONS.json`
- `docs/DEPLOYMENT.md`
- `docs/DEVELOPMENT.md`
- `docs/README.md`
- `docs/RELEASING.md`
- `docs/REMOTE_TRAINING.md`
- `docs/SECURITY.md`
- `docs/VALIDATION.md`
- `docs/validation/security-deployment-2026-10-23.md`
- `frontend/playwright.config.ts`
- `frontend/src/App.tsx`
- `frontend/src/Archive.test.tsx`
- `frontend/src/Archive.tsx`
- `frontend/src/Auth.test.tsx`
- `frontend/src/Auth.tsx`
- `frontend/src/Training.tsx`
- `frontend/src/api.ts`
- `frontend/src/main.tsx`
- `frontend/src/style.css`
- `frontend/src/test-setup.ts`
- `frontend/tests/analysis-localization.spec.ts`
- `frontend/tests/appearance.spec.ts`
- `frontend/tests/auth-fixture.ts`
- `frontend/tests/authentication.spec.ts`
- `frontend/tests/game-setup.spec.ts`
- `frontend/tests/game.spec.ts`
- `frontend/tests/job-transfer.spec.ts`
- `frontend/tests/newfeatures.spec.ts`
- `frontend/tests/record-management.spec.ts`
- `frontend/tests/records-tables.spec.ts`
- `frontend/tests/remote-training.spec.ts`
- `frontend/tests/shield-maiden.spec.ts`
- `frontend/tests/start_backend.py`
- `frontend/tests/tournaments.spec.ts`
- `frontend/tests/training-dashboard.spec.ts`
- `frontend/vite.config.ts`
- `hnefatafl/accounts.py`
- `hnefatafl/auth.py`
- `hnefatafl/deployment.py`
- `hnefatafl/remote_training.py`
- `hnefatafl/security_context.py`
- `hnefatafl/server.py`
- `hnefatafl/storage.py`
- `scripts/audit_api.py`
- `scripts/linux/backup.sh`
- `scripts/linux/install.sh`
- `scripts/linux/update.sh`
- `scripts/package_release.py`
- `scripts/proxmox/ct/hnefatafl.sh`
- `scripts/proxmox/install/hnefatafl-install.sh`
- `tests/auth_support.py`
- `tests/test_analysis_api.py`
- `tests/test_api.py`
- `tests/test_background_analysis.py`
- `tests/test_catalog_management.py`
- `tests/test_deployment.py`
- `tests/test_game_management.py`
- `tests/test_identities.py`
- `tests/test_job_archives.py`
- `tests/test_neural_wrappers_api.py`
- `tests/test_remote_api.py`
- `tests/test_remote_integration.py`
- `tests/test_round_robin.py`
- `tests/test_security.py`
- `tests/test_shield_maiden_api.py`
- `tests/test_tournaments.py`
- `tests/test_training_execution_api.py`
- `tests/test_training_resume_api.py`

## 6. Local testing and startup

From the checkout:

```sh
make install
make check
.venv/bin/python scripts/audit_api.py
shellcheck scripts/linux/*.sh scripts/proxmox/*/*.sh
```

For an isolated interactive session:

```sh
export HNEFATAFL_DATA_DIR="$(mktemp -d)"
.venv/bin/python -m hnefatafl.accounts init-admin
make build
bash scripts/start.sh
```

The CLI requests username (Odin by default), email, password and confirmation.
Create ordinary accounts locally with `python -m hnefatafl.accounts create-user`
using the same data directory and appropriate OS privileges. Password recovery
uses `reset-password --username Odin`. Browser regression tests bootstrap ordinary
temporary accounts and authenticate through the real API:

```sh
cd frontend
npx playwright install chromium
npm run test:e2e
```

## 7. Proxmox and Linux installation

On the Proxmox host, run from a reviewed authenticated checkout:

```sh
bash scripts/proxmox/ct/hnefatafl.sh
```

The default installation creates an unprivileged Debian 13 amd64 LXC with 4 CPUs,
4096 MiB RAM, 20 GiB disk, DHCP/vmbr0, hostname hnefatafl and autostart. Advanced
setup permits ID, CPU/RAM/disk, storage, bridge and static IPv4/gateway choices.
Repository/token, HTTPS origin, trusted proxies, port and administrator credentials
are entered inside the container console. No credential-bearing host handoff file
is created. CPU training requires no nesting, privileged LXC or GPU passthrough.

For dedicated Debian 13 amd64/arm64 Linux:

```sh
bash scripts/linux/install.sh
# Optional selected stable release:
# bash scripts/linux/install.sh --version vX.Y.Z
```

Production requires a matching published stable asset, HTTPS origin and explicit
trusted proxies. Existing HTTPS termination, DNS and firewall remain operator
managed. The installed service uses the dedicated non-root hnefatafl account,
protected configuration, journald and cgroup resource limits.

## 8. Updates, backups and rollback

From any directory, as root:

```sh
hnefatafl-update --check
hnefatafl-update --list
hnefatafl-update
hnefatafl-update --version vX.Y.Z
hnefatafl-backup
hnefatafl-update --rollback
hnefatafl-update --recover
systemctl status hnefatafl
journalctl -u hnefatafl -f
```

The updater selects published stable vX.Y.Z metadata, verifies GitHub asset SHA-256,
bounds extraction, builds separately with lockfiles, validates configuration and
runs security checks before interrupting service. An exclusive operation lock and
HTTP mutation gate prevent concurrent activation and training-start races. Active
or queued training/tournaments must finish or be cancelled first.

Verified full data/config snapshots precede migrations. Atomic symlink activation
is followed by health, authenticated account/catalog and anonymous-denial probes.
Failures recover the matching prior code/data. Rollback preserves current account
credentials/status/roles when schemas match, invalidates sessions, revokes restored
worker credentials, and retains newer application data in a sibling quarantine
rather than deleting it. Incompatible account schemas require manual recovery.

Before installation, publish a compatible stable asset from committed, versioned
source following [the release procedure](../RELEASING.md#production-updater-assets).
Old previews without the required asset/security implementation are refused.

## 9. Executed validation

- Full `make check`: **650 backend tests passed, one CUDA-only skip**, **167 frontend
  tests passed**, documentation links, Ruff/Prettier, mypy/TypeScript and production
  Vite build passed.
- After the final SQLite backup/connection correction: **25 focused CLI, deployment,
  recovery and real remote-worker tests passed**. These include garbage-collection
  stability of standalone snapshots and the real HTTP authenticated readiness probe.
- The broader focused security/deployment/live-worker run passed **42 tests**.
- Browser coverage: **52 distinct desktop/mobile scenarios passed across the full
  run and focused reruns; four intentional duplicate mobile cases were skipped**.
  The initial full run had 45 passes/seven failures. Frozen reruns passed six cases;
  the final mobile analysis case passed after giving its real unfinished-search
  assertion an explicit 15-second budget for touch scrolling. Assertions remain.
  Other fixes covered CSRF on the legacy deletion path and dismissing the normal
  initial-game modal before logout. Runtime edits during the initial Vite run also
  interrupted two tournament cases; their frozen reruns passed.
- ShellCheck **0.11.0**, Bash syntax checks, generated API inventory and `git diff
  --check` passed. Mobile login was visually inspected at 390×844 with no horizontal
  overflow.

The environment used Python 3.12.14, Node 24.19.0, CPU PyTorch 2.14.1 and Chromium.
Functional training/checkpoint/replay validation does not establish model strength.
A Starlette test-client deprecation warning and the existing large Vite chunk warning
remain non-blocking.

## 10. Remaining risks and unverified operations

Live Proxmox creation, Debian 13 package provisioning, target systemd/reboot,
external HTTPS proxy/client-IP behavior, private GitHub credentials and a published
production release download require target-host validation. Linux arm64 and GPU
execution were not exercised. No target-host deployment was performed.

Follow the operator guide's manual acceptance checklist before public exposure.
Use off-host encrypted backups and test restoration on disposable data. Rollback
quarantines post-snapshot application changes for manual reconciliation; it does
not automatically merge them. Single-process catalog/training ownership remains
an architectural limit. Per-user inference quotas, MFA, recovery email, public
registration and online matchmaking are outside this milestone. These controls
are consistently tested behaviors, not a claim of immunity to attacks.
