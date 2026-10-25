"""Stable GitHub release deployment. Root-only CLI; all builds run unprivileged.

The deployment journal, full verified snapshots and retained release directories
are recovery inputs. Never roll old code forward against a newer database.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from contextlib import closing, contextmanager
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def version_key(tag: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(tag)
    if match is None:
        raise ValueError("Use a stable version identifier such as 1.2.3")
    return tuple(int(part) for part in match.groups())


def repository(value: str) -> str:
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/").removesuffix(".git").rstrip("/")
    if not REPOSITORY.fullmatch(value) or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Repository must be owner/name or an HTTPS github.com repository URL")
    return value


def select_release(releases: list[dict], current: str | None, requested: str | None = None):
    if requested is not None:
        version_key(requested)
    if not releases:
        raise ValueError("No published stable GitHub release is available")
    selected = (
        next((item for item in releases if item["tag_name"] == requested), None)
        if requested
        else releases[0]
    )
    if selected is None:
        raise ValueError("Requested version is not a published stable release")
    return selected, current is None or version_key(selected["tag_name"]) > version_key(current)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in DOWNLOAD_HOSTS
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Refusing untrusted release redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and parsed.hostname != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, repo: str, token: str = ""):
        if not isinstance(token, str) or len(token) > 4096 or re.search(r"[^\x21-\x7e]", token):
            raise ValueError("Invalid GitHub credential format")
        self.repo, self.token = repository(repo), token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def fetch(self, url: str, *, binary=False, limit=16 * 1024 * 1024) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Release requests must use api.github.com HTTPS")
        headers = {
            "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hnefatafl-release-updater",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with self.opener.open(
                urllib.request.Request(url, headers=headers), timeout=120
            ) as response:
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ValueError("GitHub response exceeds the configured size limit")
                return content
        except urllib.error.HTTPError as error:
            # Never print redirect URLs, headers, tokens, or private response bodies.
            detail = {
                401: "Invalid GitHub credential",
                403: "GitHub access denied or rate limit reached",
                404: "Repository/release unavailable; check read-only Contents permission",
                429: "GitHub rate limit reached",
            }.get(error.code, "GitHub request failed")
            raise ValueError(f"{detail} (HTTP {error.code})") from None
        except urllib.error.URLError:
            raise ValueError("Cannot connect to GitHub; check HTTPS connectivity") from None

    def releases(self) -> list[dict]:
        releases: list[dict] = []
        for page in range(1, 101):
            items = json.loads(
                self.fetch(
                    f"https://api.github.com/repos/{self.repo}/releases?per_page=100&page={page}"
                )
            )
            if not isinstance(items, list):
                raise ValueError("Invalid GitHub release response")
            releases.extend(
                item
                for item in items
                if not item.get("draft")
                and not item.get("prerelease")
                and item.get("published_at")
                and VERSION.fullmatch(item.get("tag_name", ""))
            )
            if len(items) < 100:
                break
        else:
            raise ValueError("Too many release pages; refusing incomplete version selection")
        return sorted(releases, key=lambda item: version_key(item["tag_name"]), reverse=True)

    def download(self, release: dict, destination: Path) -> str:
        tag = release["tag_name"]
        name = f"hnefatafl-{tag}.tar.gz"
        asset = next(
            (
                item
                for item in release.get("assets", [])
                if item["name"] == name and item.get("state") == "uploaded"
            ),
            None,
        )
        if not asset:
            raise ValueError(f"Stable release must publish {name}; see docs/RELEASING.md")
        expected = asset.get("digest", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
            raise ValueError(
                "Release asset lacks GitHub's SHA-256 digest; refusing unverified archive"
            )
        content = self.fetch(
            f"https://api.github.com/repos/{self.repo}/releases/assets/{int(asset['id'])}",
            binary=True,
            limit=1024**3,
        )
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if not secrets.compare_digest(expected, actual):
            raise ValueError("Release archive checksum mismatch")
        destination.write_bytes(content)
        return actual


def extract(archive: Path, destination: Path):
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 50000 or sum(m.size for m in members) > 4 * 1024**3:
            raise ValueError("Release archive is too large")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe archive path or non-regular member")
            if member.mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError("Privileged archive mode")
        bundle.extractall(destination, members=members, filter="data")


def atomic_json(path: Path, value):
    temporary = path.with_suffix(".new")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def active_jobs(data: Path) -> bool:
    database = data / "hnefatafl.sqlite3"
    if not database.exists():
        return False
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as c:
        for table in ("jobs", "tournaments"):
            if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                for row in c.execute(f"SELECT data FROM {table}"):
                    if json.loads(row[0]).get("status") in {
                        "running",
                        "cancelling",
                        "queued",
                        "starting",
                    }:
                        return True
    return False


class Deployment:
    def __init__(
        self,
        config: dict,
        *,
        base=Path("/opt/hnefatafl"),
        data=Path("/var/lib/hnefatafl"),
        etc=Path("/etc/hnefatafl"),
        unit_file=Path("/etc/systemd/system/hnefatafl.service"),
    ):
        self.config, self.base, self.data, self.etc = config, base, data, etc
        self.unit_file = unit_file
        self.releases = base / "releases"
        self.current = base / "current"
        self.journal = etc / "transaction.json"
        self.state = etc / "installed.json"
        self.env = {
            "PATH": str(base / "tools/node/bin")
            + ":"
            + str(base / "tools/bin")
            + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(data),
            "LANG": "C.UTF-8",
            "UV_CACHE_DIR": str(data / "cache/uv"),
            "npm_config_cache": str(data / "cache/npm"),
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
        ):
            if name in os.environ:
                self.env[name] = os.environ[name]

    def run(self, args, *, cwd=None, user=False, extra_env=None):
        command = [str(value) for value in args]
        if user:
            command = ["runuser", "-u", "hnefatafl", "--", *command]
        subprocess.run(command, cwd=cwd, env={**self.env, **(extra_env or {})}, check=True)

    def service(self, action):
        self.run(["systemctl", action, "hnefatafl.service"])

    def record(self, value):
        atomic_json(self.journal, value)

    @contextmanager
    def write_gate(self):
        # Drain admitted HTTP mutations before checking jobs/stopping services.
        # New mutations fail with 503 until activation/recovery finishes. GET
        # readiness probes remain available and cannot launch training.
        gate = self.data / ".deployment-gate"
        with gate.open("a+") as handle:
            gate.chmod(0o644)
            deadline = time.monotonic() + 60
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "HTTP mutations are still running. Retry after they finish."
                        ) from None
                    time.sleep(0.25)
            yield

    def snapshot(self) -> Path:
        backup = (
            self.data / "backups" / (time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4))
        )
        backup.mkdir(parents=True, mode=0o700)

        def ignore(directory, names):
            return (
                {
                    "backups",
                    "cache",
                    "hnefatafl.sqlite3",
                    "hnefatafl.sqlite3-wal",
                    "hnefatafl.sqlite3-shm",
                }
                & set(names)
                if Path(directory) == self.data
                else set()
            )

        shutil.copytree(self.data, backup / "data", ignore=ignore, symlinks=True)
        shutil.copytree(self.etc, backup / "config", symlinks=True)
        database = self.data / "hnefatafl.sqlite3"
        if database.exists():
            target = backup / "data/hnefatafl.sqlite3"
            for suffix in ("-wal", "-shm"):
                target.with_name(target.name + suffix).unlink(missing_ok=True)
            with (
                closing(sqlite3.connect(database)) as source,
                closing(sqlite3.connect(target)) as copy,
            ):
                source.backup(copy)
                copy.commit()
                # Backups are standalone files; no live WAL/SHM handles may enter
                # the manifest and disappear later during connection GC.
                copy.execute("PRAGMA journal_mode=DELETE")
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup SQLite integrity check failed")
        manifest = {}
        for file in backup.rglob("*"):
            if file.is_symlink():
                raise ValueError("Backup encountered a symlink; inspect storage before continuing")
            if file.is_file():
                manifest[str(file.relative_to(backup))] = checksum(file)
        atomic_json(backup / "manifest.json", manifest)
        self.verify_snapshot(backup)
        return backup

    def verify_snapshot(self, backup):
        manifest = json.loads((backup / "manifest.json").read_text())
        for name, expected in manifest.items():
            file = backup / name
            if (
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or file.is_symlink()
                or not file.is_file()
                or checksum(file) != expected
            ):
                raise ValueError("Backup verification failed")

    def restore(self, backup: Path):
        self.verify_snapshot(backup)
        restored = self.data.with_name(self.data.name + ".restore-" + secrets.token_hex(4))
        shutil.copytree(backup / "data", restored)
        # Restoring game/training data must not resurrect logged-out sessions or
        # undo password, role, and status changes made since the snapshot.
        live_db, copy_db = self.data / "hnefatafl.sqlite3", restored / "hnefatafl.sqlite3"
        if live_db.exists() and copy_db.exists():
            with (
                closing(sqlite3.connect(live_db)) as live,
                closing(sqlite3.connect(copy_db)) as copy,
            ):
                for table in ("accounts", "auth_meta", "account_limits"):
                    live_columns = [row[1] for row in live.execute(f"PRAGMA table_info({table})")]
                    copy_columns = [row[1] for row in copy.execute(f"PRAGMA table_info({table})")]
                    if copy_columns and live_columns != copy_columns:
                        raise ValueError(
                            "Account schema changed; manual recovery is required before restoring security state"
                        )
                    if copy_columns:
                        rows = live.execute(f"SELECT * FROM {table}").fetchall()
                        copy.execute(f"DELETE FROM {table}")
                        copy.executemany(
                            f"INSERT INTO {table} VALUES({','.join('?' for _ in copy_columns)})",
                            rows,
                        )
                if copy.execute("SELECT 1 FROM sqlite_master WHERE name='sessions'").fetchone():
                    copy.execute("DELETE FROM sessions")
                # Never restore an old verification/reset capability or mail intent.
                for table in ("account_tokens", "account_mail"):
                    if copy.execute(
                        "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                    ).fetchone():
                        copy.execute(f"DELETE FROM {table}")
                if copy.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='training_workers'"
                ).fetchone():
                    for identifier, encoded in copy.execute(
                        "SELECT id,data FROM training_workers"
                    ).fetchall():
                        worker = json.loads(encoded)
                        worker["revoked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        copy.execute(
                            "UPDATE training_workers SET data=? WHERE id=?",
                            (json.dumps(worker), identifier),
                        )
                copy.commit()
        retained = self.data.with_name(self.data.name + ".retained-" + secrets.token_hex(4))
        gate = self.data / ".deployment-gate"
        if gate.exists():
            # Preserve the locked inode while replacing the surrounding data root.
            (restored / ".deployment-gate").unlink(missing_ok=True)
            gate.rename(restored / ".deployment-gate")
        self.data.rename(retained)
        restored.rename(self.data)
        if (retained / "backups").exists():
            (retained / "backups").rename(self.data / "backups")
        self.run(["chown", "-R", "hnefatafl:hnefatafl", self.data])
        if (self.data / "backups").exists():
            self.run(["chown", "-R", "root:root", self.data / "backups"])
            (self.data / "backups").chmod(0o700)
        print(f"Restored pre-deployment state. Newer data retained at {retained}.")

    def activate(self, release: Path):
        pending = self.base / "current.new"
        pending.unlink(missing_ok=True)
        pending.symlink_to(release)
        os.replace(pending, self.current)

    def validate_configuration(self, target: Path):
        values = {}
        for line in (self.etc / "application.env").read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"HNEFATAFL_[A-Z_]+", name):
                raise ValueError("Invalid application.env entry")
            values[name] = value
        if (
            values.get("HNEFATAFL_ENV") != "production"
            or values.get("HNEFATAFL_PUBLIC_URL") != self.config["public_url"]
            or values.get("HNEFATAFL_PORT") != str(self.config["port"])
        ):
            raise ValueError("Production application.env and deployment.json disagree")
        self.run(
            [
                target / ".venv/bin/python",
                "-c",
                "from hnefatafl.auth import SecuritySettings; SecuritySettings.environment()",
            ],
            cwd=target,
            user=True,
            extra_env=values,
        )

    def prepare(self, github: GitHub, release: dict) -> Path:
        self.releases.mkdir(parents=True, exist_ok=True)
        tag = release["tag_name"]
        version_key(tag)
        target = self.releases / (tag + "-" + secrets.token_hex(4))
        target.mkdir(mode=0o755)
        with tempfile.TemporaryDirectory(dir=self.base, prefix="download-") as temporary:
            archive = Path(temporary) / "release.tar.gz"
            sha256 = github.download(release, archive)
            extract(archive, target)
        for required in (
            "pyproject.toml",
            "uv.lock",
            "frontend/package-lock.json",
            "hnefatafl/auth.py",
            "tests/test_security.py",
            "deployment/hnefatafl.service",
        ):
            if not (target / required).is_file():
                raise ValueError(f"Unsupported release: missing {required}")
        metadata = tomllib.loads((target / "pyproject.toml").read_text())
        if metadata["project"]["version"] != tag:
            raise ValueError("Release tag and package version disagree")
        self.run(["chown", "-R", "hnefatafl:hnefatafl", target])
        self.run(
            [
                self.base / "tools/bin/uv",
                "sync",
                "--frozen",
                "--extra",
                "train",
                "--extra",
                "dev",
                "--python",
                "/usr/bin/python3",
            ],
            cwd=target,
            user=True,
        )
        self.run(["npm", "--prefix", "frontend", "ci"], cwd=target, user=True)
        self.run(["npm", "--prefix", "frontend", "run", "build"], cwd=target, user=True)
        self.run(
            [target / ".venv/bin/python", "-m", "pytest", "tests/test_security.py", "-q"],
            cwd=target,
            user=True,
        )
        atomic_json(target / ".release.json", {"version": tag, "sha256": sha256})
        self.run(["chown", "-R", "root:root", target])
        self.run(["chmod", "-R", "a+rX,a-w", target])
        return target

    def probe(self):
        url = f"http://127.0.0.1:{int(self.config['port'])}"
        host = urllib.parse.urlparse(self.config["public_url"]).netloc
        last_error = None
        for _ in range(30):
            try:
                request = urllib.request.Request(url + "/api/health", headers={"Host": host})
                with urllib.request.urlopen(request, timeout=3) as response:
                    if json.load(response) != {"status": "ok"}:
                        raise ValueError("Unexpected health response")
                last_error = None
                break
            except (OSError, ValueError) as error:
                last_error = error
                time.sleep(1)
        if last_error:
            raise ValueError("Service readiness failed; inspect journalctl -u hnefatafl")
        with closing(sqlite3.connect(self.data / "hnefatafl.sqlite3")) as c:
            row = c.execute(
                "SELECT id FROM accounts WHERE active=1 AND role='ADMIN' LIMIT 1"
            ).fetchone()
            if not row:
                raise ValueError("No active administrator after deployment")
            token = secrets.token_urlsafe(32)
            hashed = hashlib.sha256(token.encode()).hexdigest()
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (hashed, row[0], time.time() + 60, hashlib.sha256(b"local-probe").hexdigest()),
            )
            c.commit()
        try:
            for path in ("/api/auth/me", "/api/rulesets"):
                with urllib.request.urlopen(
                    urllib.request.Request(
                        url + path, headers={"Host": host, "Cookie": "hnefatafl_session=" + token}
                    ),
                    timeout=10,
                ) as response:
                    payload = json.load(response)
                    if path.endswith("/me") and "training" not in payload.get("account", {}).get(
                        "permissions", []
                    ):
                        raise ValueError("Authenticated permission probe failed")
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url + "/api/rulesets", headers={"Host": host}),
                    timeout=10,
                )
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise ValueError("Anonymous access probe failed") from None
            else:
                raise ValueError("Protected API accepted an anonymous request")
        finally:
            with closing(sqlite3.connect(self.data / "hnefatafl.sqlite3")) as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
                c.commit()

    def deploy(self, target: Path, *, initialize=False):
        with self.write_gate():
            return self._deploy_locked(target, initialize=initialize)

    def _deploy_locked(self, target: Path, *, initialize=False):
        if active_jobs(self.data):
            raise ValueError(
                "Active/queued training or tournament jobs exist. Cancel/finish jobs and pause remote workers first."
            )
        self.validate_configuration(target)
        old = self.current.resolve() if self.current.is_symlink() else None
        old_state = json.loads(self.state.read_text()) if self.state.exists() else None
        transaction = {
            "phase": "prepared",
            "previous": str(old) if old else None,
            "target": str(target),
            "backup": None,
        }
        self.record(transaction)
        if old or self.unit_file.exists():
            self.service("stop")
        try:
            if active_jobs(self.data):
                raise ValueError(
                    "New job activity detected during shutdown; update refused, retained state preserved"
                )
            transaction["backup"] = str(self.snapshot())
            transaction["phase"] = "migrating"
            self.record(transaction)
            self.run(
                [
                    target / ".venv/bin/python",
                    "-m",
                    "hnefatafl.accounts",
                    "migrate",
                    "--data-dir",
                    self.data,
                ],
                cwd=target,
                user=True,
            )
            if initialize:
                self.run(
                    [
                        target / ".venv/bin/python",
                        "-m",
                        "hnefatafl.accounts",
                        "init-admin",
                        "--data-dir",
                        self.data,
                    ],
                    cwd=target,
                    user=False,
                )
            self.activate(target)
            shutil.copyfile(
                target / "deployment/hnefatafl.service",
                self.unit_file,
            )
            self.run(["systemctl", "daemon-reload"])
            transaction["phase"] = "activated"
            self.record(transaction)
            self.service("start")
            self.probe()
            transaction["phase"] = "committed"
            atomic_json(
                self.state, {**json.loads((target / ".release.json").read_text()), **transaction}
            )
            self.record(transaction)
            print("Release ready: " + str(target))
        except BaseException:
            if old or self.unit_file.exists():
                self.service("stop")
            if transaction["backup"]:
                self.restore(Path(transaction["backup"]))
            if old:
                self.activate(old)
                shutil.copyfile(
                    old / "deployment/hnefatafl.service",
                    self.unit_file,
                )
                self.run(["systemctl", "daemon-reload"])
                self.service("start")
                self.probe()
            elif self.current.is_symlink():
                self.current.unlink()
            if old_state is not None:
                atomic_json(self.state, old_state)
            else:
                self.state.unlink(missing_ok=True)
            transaction["phase"] = "recovered"
            self.record(transaction)
            raise

    def recover(self, *, rollback=False):
        with self.write_gate():
            return self._recover_locked(rollback=rollback)

    def _recover_locked(self, *, rollback=False):
        source = self.state if rollback else self.journal
        transaction = json.loads(source.read_text())
        if not rollback and transaction.get("phase") in {"committed", "recovered"}:
            print("No interrupted deployment to recover.")
            return
        previous, backup = transaction.get("previous"), transaction.get("backup")
        if rollback and not previous:
            raise ValueError("No previous release to roll back to")
        if active_jobs(self.data):
            raise ValueError("Finish/cancel training and tournaments before recovery")
        self.service("stop")
        if backup:
            self.restore(Path(backup))
        if previous:
            self.activate(Path(previous))
            shutil.copyfile(
                Path(previous) / "deployment/hnefatafl.service",
                self.unit_file,
            )
            self.run(["systemctl", "daemon-reload"])
            self.service("start")
            self.probe()
        elif self.current.is_symlink():
            self.current.unlink()
        self.record({"phase": "recovered"})
        if previous:
            atomic_json(
                self.state,
                {
                    **json.loads((Path(previous) / ".release.json").read_text()),
                    "previous": None,
                    "backup": None,
                },
            )
        else:
            self.state.unlink(missing_ok=True)


def protected_json(path: Path):
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o077 or path.is_symlink():
        raise ValueError(f"Configuration must be a root-owned 0600 regular file: {path}")
    return json.loads(path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    for flag in ("check", "list", "rollback", "recover", "backup", "initialize"):
        options.add_argument("--" + flag, action="store_true")
    parser.add_argument("--version")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("Run this deployment command as root")
    os.umask(0o077)

    def interrupted(*_):
        raise InterruptedError("Deployment interrupted; inspect the transaction journal")

    signal.signal(signal.SIGTERM, interrupted)
    config_dir = Path("/etc/hnefatafl")
    try:
        with Path("/run/lock/hnefatafl-update.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another installation/update/backup is running") from None
            config = protected_json(config_dir / "deployment.json")
            deployment = Deployment(config)
            if args.rollback or args.recover:
                deployment.recover(rollback=args.rollback)
                return 0
            if deployment.journal.exists() and json.loads(deployment.journal.read_text()).get(
                "phase"
            ) not in {"committed", "recovered"}:
                raise ValueError(
                    "Interrupted transaction found. Run hnefatafl-update --recover before continuing"
                )
            if args.backup:
                with deployment.write_gate():
                    if active_jobs(deployment.data):
                        raise ValueError("Stop/cancel active jobs before backup")
                    deployment.service("stop")
                    try:
                        print("Verified backup: " + str(deployment.snapshot()))
                    finally:
                        deployment.service("start")
                return 0
            token_file = config_dir / "github.json"
            token = protected_json(token_file).get("token", "") if token_file.exists() else ""
            github = GitHub(config["repository"], token)
            releases = github.releases()
            if not releases:
                raise ValueError("No published stable GitHub release is available")
            if args.list:
                print("\n".join(item["tag_name"] for item in releases))
                return 0
            current = (
                json.loads(deployment.state.read_text())["version"]
                if deployment.state.exists()
                else None
            )
            selected, newer = select_release(releases, current, args.version)
            print(
                f"Installed: {current or 'none'}; selected stable release: {selected['tag_name']}"
            )
            if not newer:
                print("No newer stable release. Use --rollback for a recorded recovery.")
                return 0
            if args.check:
                return 0
            target = deployment.prepare(github, selected)
            deployment.deploy(target, initialize=args.initialize)
            return 0
    except (ValueError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        # No credentials or remote URLs appear in exception output.
        print(
            f"Deployment failed: {error if isinstance(error, ValueError) else type(error).__name__}. Inspect /etc/hnefatafl/transaction.json and journalctl -u hnefatafl.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
