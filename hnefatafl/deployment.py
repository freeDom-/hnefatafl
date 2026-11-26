"""Stable GitHub release deployment. Root-only CLI; all builds run unprivileged.

The deployment journal, full verified snapshots and retained release directories
are recovery inputs. Never roll old code forward against a newer database.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Stable identifiers are used verbatim as the release tag and package version. A
# release may omit the patch component (for example 0.42, meaning 0.42.0).
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
READINESS_ATTEMPTS = 30
READINESS_TIMEOUT_SECONDS = 3
READINESS_INTERVAL_SECONDS = 1
READINESS_BODY_LIMIT = 64 * 1024
MIB = 1024**2
DEFAULT_RETENTION = {"releases": 3, "backups": 3}
DEFAULT_STORAGE = {
    "headroom_mib": 256,
    "snapshot_margin_percent": 15,
    "cache_headroom_mib": 512,
    "minimum_free_inodes": 2048,
    "release_estimate_mib": 2048,
    "prune_build_dependencies": True,
}
DEFAULT_RECOVERY = {
    "readiness_attempts": READINESS_ATTEMPTS,
    "readiness_interval_seconds": READINESS_INTERVAL_SECONDS,
    "recovery_readiness_attempts": READINESS_ATTEMPTS,
}
# A newly created release or snapshot is published by an atomic rename. Recognised
# but incomplete artifacts younger than this may still be part of an interrupted
# operation and are not removed automatically.
INCOMPLETE_GRACE_SECONDS = 3600
RELEASE_DIRECTORY = re.compile(
    r"^(?P<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?)"
    r"(?:-(?P<identifier>[0-9a-f]{4,32}))?$"
)
BACKUP_DIRECTORY = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{4,32}$")
STAGING_DIRECTORY = re.compile(r"^\.staging-[a-z]+-[0-9a-f]{8,32}$")
DOWNLOAD_DIRECTORY = re.compile(r"^download-[A-Za-z0-9_]{8}$")
# 0.41.0 cannot start while the updater holds the exclusive deployment gate:
# its lifespan takes the shared tournament-reconciliation lock. A rollback to
# that immutable release would reproduce the original production failure.
INCOMPATIBLE_ROLLBACK_RELEASES = {
    "0.41.0": (
        "0.41.0 startup reconciliation takes the shared deployment gate while "
        "the updater holds it exclusively, so the service cannot become ready"
    )
}


class ReadinessError(ValueError):
    """Sanitized readiness failure suitable for operator-facing output."""


class DeploymentStepError(ValueError):
    """Sanitized deployment/recovery failure that identifies the failed step."""


class DeploymentError(ValueError):
    """Deliberate operator-facing deployment error with a safe message."""


class StorageError(DeploymentError):
    """Actionable, sanitized storage-preflight refusal."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        filesystem: str,
        available_bytes: int,
        required_bytes: int,
        available_inodes: int,
        required_inodes: int,
    ):
        super().__init__(message)
        self.phase = phase
        self.filesystem = filesystem
        self.available_bytes = available_bytes
        self.required_bytes = required_bytes
        self.available_inodes = available_inodes
        self.required_inodes = required_inodes


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError(f"{name} must be an integer between 1 and 100")
    return value


def _bounded_int(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _settings(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def normalized_config(config: dict) -> dict:
    """Return configuration with retention/storage/recovery defaults.

    Existing installations keep their root-owned deployment.json unchanged; the
    defaults are applied in memory so an upgrade needs no manual configuration.
    Unknown top-level keys are preserved for forward compatibility.
    """
    if not isinstance(config, dict):
        raise ValueError("deployment configuration must be an object")
    result = dict(config)
    retention = {**DEFAULT_RETENTION, **_settings(config.get("retention"), "retention")}
    retention["releases"] = _positive_int(retention.get("releases"), "retention.releases")
    retention["backups"] = _positive_int(retention.get("backups"), "retention.backups")
    result["retention"] = retention
    storage = {**DEFAULT_STORAGE, **_settings(config.get("storage"), "storage")}
    storage["headroom_mib"] = _bounded_int(
        storage.get("headroom_mib"), "storage.headroom_mib", 0, 1024 * 1024
    )
    storage["snapshot_margin_percent"] = _bounded_int(
        storage.get("snapshot_margin_percent"),
        "storage.snapshot_margin_percent",
        0,
        100,
    )
    storage["cache_headroom_mib"] = _bounded_int(
        storage.get("cache_headroom_mib"), "storage.cache_headroom_mib", 0, 1024 * 1024
    )
    storage["minimum_free_inodes"] = _bounded_int(
        storage.get("minimum_free_inodes"), "storage.minimum_free_inodes", 0, 100000000
    )
    storage["release_estimate_mib"] = _bounded_int(
        storage.get("release_estimate_mib"),
        "storage.release_estimate_mib",
        128,
        1024 * 1024,
    )
    if not isinstance(storage.get("prune_build_dependencies"), bool):
        raise ValueError("storage.prune_build_dependencies must be a boolean")
    result["storage"] = storage
    recovery = {**DEFAULT_RECOVERY, **_settings(config.get("recovery"), "recovery")}
    recovery["readiness_attempts"] = _bounded_int(
        recovery.get("readiness_attempts"), "recovery.readiness_attempts", 1, 600
    )
    recovery["recovery_readiness_attempts"] = _bounded_int(
        recovery.get("recovery_readiness_attempts"),
        "recovery.recovery_readiness_attempts",
        1,
        600,
    )
    interval = recovery.get("readiness_interval_seconds")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ValueError("recovery.readiness_interval_seconds must be a number")
    if not 0.05 <= float(interval) <= 60:
        raise ValueError("recovery.readiness_interval_seconds must be between 0.05 and 60")
    recovery["readiness_interval_seconds"] = float(interval)
    result["recovery"] = recovery
    return result


def format_bytes(value: int) -> str:
    value = max(0, int(value))
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GiB"
    return f"{value / MIB:.0f} MiB"


def describe_exception(error: BaseException) -> str:
    """Sanitized operator-facing failure detail.

    Deliberate deployment errors already have safe text. Filesystem errors expose
    only errno, strerror and the affected path, never arbitrary exception payloads
    that might contain credentials or database content.
    """
    if isinstance(error, (ReadinessError, DeploymentStepError, DeploymentError)):
        return str(error)
    if isinstance(error, shutil.Error):
        details = []
        for item in error.args[0] if error.args else []:
            if not isinstance(item, tuple) or len(item) < 3:
                continue
            source, destination, reason = item[0], item[1], item[2]
            detail = describe_exception(reason) if isinstance(reason, BaseException) else ""
            if not detail and isinstance(reason, str):
                cleaned = reason.replace("\n", " ")[:240]
                detail = cleaned or "filesystem error"
            details.append(f"{source} -> {destination}: {detail}")
        count = len(error.args[0]) if error.args and isinstance(error.args[0], list) else 0
        first = details[0] if details else "no per-file detail"
        return f"shutil.Error ({count} failed file operations; first: {first})"
    if isinstance(error, OSError):
        if error.errno is None:
            return type(error).__name__
        code = errno.errorcode.get(error.errno, str(error.errno))
        detail = os.strerror(error.errno) if error.errno else "filesystem error"
        location = error.filename or error.filename2 or ""
        suffix = f" at {location}" if location else ""
        return f"{type(error).__name__}: {code} ({detail}){suffix}"
    if isinstance(error, subprocess.CalledProcessError):
        command = ""
        if isinstance(error.cmd, (list, tuple)) and error.cmd:
            command = Path(str(error.cmd[0])).name
        elif isinstance(error.cmd, str):
            command = Path(error.cmd).name
        return f"subprocess failed (exit {error.returncode})" + (f": {command}" if command else "")
    return type(error).__name__


@dataclass(frozen=True)
class StorageRequirement:
    path: Path
    bytes: int
    label: str
    files: int = 0


@dataclass(frozen=True)
class FilesystemInfo:
    device: int
    anchor: Path
    available_bytes: int
    available_inodes: int


@dataclass
class CleanupEntry:
    category: str
    action: str
    path: Path
    reason: str
    bytes: int = 0
    files: int = 0
    version: str = ""


@dataclass
class CleanupReport:
    dry_run: bool
    retention: dict = field(default_factory=dict)
    entries: list[CleanupEntry] = field(default_factory=list)
    removed: list[CleanupEntry] = field(default_factory=list)
    retained: list[CleanupEntry] = field(default_factory=list)
    skipped: list[CleanupEntry] = field(default_factory=list)
    failed: list[CleanupEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reclaimable_bytes: int = 0
    exception_reason: str = ""
    refused: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.errors


def version_key(tag: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(tag)
    if match is None:
        raise ValueError("Use a stable version identifier such as 1.2.3 or 1.2")
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


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
        self.config = normalized_config(config)
        self.base, self.data, self.etc = base, data, etc
        self.unit_file = unit_file
        self.releases = base / "releases"
        self.backups = data / "backups"
        self.current = base / "current"
        self.journal = etc / "transaction.json"
        self.state = etc / "installed.json"
        self.retention = self.config["retention"]
        self.storage_settings = self.config["storage"]
        self.recovery_settings = self.config["recovery"]
        self.readiness_attempts = self.recovery_settings["readiness_attempts"]
        self.recovery_attempts = self.recovery_settings["recovery_readiness_attempts"]
        self.readiness_interval = self.recovery_settings["readiness_interval_seconds"]
        # Only a standard production layout manages the installed updater itself.
        # Tests and custom installations can opt in explicitly with temporary paths.
        self.manage_updater = base == Path("/opt/hnefatafl")
        self.updater_module = Path("/usr/local/lib/hnefatafl/deployment.py")
        self.updater_commands = (
            Path("/usr/local/bin/hnefatafl-update"),
            Path("/usr/local/bin/hnefatafl-backup"),
        )
        self._release_size_cache: dict[Path, tuple[int, int]] = {}
        self._verified_backups: set[Path] = set()
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

    @staticmethod
    def _existing_anchor(path: Path) -> Path:
        anchor = Path(path)
        while not anchor.exists():
            if anchor.parent == anchor:
                break
            anchor = anchor.parent
        if not anchor.exists():
            raise DeploymentError("Storage location does not exist and has no existing parent")
        return anchor

    def filesystem_info(self, path: Path) -> FilesystemInfo:
        """Return byte/inode capacity visible to the deployment owner.

        f_bavail/f_favail are the conservative values available to unprivileged
        writers. Releases are root-owned, but data and build workspaces are written
        by the application user, so the same unprivileged view is used everywhere.
        """
        anchor = self._existing_anchor(path)
        info = os.stat(anchor)
        vfs = os.statvfs(anchor)
        return FilesystemInfo(
            info.st_dev,
            anchor,
            int(vfs.f_bavail) * int(vfs.f_frsize),
            int(vfs.f_favail),
        )

    def _tree_stats(self, path: Path, skip_top=frozenset()) -> tuple[int, int]:
        """Apparent bytes and regular-file/symlink count without following links.

        The snapshot copies each directory entry, so hardlinked files are counted
        per entry rather than deduplicated. This may overestimate shared dependency
        storage, which is the safe direction for a preflight refusal.
        """
        path = Path(path)
        if not path.exists() or path.is_symlink():
            return 0, 0
        total = files = 0
        for current, directories, names in os.walk(path, topdown=True, followlinks=False):
            if Path(current) == path:
                directories[:] = [name for name in directories if name not in skip_top]
                names = [name for name in names if name not in skip_top]
            for name in (*directories, *names):
                candidate = Path(current) / name
                try:
                    info = os.lstat(candidate)
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    total += int(info.st_size)
                    files += 1
                elif stat.S_ISLNK(info.st_mode):
                    files += 1
        return total, files

    def _release_stats(self, path: Path) -> tuple[int, int]:
        key = Path(path)
        if key not in self._release_size_cache:
            self._release_size_cache[key] = self._tree_stats(key)
        return self._release_size_cache[key]

    def snapshot_requirement(self) -> StorageRequirement:
        """Estimate the snapshot using exactly the snapshot inclusion rules."""
        excluded = {
            "backups",
            "cache",
            "hnefatafl.sqlite3",
            "hnefatafl.sqlite3-wal",
            "hnefatafl.sqlite3-shm",
        }
        data_bytes, data_files = self._tree_stats(self.data, excluded)
        config_bytes, config_files = self._tree_stats(self.etc)
        database = self.data / "hnefatafl.sqlite3"
        database_bytes = 0
        if database.is_file() and not database.is_symlink():
            database_bytes = database.stat().st_size
            for suffix in ("-wal", "-shm"):
                sidecar = database.with_name(database.name + suffix)
                if sidecar.is_file() and not sidecar.is_symlink():
                    database_bytes += sidecar.stat().st_size
            # The sqlite backup writes a standalone database without WAL. Use the
            # larger of the live allocation and file size plus a small manifest.
            database_bytes = int(database_bytes * 1.05)
        manifest_bytes = max(MIB, (data_files + config_files + 32) * 512)
        total = data_bytes + config_bytes + database_bytes + manifest_bytes
        margin = int(total * self.storage_settings["snapshot_margin_percent"] / 100)
        return StorageRequirement(
            self.data,
            total + margin,
            "pre-deployment snapshot (data, configuration and SQLite copy)",
            data_files + config_files + 32,
        )

    def _release_history_estimate(self) -> tuple[int, int]:
        peak = 0
        files = 0
        for entry in self._release_entries():
            if entry["kind"] != "complete":
                continue
            measured, count = self._release_stats(entry["path"])
            metadata = entry.get("metadata") or {}
            recorded_peak = metadata.get("peak_bytes")
            recorded_size = metadata.get("size_bytes")
            historical = 0
            for value in (recorded_peak, recorded_size):
                if isinstance(value, int) and not isinstance(value, bool) and value > historical:
                    historical = value
            peak = max(peak, measured, historical)
            files = max(files, count)
        floor = int(self.storage_settings["release_estimate_mib"]) * MIB
        if peak <= 0:
            return floor, 30000
        return peak, max(files, 3000)

    def _prepare_requirements(self, archive_size: int) -> list[StorageRequirement]:
        release_bytes, release_files = self._release_history_estimate()
        archive_bytes = archive_size if archive_size > 0 else 64 * MIB
        # Extraction, the frozen virtual environment, npm ci and the frontend
        # build coexist temporarily. Historical prepared peaks are the primary
        # measurement; the configured floor prevents an empty history from
        # pretending a first release is tiny.
        build_bytes = int(release_bytes * 1.15) + archive_bytes
        cache_bytes = int(self.storage_settings["cache_headroom_mib"]) * MIB
        return [
            StorageRequirement(
                self.base,
                build_bytes,
                "release archive, extraction, virtual environment and frontend build",
                release_files,
            ),
            StorageRequirement(
                self.data,
                cache_bytes,
                "uv/npm dependency cache growth and temporary build files",
                8000,
            ),
            self.snapshot_requirement(),
            StorageRequirement(self.etc, 16 * MIB, "deployment metadata", 64),
        ]

    def _deployment_requirements(self) -> list[StorageRequirement]:
        return [
            self.snapshot_requirement(),
            StorageRequirement(self.base, 16 * MIB, "release activation metadata", 32),
            StorageRequirement(self.etc, 16 * MIB, "transaction journal", 64),
        ]

    def check_storage(
        self,
        requirements: list[StorageRequirement],
        *,
        phase: str,
        service_state: str | None = None,
        persistent_data: str = "unchanged",
        protected_releases: set[Path] | None = None,
    ) -> None:
        groups: dict[int, dict] = {}
        for requirement in requirements:
            if requirement.bytes <= 0 and requirement.files <= 0:
                continue
            info = self.filesystem_info(requirement.path)
            group = groups.setdefault(
                info.device,
                {
                    "info": info,
                    "bytes": 0,
                    "files": 0,
                    "labels": [],
                    "paths": set(),
                },
            )
            group["bytes"] += max(0, int(requirement.bytes))
            group["files"] += max(0, int(requirement.files))
            group["labels"].append(requirement.label)
            group["paths"].add(str(requirement.path))
        headroom = int(self.storage_settings["headroom_mib"]) * MIB
        inode_reserve = int(self.storage_settings["minimum_free_inodes"])
        failures = []
        for group in groups.values():
            required_bytes = group["bytes"] + headroom
            required_inodes = group["files"] + inode_reserve
            info = group["info"]
            if info.available_bytes >= required_bytes and info.available_inodes >= required_inodes:
                continue
            failures.append((group, required_bytes, required_inodes))
        if not failures:
            return
        current = "none"
        try:
            current = self.release_version(
                self.current.resolve() if self.current.is_symlink() else None
            )
        except (OSError, RuntimeError):
            current = "unknown"
        lines = [
            "Update refused before stopping the service."
            if service_state == "running"
            else f"Storage preflight failed during {phase}.",
            "",
        ]
        for group, required_bytes, required_inodes in failures:
            info = group["info"]
            lines.extend(
                [
                    f"Insufficient storage for {phase}.",
                    f"Filesystem: {info.anchor} (locations: {', '.join(sorted(group['paths']))})",
                    f"Available: {format_bytes(info.available_bytes)} "
                    f"({info.available_inodes} inodes)",
                    f"Estimated requirement: {format_bytes(required_bytes)} "
                    f"({required_inodes} inodes) including configured headroom",
                    "Estimated contributors: " + "; ".join(dict.fromkeys(group["labels"])),
                    "The estimate is conservative, not a guarantee; actual compressed "
                    "archive size and dependency downloads vary.",
                    "",
                ]
            )
        lines.extend(
            [
                f"Current release: {current}",
                f"Service: {service_state or 'unchanged'}",
                f"Persistent data: {persistent_data}",
                "",
                "Free additional storage or increase filesystem capacity.",
                "Run hnefatafl-update --cleanup --dry-run to inspect unprotected "
                "historical deployment artifacts, then --cleanup to reclaim them.",
                "Do not delete persistent application data to make room for an update.",
                "Container free space does not guarantee capacity in the underlying "
                "Proxmox thin pool; check host storage as well.",
            ]
        )
        first = failures[0]
        info = first[0]["info"]
        raise StorageError(
            "\n".join(lines),
            phase=phase,
            filesystem=str(info.anchor),
            available_bytes=info.available_bytes,
            required_bytes=first[1],
            available_inodes=info.available_inodes,
            required_inodes=first[2],
        )

    def reclaim_caches(self, *, dry_run: bool = False) -> int:
        """Use supported uv/npm cache maintenance without touching live releases."""
        commands = (
            (
                "uv",
                self.data / "cache/uv",
                [self.base / "tools/bin/uv", "cache", "prune"],
            ),
            (
                "npm",
                self.data / "cache/npm",
                [self.base / "tools/node/bin/npm", "cache", "verify"],
            ),
        )
        reclaimed = 0
        for name, cache, command in commands:
            if not cache.exists():
                continue
            before, _ = self._tree_stats(cache)
            print(f"Dependency cache {name}: {format_bytes(before)}")
            if dry_run:
                continue
            if not Path(command[0]).exists():
                continue
            try:
                self.run(command, user=True)
            except (OSError, subprocess.CalledProcessError) as error:
                print(
                    f"Cache cleanup warning: {name} cache maintenance failed "
                    f"({describe_exception(error)}). The original operation continues.",
                    file=sys.stderr,
                )
                continue
            after, _ = self._tree_stats(cache)
            reclaimed += max(0, before - after)
            if before > after:
                print(f"Reclaimed {format_bytes(before - after)} from the {name} cache.")
        return reclaimed

    def _release_entries(self) -> list[dict]:
        entries: list[dict] = []
        try:
            children = sorted(self.releases.iterdir(), key=lambda item: item.name)
        except FileNotFoundError:
            return entries
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect release storage: {describe_exception(error)}"
            ) from error
        for path in children:
            try:
                info = path.lstat()
            except OSError:
                continue
            entry = {
                "path": path,
                "kind": "unrecognized",
                "version": "",
                "order": float(info.st_mtime),
                "metadata": None,
            }
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                entries.append(entry)
                continue
            if STAGING_DIRECTORY.fullmatch(path.name):
                entry["kind"] = "staging"
                entries.append(entry)
                continue
            match = RELEASE_DIRECTORY.fullmatch(path.name)
            if match is None:
                entries.append(entry)
                continue
            version = match.group("version")
            entry["version"] = version
            metadata, valid, _ = self._load_metadata(path / ".release.json", "release metadata")
            if metadata is None and valid:
                entry["kind"] = "incomplete"
            elif metadata is None or not valid or str(metadata.get("version")) != version:
                entry["kind"] = "invalid"
            else:
                entry["kind"] = "complete"
                entry["metadata"] = metadata
                activated_at = metadata.get("activated_at")
                entry["description"] = (
                    "successfully deployed"
                    if isinstance(activated_at, (int, float)) and not isinstance(activated_at, bool)
                    else "prepared but never activated"
                )
                order = info.st_mtime
                for key in ("prepared_at", "activated_at"):
                    value = metadata.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        order = max(order, float(value))
                entry["order"] = order
            entries.append(entry)
        return entries

    def _load_metadata(self, path: Path, label: str) -> tuple[dict | None, bool, str]:
        if not path.exists():
            return None, True, ""
        if path.is_symlink() or not path.is_file():
            return None, False, f"{label} is not a regular file"
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError, TypeError):
            return None, False, f"{label} is unreadable or invalid JSON"
        if not isinstance(value, dict):
            return None, False, f"{label} is not a JSON object"
        return value, True, ""

    def _backup_status(self, path: Path) -> tuple[str, str]:
        if path in self._verified_backups:
            return "complete", "verified during this operation"
        try:
            info = path.lstat()
        except OSError:
            return "unrecognized", "unreadable"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return "unrecognized", "not a directory"
        if BACKUP_DIRECTORY.fullmatch(path.name) is None:
            return "unrecognized", "unrecognised backup name"
        if not (path / "data").is_dir() or not (path / "config").is_dir():
            return "incomplete", "missing data or configuration directory"
        manifest = path / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            return "incomplete", "missing manifest"
        try:
            self.verify_snapshot(path)
        except (OSError, ValueError, KeyError, TypeError):
            return "invalid", "manifest verification failed"
        database = path / "data/hnefatafl.sqlite3"
        if database.is_file() and not database.is_symlink():
            try:
                with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        return "invalid", "SQLite integrity check failed"
            except sqlite3.Error:
                return "invalid", "SQLite integrity check failed"
        return "complete", ""

    @staticmethod
    def _path_key(value) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return Path(value).resolve()
        except (OSError, RuntimeError):
            return Path(value).absolute()

    def _cleanup_protection(
        self,
        state: dict | None,
        journal: dict | None,
        protected_releases: set[Path] | None,
        protected_backups: set[Path] | None,
    ) -> tuple[dict[Path, str], dict[Path, str]]:
        releases: dict[Path, str] = {}
        backups: dict[Path, str] = {}
        try:
            if self.current.is_symlink():
                key = self._path_key(str(self.current.resolve()))
                if key:
                    releases[key] = "active release referenced by current"
        except (OSError, RuntimeError):
            pass
        if state:
            for field_name, reason in (
                ("target", "recorded installed release"),
                ("previous", "recorded rollback target"),
            ):
                key = self._path_key(state.get(field_name))
                if key:
                    releases[key] = reason
            key = self._path_key(state.get("backup"))
            if key:
                backups[key] = "required by the committed rollback state"
            prior = state.get("prior_state")
            if isinstance(prior, dict):
                for field_name in ("target", "previous"):
                    key = self._path_key(prior.get(field_name))
                    if key:
                        releases.setdefault(key, "prior committed state reference")
                key = self._path_key(prior.get("backup"))
                if key:
                    backups.setdefault(key, "prior committed rollback state")
        if journal:
            for field_name, reason in (
                ("target", "unfinished transaction target"),
                ("previous", "unfinished transaction rollback target"),
            ):
                key = self._path_key(journal.get(field_name))
                if key:
                    releases[key] = reason
            key = self._path_key(journal.get("backup"))
            if key:
                backups[key] = "unfinished transaction snapshot"
            prior = journal.get("prior_state")
            if isinstance(prior, dict):
                for field_name in ("target", "previous"):
                    key = self._path_key(prior.get(field_name))
                    if key:
                        releases.setdefault(key, "prior committed state reference")
                key = self._path_key(prior.get("backup"))
                if key:
                    backups.setdefault(key, "prior committed rollback state")
        for value in protected_releases or set():
            key = self._path_key(str(value))
            if key:
                releases[key] = "release currently being prepared, activated or verified"
        for value in protected_backups or set():
            key = self._path_key(str(value))
            if key:
                backups[key] = "snapshot currently being created or verified"
        return releases, backups

    def cleanup(
        self,
        *,
        dry_run: bool = False,
        automatic: bool = False,
        allow_unfinished: bool = False,
        include_caches: bool = False,
        protected_releases: set[Path] | None = None,
        protected_backups: set[Path] | None = None,
    ) -> CleanupReport:
        """Apply retention to recognised releases, verified backups and caches.

        Unknown directories, symlinks and ambiguous metadata are never removed.
        An unfinished transaction or invalid state metadata disables destructive
        cleanup unless an internal deployment step explicitly protects its live
        references with allow_unfinished.
        """
        report = CleanupReport(dry_run=dry_run, retention=dict(self.retention))
        state, state_ok, state_reason = self._load_metadata(self.state, "installed state")
        journal, journal_ok, journal_reason = self._load_metadata(
            self.journal, "transaction journal"
        )
        unfinished = bool(journal and journal.get("phase") not in {"committed", "recovered"})
        can_delete = state_ok and journal_ok and (not unfinished or allow_unfinished)
        for valid, reason in ((state_ok, state_reason), (journal_ok, journal_reason)):
            if not valid and reason and reason not in report.exception_reason:
                report.exception_reason = (
                    (report.exception_reason + " ") if report.exception_reason else ""
                ) + reason
        if unfinished and not allow_unfinished:
            report.exception_reason = (
                (report.exception_reason + " ") if report.exception_reason else ""
            ) + "unfinished deployment transaction; run hnefatafl-update --recover"
        if (
            not self.state.exists()
            and not allow_unfinished
            and (self.journal.exists() or self.current.is_symlink())
        ):
            can_delete = False
            report.exception_reason = (
                (report.exception_reason + " ") if report.exception_reason else ""
            ) + "installed state metadata is missing for an active installation"
        report.refused = not can_delete
        if not can_delete and not report.exception_reason:
            report.exception_reason = "state metadata could not be validated"

        state_for_protection = state if state_ok else None
        journal_for_protection = journal if journal_ok else None
        if not (unfinished or allow_unfinished):
            journal_for_protection = None
        protected_release_map, protected_backup_map = self._cleanup_protection(
            state_for_protection,
            journal_for_protection,
            protected_releases,
            protected_backups,
        )
        now = time.time()

        def add(category, action, path, reason, *, bytes_size=0, files=0, version=""):
            if action == "remove" and not can_delete and not dry_run:
                action = "retain"
                reason = reason + "; cleanup disabled because " + report.exception_reason
            elif action == "remove" and not can_delete:
                reason = reason + "; preview only: " + report.exception_reason
            entry = CleanupEntry(
                category=category,
                action=action,
                path=Path(path),
                reason=reason,
                bytes=bytes_size,
                files=files,
                version=version,
            )
            report.entries.append(entry)
            if action in {"keep", "retain"}:
                report.retained.append(entry)
            elif action == "skip":
                report.skipped.append(entry)
            return entry

        release_entries = self._release_entries()
        protected_release_count = 0
        protected_versions = set()
        complete_candidates = []
        for entry in release_entries:
            path = entry["path"]
            key = self._path_key(str(path))
            if key in protected_release_map:
                protected_release_count += 1
                if entry["version"] and entry["kind"] == "complete":
                    protected_versions.add(entry["version"])
                add(
                    "release",
                    "keep",
                    path,
                    protected_release_map[key],
                    version=entry["version"],
                )
                continue
            kind = entry["kind"]
            age = max(0.0, now - entry["order"])
            if kind == "staging":
                if age < INCOMPLETE_GRACE_SECONDS:
                    add("staging", "retain", path, "recent staging directory; may be in progress")
                else:
                    add("staging", "remove", path, "incomplete release preparation")
            elif kind == "incomplete":
                if age < INCOMPLETE_GRACE_SECONDS:
                    add(
                        "release",
                        "retain",
                        path,
                        "incomplete release preparation; recent and not yet eligible",
                        version=entry["version"],
                    )
                else:
                    add(
                        "release",
                        "remove",
                        path,
                        "incomplete release preparation (no completion marker)",
                        version=entry["version"],
                    )
            elif kind == "complete":
                complete_candidates.append(entry)
            elif kind == "invalid":
                add(
                    "release",
                    "retain",
                    path,
                    "invalid or inconsistent release metadata; retained for inspection",
                    version=entry["version"],
                )
            else:
                add(
                    "release",
                    "skip",
                    path,
                    "unrecognized directory; never automatically removed",
                )

        slots = max(0, int(self.retention["releases"]) - protected_release_count)
        grouped: dict[str, list[dict]] = {}
        for entry in complete_candidates:
            grouped.setdefault(entry["version"], []).append(entry)
        selected = []
        for version, items in grouped.items():
            items.sort(key=lambda item: (item["order"], item["path"].name), reverse=True)
            if version in protected_versions:
                for duplicate in items:
                    add(
                        "release",
                        "remove",
                        duplicate["path"],
                        "duplicate of a protected release version ("
                        + str(duplicate.get("description", "historical release"))
                        + ")",
                        version=version,
                    )
                continue
            selected.append(items[0])
            for duplicate in items[1:]:
                add(
                    "release",
                    "remove",
                    duplicate["path"],
                    "duplicate release version; newest directory retained ("
                    + str(duplicate.get("description", "historical release"))
                    + ")",
                    version=version,
                )
        selected.sort(key=lambda item: (item["order"], item["path"].name), reverse=True)
        for index, entry in enumerate(selected):
            if index < slots:
                add(
                    "release",
                    "keep",
                    entry["path"],
                    "within configured release retention ("
                    + str(entry.get("description", "historical release"))
                    + ")",
                    version=entry["version"],
                )
            else:
                add(
                    "release",
                    "remove",
                    entry["path"],
                    "beyond configured release retention ("
                    + str(entry.get("description", "historical release"))
                    + ")",
                    version=entry["version"],
                )
        if protected_release_count > int(self.retention["releases"]):
            report.exception_reason = (
                (report.exception_reason + " ") if report.exception_reason else ""
            ) + (
                f"retention exception: {protected_release_count} release directories are "
                f"protected but the configured limit is {self.retention['releases']}; "
                "no protected release was removed"
            )

        try:
            download_children = sorted(self.base.iterdir(), key=lambda item: item.name)
        except (FileNotFoundError, OSError):
            download_children = []
        for path in download_children:
            if not DOWNLOAD_DIRECTORY.fullmatch(path.name):
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            age = max(0.0, now - info.st_mtime)
            if age < INCOMPLETE_GRACE_SECONDS:
                add("download", "retain", path, "recent download staging directory")
            else:
                add("download", "remove", path, "interrupted archive download/extraction staging")

        backup_entries = []
        try:
            backup_children = sorted(self.backups.iterdir(), key=lambda item: item.name)
        except (FileNotFoundError, OSError):
            backup_children = []
        for path in backup_children:
            try:
                info = path.lstat()
            except OSError:
                continue
            if STAGING_DIRECTORY.fullmatch(path.name):
                age = max(0.0, now - info.st_mtime)
                if age < INCOMPLETE_GRACE_SECONDS:
                    add("staging", "retain", path, "recent snapshot staging directory")
                else:
                    add("staging", "remove", path, "incomplete snapshot creation")
                continue
            key = self._path_key(str(path))
            status, detail = self._backup_status(path)
            if status == "complete":
                backup_entries.append((path, info.st_mtime))
            elif status == "incomplete":
                if key in protected_backup_map:
                    add("backup", "keep", path, protected_backup_map[key])
                elif max(0.0, now - info.st_mtime) < INCOMPLETE_GRACE_SECONDS:
                    add("backup", "retain", path, detail + "; recent and not yet eligible")
                else:
                    add("backup", "remove", path, "incomplete snapshot (" + detail + ")")
            elif status == "invalid":
                add(
                    "backup",
                    "retain",
                    path,
                    detail + "; retained for manual inspection",
                )
            else:
                add(
                    "backup",
                    "skip",
                    path,
                    "unrecognized backup directory; never automatically removed",
                )
        protected_backup_count = sum(
            1
            for path, _mtime in backup_entries
            if self._path_key(str(path)) in protected_backup_map
        )
        backup_slots = max(0, int(self.retention["backups"]) - protected_backup_count)
        backup_entries.sort(key=lambda item: (item[1], item[0].name), reverse=True)
        unprotected_backups = []
        for path, mtime in backup_entries:
            key = self._path_key(str(path))
            if key in protected_backup_map:
                add("backup", "keep", path, protected_backup_map[key])
            else:
                unprotected_backups.append((path, mtime))
        for index, (path, _mtime) in enumerate(unprotected_backups):
            if index < backup_slots:
                add("backup", "keep", path, "within configured backup retention")
            else:
                add("backup", "remove", path, "beyond configured backup retention")
        if protected_backup_count > int(self.retention["backups"]):
            report.exception_reason = (
                (report.exception_reason + " ") if report.exception_reason else ""
            ) + (
                f"retention exception: {protected_backup_count} verified backups are "
                f"protected but the configured limit is {self.retention['backups']}; "
                "no protected backup was removed"
            )

        if include_caches:
            for name in ("uv", "npm"):
                cache = self.data / "cache" / name
                if not cache.is_dir():
                    continue
                size, files = self._tree_stats(cache)
                if dry_run:
                    add(
                        "cache",
                        "prune",
                        cache,
                        f"unused {name} cache entries would be pruned (current size)",
                        bytes_size=size,
                        files=files,
                    )
                elif can_delete:
                    add("cache", "prune", cache, f"unused {name} cache entries")
                else:
                    add("cache", "retain", cache, "cache cleanup disabled by metadata state")

        for candidate in report.entries:
            if candidate.action != "remove":
                continue
            try:
                size, files = self._tree_stats(candidate.path)
            except OSError as error:
                size, files = 0, 0
                report.errors.append(
                    f"Cannot measure {candidate.path}: {describe_exception(error)}"
                )
            candidate.bytes, candidate.files = size, files
            if dry_run:
                report.reclaimable_bytes += size
                continue
            if candidate.category == "release":
                root = self.releases
            elif candidate.category == "backup":
                root = self.backups
            elif candidate.category in {"download", "staging"}:
                if candidate.path.parent == self.releases:
                    root = self.releases
                elif candidate.path.parent == self.backups:
                    root = self.backups
                else:
                    root = self.base
            else:
                root = candidate.path.parent
            try:
                self._safe_remove(root, candidate.path)
            except (OSError, DeploymentError) as error:
                candidate.action = "failed"
                candidate.reason = describe_exception(error)
                report.failed.append(candidate)
                report.errors.append(f"Cannot remove {candidate.path}: {describe_exception(error)}")
            else:
                candidate.action = "removed"
                report.removed.append(candidate)
                report.reclaimable_bytes += size

        if include_caches and not dry_run and can_delete:
            report.reclaimable_bytes += self.reclaim_caches(dry_run=False)
        return report

    def _safe_remove(self, root: Path, path: Path) -> None:
        root = Path(root)
        path = Path(path)
        if not root.exists():
            return
        if path.is_symlink():
            raise DeploymentError("Refusing to remove a symlink target")
        if not path.exists():
            return
        if path.parent.resolve() != root.resolve():
            raise DeploymentError("Refusing to remove a path outside its managed directory")
        if path.resolve() == root.resolve():
            raise DeploymentError("Refusing to remove a managed root directory")
        if os.path.ismount(path):
            raise DeploymentError("Refusing to remove an unexpected mount point")
        root_device = os.lstat(root).st_dev
        for current, directories, _names in os.walk(path, topdown=True, followlinks=False):
            try:
                current_info = os.lstat(current)
            except OSError:
                continue
            if current_info.st_dev != root_device:
                raise DeploymentError(
                    f"Refusing to cross filesystem boundary at {current}; investigate the mount"
                )
            if Path(current) != path and os.path.ismount(current):
                raise DeploymentError(
                    f"Refusing to remove through an unexpected mount point at {current}"
                )
            for name in directories:
                child = Path(current) / name
                if child.is_symlink():
                    continue
                try:
                    if os.path.ismount(child):
                        raise DeploymentError(
                            f"Refusing to remove through an unexpected mount point at {child}"
                        )
                    if os.lstat(child).st_dev != root_device:
                        raise DeploymentError(
                            f"Refusing to cross filesystem boundary at {child}; investigate the mount"
                        )
                except OSError as error:
                    raise DeploymentError(
                        f"Cannot inspect {child}: {describe_exception(error)}"
                    ) from error
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        try:
            descriptor = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def print_cleanup_report(self, report: CleanupReport) -> None:
        print(
            ("Storage cleanup (dry run)" if report.dry_run else "Storage cleanup")
            + ": no service restart or database migration is performed."
        )
        print(
            f"Retention limits: releases={report.retention.get('releases')}, "
            f"backups={report.retention.get('backups')}"
        )
        headings = {
            "release": "Releases",
            "backup": "Backups",
            "staging": "Staging",
            "download": "Download staging",
            "cache": "Dependency caches",
        }
        for category in ("release", "backup", "staging", "download", "cache"):
            entries = [entry for entry in report.entries if entry.category == category]
            if not entries:
                continue
            print(headings[category] + ":")
            for entry in sorted(entries, key=lambda item: str(item.path)):
                verb = entry.action.upper()
                if report.dry_run and entry.action == "remove":
                    verb = "WOULD REMOVE"
                label = f"{entry.version} " if entry.version else ""
                size = f" ({format_bytes(entry.bytes)})" if entry.bytes else ""
                print(f"  {verb} {label}{entry.path}: {entry.reason}{size}")
        print(f"Reclaimable: {format_bytes(report.reclaimable_bytes)}")
        if report.exception_reason:
            print(f"Retention exception: {report.exception_reason}")
        if report.errors:
            print("Cleanup errors:", file=sys.stderr)
            for error in report.errors:
                print("  " + error, file=sys.stderr)

    def _remove_orphan_staging(self) -> None:
        """Remove only recognised staging artifacts from an interrupted operation.

        The global deployment lock is held, so no other updater operation owns these
        paths. Completion markers are never used as evidence here: a staging name is
        never a published release or snapshot by construction.
        """
        for root, pattern in (
            (self.releases, STAGING_DIRECTORY),
            (self.backups, STAGING_DIRECTORY),
            (self.base, DOWNLOAD_DIRECTORY),
        ):
            try:
                children = list(root.iterdir())
            except (FileNotFoundError, OSError):
                continue
            for path in children:
                if pattern.fullmatch(path.name) is None:
                    continue
                try:
                    self._safe_remove(root, path)
                except (OSError, DeploymentError) as error:
                    print(
                        f"Staging cleanup warning: {path}: {describe_exception(error)}",
                        file=sys.stderr,
                    )
                else:
                    print(f"Removed interrupted staging artifact: {path}")

    def _post_operation_cleanup(self, operation: str) -> None:
        try:
            report = self.cleanup(dry_run=False, automatic=True)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                print(
                    f"Cleanup warning after {operation}: interrupted. "
                    "The operation itself already completed.",
                    file=sys.stderr,
                )
                return
            print(
                f"Cleanup warning after {operation}: {describe_exception(error)}. "
                "The operation itself already completed.",
                file=sys.stderr,
            )
            return
        if report.removed:
            print(
                f"Storage retention removed {len(report.removed)} artifact(s), reclaiming "
                f"{format_bytes(report.reclaimable_bytes)}."
            )
        for entry in report.failed:
            print(
                f"Cleanup warning after {operation}: {entry.path}: {entry.reason}. "
                "The operation itself already completed.",
                file=sys.stderr,
            )

    def _preflight_or_reclaim(
        self,
        requirements: list[StorageRequirement],
        *,
        phase: str,
        service_state: str | None,
        protected_releases: set[Path] | None,
        persistent_data: str = "unchanged",
    ) -> None:
        try:
            self.check_storage(
                requirements,
                phase=phase,
                service_state=service_state,
                persistent_data=persistent_data,
                protected_releases=protected_releases,
            )
            return
        except StorageError:
            try:
                report = self.cleanup(
                    dry_run=False,
                    automatic=True,
                    allow_unfinished=True,
                    protected_releases=protected_releases,
                )
            except BaseException as error:
                print(
                    f"Storage cleanup before {phase} failed: {describe_exception(error)}",
                    file=sys.stderr,
                )
            else:
                if report.removed:
                    print(f"Reclaimed {format_bytes(report.reclaimable_bytes)} before {phase}.")
            self.reclaim_caches(dry_run=False)
            self.check_storage(
                requirements,
                phase=phase,
                service_state=service_state,
                persistent_data=persistent_data,
                protected_releases=protected_releases,
            )

    def preflight_prepare(self, archive_size: int) -> None:
        self._preflight_or_reclaim(
            self._prepare_requirements(archive_size),
            phase="release preparation",
            service_state="running",
            protected_releases=set(),
        )

    def preflight_deployment(self, target: Path, *, service_state: str | None) -> None:
        self._preflight_or_reclaim(
            self._deployment_requirements(),
            phase="pre-deployment snapshot",
            service_state=service_state,
            protected_releases={Path(target)},
        )

    def _mark_release_activated(self, target: Path) -> None:
        metadata_path = Path(target) / ".release.json"
        metadata, valid, _ = self._load_metadata(metadata_path, "release metadata")
        if metadata is None or not valid:
            return
        metadata["activated_at"] = time.time()
        try:
            atomic_json(metadata_path, metadata)
            os.chmod(metadata_path, 0o644)
        except OSError:
            return

    def _prune_build_outputs(self, target: Path) -> None:
        if not self.storage_settings["prune_build_dependencies"]:
            return
        before, _files = self._tree_stats(target)
        candidates = [
            target / "frontend/node_modules",
            target / "frontend/.vite",
            target / ".pytest_cache",
            target / ".mypy_cache",
            target / ".ruff_cache",
        ]
        candidates.extend(target.rglob("__pycache__"))
        for path in candidates:
            if not path.exists() or path.is_symlink():
                continue
            try:
                if (
                    path.resolve().parent != target.resolve()
                    and target.resolve() not in path.resolve().parents
                ):
                    continue
                shutil.rmtree(path)
            except OSError as error:
                print(
                    f"Build-artifact cleanup warning: {path}: {describe_exception(error)}",
                    file=sys.stderr,
                )
        after, _files = self._tree_stats(target)
        self._release_size_cache = {}
        if before > after:
            print(f"Removed build-only release files, reclaiming {format_bytes(before - after)}.")

    def _install_updater_file(self, source: Path, destination: Path, mode: int) -> None:
        source = Path(source)
        destination = Path(destination)
        if source.is_symlink() or not source.is_file():
            raise DeploymentError(f"Updater source is not a regular file: {source}")
        if destination.is_symlink():
            raise DeploymentError(f"Updater destination is a symlink: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        temporary = destination.with_name(destination.name + ".new-" + secrets.token_hex(4))
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, mode)
            if os.geteuid() == 0:
                os.chown(temporary, 0, 0)
            os.replace(temporary, destination)
            descriptor = os.open(destination.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _refresh_installed_updater(self, target: Path) -> None:
        """Refresh the installed utility from the verified prepared release.

        The running Python process keeps its current module; the next invocation
        uses the new one. Trusted configuration, installed state and transaction
        metadata are never overwritten.
        """
        if not self.manage_updater:
            return
        module = Path(target) / "hnefatafl/deployment.py"
        if module.is_symlink() or not module.is_file():
            raise DeploymentError(f"Updater source is not a regular file: {module}")
        try:
            compile(module.read_text(), str(module), "exec")
        except (OSError, SyntaxError, ValueError) as error:
            raise DeploymentError(
                f"Refusing to install an invalid deployment utility: {describe_exception(error)}"
            ) from error
        sources = (
            (module, self.updater_module, 0o644),
            (Path(target) / "scripts/linux/update.sh", self.updater_commands[0], 0o755),
            (Path(target) / "scripts/linux/backup.sh", self.updater_commands[1], 0o755),
        )
        for source, destination, mode in sources:
            if source != module:
                syntax = subprocess.run(
                    ["bash", "-n", str(source)],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if syntax.returncode != 0:
                    raise DeploymentError(
                        f"Refusing to install an invalid updater wrapper: {source.name}"
                    )
            self._install_updater_file(source, destination, mode)
        print("Installed deployment utility refreshed from the verified release.")

    def refresh_updater(self, github: GitHub, release: dict) -> None:
        """Install only the verified updater files from a published release."""
        if not self.manage_updater:
            raise DeploymentError(
                "The installed deployment utility is not managed by this installation layout"
            )
        tag = release["tag_name"]
        version_key(tag)
        archive_size = self._archive_size(release)
        self._preflight_or_reclaim(
            [
                StorageRequirement(
                    self.base,
                    archive_size + 128 * MIB,
                    "updater archive download and extraction",
                    10000,
                )
            ],
            phase="updater refresh",
            service_state="unchanged",
            protected_releases=set(),
        )
        self.base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.base, prefix="download-") as temporary:
            archive = Path(temporary) / "release.tar.gz"
            github.download(release, archive)
            extracted = Path(temporary) / "release"
            extracted.mkdir(mode=0o700)
            extract(archive, extracted)
            metadata = tomllib.loads((extracted / "pyproject.toml").read_text())
            if metadata.get("project", {}).get("version") != tag:
                raise DeploymentError("Release tag and package version disagree")
            self._refresh_installed_updater(extracted)
        print(f"Deployment utility refreshed from release {tag}.")

    @staticmethod
    def _archive_size(release: dict) -> int:
        for asset in release.get("assets", []):
            if asset.get("name") == f"hnefatafl-{release.get('tag_name')}.tar.gz":
                size = asset.get("size")
                if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                    return size
        return 0

    def snapshot(self) -> Path:
        self.backups.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.backups.chmod(0o700)
        backup = self.backups / (".staging-backup-" + secrets.token_hex(8))
        backup.mkdir(mode=0o700)

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

        try:
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
                        raise DeploymentError("Backup SQLite integrity check failed")
            manifest = {}
            for file in backup.rglob("*"):
                if file.is_symlink():
                    raise DeploymentError(
                        "Backup encountered a symlink; inspect storage before continuing"
                    )
                if file.is_file():
                    manifest[str(file.relative_to(backup))] = checksum(file)
            atomic_json(backup / "manifest.json", manifest)
            self.verify_snapshot(backup)
            final = self.backups / (time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4))
            os.replace(backup, final)
            self._verified_backups.add(final)
        except BaseException:
            try:
                if backup.exists() and not backup.is_symlink():
                    shutil.rmtree(backup)
            except OSError as cleanup_error:
                print(
                    "Snapshot cleanup warning: "
                    f"{describe_exception(cleanup_error)}. The original snapshot failure follows.",
                    file=sys.stderr,
                )
            raise
        try:
            descriptor = os.open(self.backups, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        return final

    def verify_snapshot(self, backup):
        manifest_path = Path(backup) / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise DeploymentError("Backup verification failed: missing manifest")
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise DeploymentError("Backup verification failed: invalid manifest")
        for name, expected in manifest.items():
            if (
                not isinstance(name, str)
                or not isinstance(expected, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
            ):
                raise DeploymentError("Backup verification failed: manifest entry is invalid")
            file = Path(backup) / name
            if (
                Path(name).is_absolute()
                or ".." in Path(name).parts
                or file.is_symlink()
                or not file.is_file()
                or checksum(file) != expected
            ):
                raise DeploymentError("Backup verification failed")
        expected_names = set(manifest)
        for file in Path(backup).rglob("*"):
            if file.is_symlink():
                raise DeploymentError("Backup verification failed: unexpected symlink")
            if not file.is_file():
                continue
            relative = file.relative_to(backup).as_posix()
            if relative != "manifest.json" and relative not in expected_names:
                raise DeploymentError("Backup verification failed: unmanifested file present")

    def restore(self, backup: Path):
        self.verify_snapshot(backup)
        restored = self.data.with_name(self.data.name + ".restore-" + secrets.token_hex(4))
        shutil.copytree(backup / "data", restored)
        # Restoring game/training data must not resurrect logged-out sessions or
        # undo password, role, and status changes made since the snapshot.
        live_db, copy_db = self.data / "hnefatafl.sqlite3", restored / "hnefatafl.sqlite3"
        if live_db.exists() and not copy_db.exists():
            raise DeploymentError(
                "Snapshot has no database; manual recovery is required before replacing live authority"
            )
        if live_db.exists() and copy_db.exists():
            with (
                closing(sqlite3.connect(live_db)) as live,
                closing(sqlite3.connect(copy_db)) as copy,
            ):
                # One live read snapshot for security and authoritative online
                # state. The deployment gate/service stop excludes writers.
                live.execute("BEGIN")
                for table in ("accounts", "auth_meta", "account_limits"):
                    live_columns = [row[1] for row in live.execute(f"PRAGMA table_info({table})")]
                    copy_columns = [row[1] for row in copy.execute(f"PRAGMA table_info({table})")]
                    if copy_columns and live_columns != copy_columns:
                        raise DeploymentError(
                            "Account schema changed; manual recovery is required before restoring security state"
                        )
                    if copy_columns:
                        rows = live.execute(f"SELECT * FROM {table}").fetchall()
                        copy.execute(f"DELETE FROM {table}")
                        copy.executemany(
                            f"INSERT INTO {table} VALUES({','.join('?' for _ in copy_columns)})",
                            rows,
                        )
                self.revoke_restore_capabilities(copy)
                from .online_migrations import retain_online_on_restore

                try:
                    retain_online_on_restore(live, copy)
                except ValueError as error:
                    raise DeploymentError(str(error)) from error
                copy.commit()
        elif copy_db.exists():
            # Disaster recovery without a live DB cannot recover post-snapshot
            # progress. It must still revoke saved capabilities and pause clocks.
            with closing(sqlite3.connect(copy_db)) as copy:
                from .online_migrations import pause_online_after_restore

                self.revoke_restore_capabilities(copy)
                try:
                    pause_online_after_restore(
                        copy,
                        "Snapshot-only recovery; timeout finalization paused for operator reconciliation.",
                    )
                except ValueError as error:
                    raise DeploymentError(str(error)) from error
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

    @staticmethod
    def revoke_restore_capabilities(copy):
        for table in ("sessions", "account_tokens", "account_mail"):
            if copy.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                copy.execute(f"DELETE FROM {table}")
        if copy.execute("SELECT 1 FROM sqlite_master WHERE name='training_workers'").fetchone():
            for identifier, encoded in copy.execute(
                "SELECT id,data FROM training_workers"
            ).fetchall():
                worker = json.loads(encoded)
                worker["revoked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                copy.execute(
                    "UPDATE training_workers SET data=? WHERE id=?",
                    (json.dumps(worker), identifier),
                )

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
        self.base.mkdir(parents=True, exist_ok=True)
        self._remove_orphan_staging()
        tag = release["tag_name"]
        version_key(tag)
        archive_size = self._archive_size(release)
        self.preflight_prepare(archive_size)
        token = secrets.token_hex(8)
        staging = self.releases / (".staging-release-" + token)
        staging.mkdir(mode=0o755)
        final = self.releases / (tag + "-" + token[:8])
        try:
            with tempfile.TemporaryDirectory(dir=self.base, prefix="download-") as temporary:
                archive = Path(temporary) / "release.tar.gz"
                sha256 = github.download(release, archive)
                extract(archive, staging)
            for required in (
                "pyproject.toml",
                "uv.lock",
                "frontend/package-lock.json",
                "hnefatafl/auth.py",
                "tests/test_security.py",
                "deployment/hnefatafl.service",
            ):
                if not (staging / required).is_file():
                    raise ValueError(f"Unsupported release: missing {required}")
            metadata = tomllib.loads((staging / "pyproject.toml").read_text())
            if metadata["project"]["version"] != tag:
                raise ValueError("Release tag and package version disagree")
            self.run(["chown", "-R", "hnefatafl:hnefatafl", staging])
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
                cwd=staging,
                user=True,
            )
            self.run(["npm", "--prefix", "frontend", "ci"], cwd=staging, user=True)
            self.run(["npm", "--prefix", "frontend", "run", "build"], cwd=staging, user=True)
            self.run(
                [
                    staging / ".venv/bin/python",
                    "-m",
                    "pytest",
                    "tests/test_security.py",
                    "-q",
                ],
                cwd=staging,
                user=True,
            )
            peak_bytes, peak_files = self._tree_stats(staging)
            self._prune_build_outputs(staging)
            final_bytes, final_files = self._tree_stats(staging)
            atomic_json(
                staging / ".release.json",
                {
                    "version": tag,
                    "sha256": sha256,
                    "prepared_at": time.time(),
                    "peak_bytes": peak_bytes,
                    "size_bytes": final_bytes,
                    "file_count": final_files,
                },
            )
            self._release_size_cache = {}
            self.run(["chown", "-R", "root:root", staging])
            self.run(["chmod", "-R", "a+rX,a-w", staging])
            os.replace(staging, final)
        except BaseException:
            try:
                if staging.exists() and not staging.is_symlink():
                    shutil.rmtree(staging)
            except OSError as cleanup_error:
                print(
                    "Failed release cleanup warning: "
                    f"{describe_exception(cleanup_error)}. The original preparation "
                    "failure follows.",
                    file=sys.stderr,
                )
            raise
        try:
            descriptor = os.open(self.releases, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        print(
            f"Prepared release {tag}: {format_bytes(final_bytes)} on disk; "
            f"peak preparation usage was {format_bytes(peak_bytes)}."
        )
        return final

    def fetch_url(self, request, *, timeout):
        """HTTP transport seam for disposable integration tests."""
        return urllib.request.urlopen(request, timeout=timeout)

    @staticmethod
    def release_version(path: Path | None) -> str:
        """Best-effort sanitized version for an installed release directory."""
        if path is None:
            return "none"
        try:
            metadata = json.loads((Path(path) / ".release.json").read_text())
            candidate = metadata.get("version") if isinstance(metadata, dict) else None
            if isinstance(candidate, str) and VERSION.fullmatch(candidate):
                return candidate
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return "unknown"

    def _deployment_diagnostics(self) -> dict[str, str]:
        """Best-effort journal/release context; never raises into a failure path."""
        phase, target, previous = "unknown", "unknown", "unknown"
        try:
            transaction = json.loads(self.journal.read_text())
            if isinstance(transaction, dict):
                candidate = transaction.get("phase")
                if isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z-]{0,31}", candidate):
                    phase = candidate
                candidate = transaction.get("target")
                if isinstance(candidate, str):
                    target = self.release_version(Path(candidate))
                candidate = transaction.get("previous")
                if isinstance(candidate, str):
                    previous = self.release_version(Path(candidate))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        installed = "unknown"
        try:
            state = json.loads(self.state.read_text())
            candidate = state.get("version") if isinstance(state, dict) else None
            if isinstance(candidate, str) and VERSION.fullmatch(candidate):
                installed = candidate
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        current = "none"
        try:
            current = (
                self.release_version(self.current.resolve())
                if self.current.is_symlink()
                else "none"
            )
        except (OSError, RuntimeError):
            current = "unknown"
        return {
            "phase": phase,
            "target": target,
            "previous": previous,
            "current": current,
            "installed": installed,
            "recovery_target": getattr(self, "_diagnostic_recovery_target", None) or "none",
            "failure_phase": getattr(self, "_diagnostic_failure_phase", None)
            or "readiness verification",
        }

    def service_status(self) -> str:
        """Allowlisted systemd state for diagnostics; never arbitrary unit output."""
        fields = ("ActiveState", "SubState", "Result", "ExecMainStatus", "NRestarts")
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    "hnefatafl.service",
                    "--no-pager",
                    *(argument for field in fields for argument in ("-p", field)),
                ],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        values = {}
        for line in result.stdout.splitlines():
            name, separator, value = line.partition("=")
            if separator and name in fields and re.fullmatch(r"[A-Za-z0-9_.-]{0,64}", value):
                values[name] = value
        if not values:
            return "unavailable"
        return (
            f"{values.get('ActiveState', 'unknown')} ({values.get('SubState', 'unknown')}), "
            f"result={values.get('Result', 'unknown')}, "
            f"exit={values.get('ExecMainStatus', 'unknown')}, "
            f"restarts={values.get('NRestarts', 'unknown')}"
        )

    @staticmethod
    def _startup_diagnosis(status: str) -> str:
        if "activating" in status or "(start)" in status:
            return (
                "The service is still starting; the readiness window is bounded. "
                "Do not raise the timeout for unrelated failure types."
            )
        if "failed" in status or "result=exit-code" in status or "result=signal" in status:
            return (
                "The service is failing repeatedly. Inspect the first startup exception "
                "in journalctl before retrying; a restart loop does not prove a healthy app."
            )
        return "The service did not become ready within the configured bounded window."

    def _readiness_error(
        self,
        probe: str,
        category: str,
        *,
        attempts: int,
        elapsed: float,
        status: int | None = None,
    ) -> ReadinessError:
        context = self._deployment_diagnostics()
        status_text = "none" if status is None else str(status)
        service_state = self.service_status()
        return ReadinessError(
            f"Service readiness failed during {probe} probe. "
            f"Failure phase: {context['failure_phase']}. "
            f"Deployment phase: {context['phase']}. "
            f"Original target: {context['target']}; "
            f"previous release: {context['previous']}; "
            f"currently probed release: {context['current']}; "
            f"recovery target: {context['recovery_target']}; "
            f"installed release: {context['installed']}. "
            f"Probe category: {category}; last HTTP status: {status_text}. "
            f"Attempts: {attempts}; elapsed: {elapsed:.1f}s. "
            f"Service state: {service_state}. "
            f"Startup diagnosis: {self._startup_diagnosis(service_state)} "
            "Automatic recovery: not evaluated by this readiness probe. "
            f"Inspect: journalctl -u hnefatafl -n 200 --no-pager; "
            f"transaction journal: {self.journal}."
        )

    @staticmethod
    def _transport_category(reason) -> str:
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "connection_timeout"
        if isinstance(reason, ConnectionRefusedError):
            return "connection_refused"
        return "connection_error"

    def _probe_health(self, url: str, host: str, *, attempts_limit: int | None = None) -> None:
        attempts = 0
        started = time.monotonic()
        category, status = "invalid_response", None
        limit = max(1, int(attempts_limit or self.readiness_attempts))
        for attempt in range(limit):
            attempts = attempt + 1
            request = urllib.request.Request(url + "/api/health", headers={"Host": host})
            try:
                with self.fetch_url(request, timeout=READINESS_TIMEOUT_SECONDS) as response:
                    status = int(getattr(response, "status", 200))
                    if status != 200:
                        category = "http_status"
                        raise ReadinessError
                    body = response.read(READINESS_BODY_LIMIT + 1)
                    if len(body) > READINESS_BODY_LIMIT:
                        category = "invalid_response"
                        raise ReadinessError
                    try:
                        payload = json.loads(body)
                    except (ValueError, TypeError):
                        category = "invalid_json"
                        raise ReadinessError from None
                    if payload != {"status": "ok"}:
                        category = "unexpected_health_response"
                        raise ReadinessError
                    return
            except ReadinessError:
                pass
            except http.client.IncompleteRead:
                category, status = "truncated_response", None
            except http.client.HTTPException:
                category, status = "http_protocol_error", None
            except urllib.error.HTTPError as error:
                category, status = "http_status", int(error.code)
            except urllib.error.URLError as error:
                category, status = self._transport_category(error.reason), None
            except (TimeoutError, socket.timeout):
                category, status = "connection_timeout", None
            except OSError:
                category, status = "connection_error", None
            if attempts < limit:
                time.sleep(self.readiness_interval)
        raise self._readiness_error(
            "health",
            category,
            attempts=attempts,
            elapsed=time.monotonic() - started,
            status=status,
        )

    def _probe_authentication(self, url: str, host: str, token: str) -> None:
        started = time.monotonic()
        for path in ("/api/auth/me", "/api/rulesets"):
            request = urllib.request.Request(
                url + path, headers={"Host": host, "Cookie": "hnefatafl_session=" + token}
            )
            try:
                with self.fetch_url(request, timeout=10) as response:
                    status = int(getattr(response, "status", 200))
                    if status != 200:
                        raise self._readiness_error(
                            "authenticated",
                            "authenticated_http_status",
                            attempts=1,
                            elapsed=time.monotonic() - started,
                            status=status,
                        )
                    body = response.read(READINESS_BODY_LIMIT + 1)
                    if len(body) > READINESS_BODY_LIMIT:
                        raise self._readiness_error(
                            "authenticated",
                            "invalid_response",
                            attempts=1,
                            elapsed=time.monotonic() - started,
                        )
                    try:
                        payload = json.loads(body)
                    except (ValueError, TypeError):
                        raise self._readiness_error(
                            "authenticated",
                            "authenticated_invalid_json",
                            attempts=1,
                            elapsed=time.monotonic() - started,
                        ) from None
                    account = payload.get("account") if isinstance(payload, dict) else None
                    permissions = account.get("permissions") if isinstance(account, dict) else None
                    if path.endswith("/me") and (
                        not isinstance(permissions, list) or "training" not in permissions
                    ):
                        raise self._readiness_error(
                            "authenticated",
                            "authenticated_permission_probe_failed",
                            attempts=1,
                            elapsed=time.monotonic() - started,
                            status=status,
                        )
            except ReadinessError:
                raise
            except http.client.IncompleteRead:
                raise self._readiness_error(
                    "authenticated",
                    "truncated_response",
                    attempts=1,
                    elapsed=time.monotonic() - started,
                ) from None
            except http.client.HTTPException:
                raise self._readiness_error(
                    "authenticated",
                    "http_protocol_error",
                    attempts=1,
                    elapsed=time.monotonic() - started,
                ) from None
            except urllib.error.HTTPError as error:
                raise self._readiness_error(
                    "authenticated",
                    "authenticated_http_status",
                    attempts=1,
                    elapsed=time.monotonic() - started,
                    status=int(error.code),
                ) from None
            except urllib.error.URLError as error:
                raise self._readiness_error(
                    "authenticated",
                    self._transport_category(error.reason),
                    attempts=1,
                    elapsed=time.monotonic() - started,
                ) from None
            except (TimeoutError, socket.timeout):
                raise self._readiness_error(
                    "authenticated",
                    "connection_timeout",
                    attempts=1,
                    elapsed=time.monotonic() - started,
                ) from None
            except OSError:
                raise self._readiness_error(
                    "authenticated",
                    "connection_error",
                    attempts=1,
                    elapsed=time.monotonic() - started,
                ) from None

    def _probe_anonymous_rejection(self, url: str, host: str) -> None:
        started = time.monotonic()
        request = urllib.request.Request(url + "/api/rulesets", headers={"Host": host})
        try:
            with self.fetch_url(request, timeout=10) as response:
                status = int(getattr(response, "status", 200))
        except http.client.IncompleteRead:
            raise self._readiness_error(
                "anonymous rejection",
                "truncated_response",
                attempts=1,
                elapsed=time.monotonic() - started,
            ) from None
        except http.client.HTTPException:
            raise self._readiness_error(
                "anonymous rejection",
                "http_protocol_error",
                attempts=1,
                elapsed=time.monotonic() - started,
            ) from None
        except urllib.error.HTTPError as error:
            if int(error.code) == 401:
                return
            raise self._readiness_error(
                "anonymous rejection",
                "anonymous_http_status",
                attempts=1,
                elapsed=time.monotonic() - started,
                status=int(error.code),
            ) from None
        except urllib.error.URLError as error:
            raise self._readiness_error(
                "anonymous rejection",
                self._transport_category(error.reason),
                attempts=1,
                elapsed=time.monotonic() - started,
            ) from None
        except (TimeoutError, socket.timeout):
            raise self._readiness_error(
                "anonymous rejection",
                "connection_timeout",
                attempts=1,
                elapsed=time.monotonic() - started,
            ) from None
        except OSError:
            raise self._readiness_error(
                "anonymous rejection",
                "connection_error",
                attempts=1,
                elapsed=time.monotonic() - started,
            ) from None
        raise self._readiness_error(
            "anonymous rejection",
            "anonymous_access_accepted",
            attempts=1,
            elapsed=time.monotonic() - started,
            status=status,
        )

    def _recovery_probe(self):
        self._readiness_override = self.recovery_attempts
        try:
            self.probe()
        finally:
            self._readiness_override = None

    def probe(self):
        url = f"http://127.0.0.1:{int(self.config['port'])}"
        host = urllib.parse.urlparse(self.config["public_url"]).netloc
        attempts = getattr(self, "_readiness_override", None) or self.readiness_attempts
        self._probe_health(url, host, attempts_limit=attempts)
        with closing(sqlite3.connect(self.data / "hnefatafl.sqlite3")) as c:
            row = c.execute(
                "SELECT id FROM accounts WHERE active=1 AND role='ADMIN' LIMIT 1"
            ).fetchone()
            if not row:
                raise self._readiness_error(
                    "administrator",
                    "no_active_administrator",
                    attempts=1,
                    elapsed=0.0,
                    status=200,
                )
            token = secrets.token_urlsafe(32)
            hashed = hashlib.sha256(token.encode()).hexdigest()
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (hashed, row[0], time.time() + 60, hashlib.sha256(b"local-probe").hexdigest()),
            )
            c.commit()
        try:
            self._probe_authentication(url, host, token)
            self._probe_anonymous_rejection(url, host)
        finally:
            with closing(sqlite3.connect(self.data / "hnefatafl.sqlite3")) as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
                c.commit()

    def _ensure_rollback_target_supported(self, path: Path, *, action: str) -> None:
        version = self.release_version(path)
        reason = INCOMPATIBLE_ROLLBACK_RELEASES.get(version)
        if reason is None:
            return
        raise DeploymentError(
            f"Refusing {action} to unsupported release {version}: {reason}. "
            "No snapshot restore, release switch or service start was attempted; "
            "the current release symlink and persistent data were left unchanged. "
            f"Inspect journalctl -u hnefatafl and {self.journal}; use a corrected "
            "0.41.1 or later release, or perform a reviewed manual recovery. "
            "Do not run older code against migrated data."
        )

    def _verify_restore_preflight(self, backup: Path) -> None:
        """Read-only schema preflight before stopping a healthy service.

        The authoritative restore still performs its complete checks. This only
        detects the common cross-schema refusal early, without modifying live or
        snapshot data.
        """
        snapshot = Path(backup) / "data/hnefatafl.sqlite3"
        live = self.data / "hnefatafl.sqlite3"
        if not snapshot.is_file() or not live.is_file():
            return
        try:
            with (
                closing(sqlite3.connect(f"file:{live}?mode=ro", uri=True)) as current,
                closing(sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)) as saved,
            ):
                live_version = current.execute("PRAGMA user_version").fetchone()[0]
                snapshot_version = saved.execute("PRAGMA user_version").fetchone()[0]
                if max(live_version, snapshot_version) >= 10 and (live_version != snapshot_version):
                    raise DeploymentError(
                        f"Snapshot schema {snapshot_version} differs from live schema "
                        f"{live_version}; automatic rollback would be refused by the "
                        "online schema-authority checks. The service was not stopped "
                        "and persistent data was not replaced. Preserve the snapshot, "
                        "inspect both databases, and perform reviewed manual recovery."
                    )
                for table in ("accounts", "auth_meta", "account_limits"):
                    current_columns = [
                        row[1] for row in current.execute(f"PRAGMA table_info({table})")
                    ]
                    saved_columns = [row[1] for row in saved.execute(f"PRAGMA table_info({table})")]
                    if saved_columns and current_columns != saved_columns:
                        raise DeploymentError(
                            "Account schema changed; manual recovery is required before "
                            "restoring security state. The service was not stopped and "
                            "persistent data was not replaced."
                        )
        except sqlite3.Error as error:
            raise DeploymentError(
                "Snapshot compatibility could not be read safely; manual recovery is required."
            ) from error

    @staticmethod
    def _safe_failure_detail(error: BaseException) -> str:
        return describe_exception(error)

    def deploy(self, target: Path, *, initialize=False):
        with self.write_gate():
            return self._deploy_locked(target, initialize=initialize)

    def _deploy_locked(self, target: Path, *, initialize=False):
        self._diagnostic_failure_phase = None
        self._diagnostic_recovery_target = None
        if active_jobs(self.data):
            raise ValueError(
                "Active/queued training or tournament jobs exist. Cancel/finish jobs and pause remote workers first."
            )
        self.validate_configuration(target)
        try:
            self._refresh_installed_updater(target)
        except (OSError, DeploymentError) as error:
            print(
                "Updater refresh warning: "
                f"{describe_exception(error)}. The update proceeds with the installed utility; "
                "rerun scripts/linux/install.sh from the verified target release to refresh it.",
                file=sys.stderr,
            )
        old = self.current.resolve() if self.current.is_symlink() else None
        old_state, state_valid, state_reason = self._load_metadata(self.state, "installed state")
        if self.state.exists() and not state_valid:
            raise DeploymentError(
                f"Refusing to deploy with invalid installed state: {state_reason}. "
                "The service was not stopped and persistent data is unchanged. "
                "Inspect /etc/hnefatafl before continuing."
            )
        # Refuse predictable storage failures while the application is still
        # running. The target is explicitly protected from the cleanup retry.
        self.preflight_deployment(Path(target), service_state="running")
        transaction = {
            "phase": "prepared",
            "previous": str(old) if old else None,
            "target": str(target),
            "backup": None,
            "prior_state": old_state,
            "service_stopped": False,
            "migration_started": False,
            "data_modified": False,
        }
        self.record(transaction)
        deployment_step = "stopping current service"
        try:
            if old or self.unit_file.exists():
                self.service("stop")
                transaction["service_stopped"] = True
                self.record(transaction)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise DeploymentStepError(
                f"Deployment failed during {deployment_step}: "
                f"{self._safe_failure_detail(error)}. The service may still be running; "
                f"inspect {self.journal} and journalctl -u hnefatafl -n 200 --no-pager."
            ) from error
        try:
            deployment_step = "post-stop activity check"
            if active_jobs(self.data):
                raise DeploymentError(
                    "New job activity detected during shutdown; update refused, retained state preserved"
                )
            deployment_step = "post-stop storage preflight"
            self.preflight_deployment(Path(target), service_state="stopped")
            deployment_step = "pre-deployment snapshot"
            transaction["phase"] = "snapshot"
            self.record(transaction)
            transaction["backup"] = str(self.snapshot())
            transaction["phase"] = "prepared"
            self.record(transaction)
            deployment_step = "database migration"
            transaction["phase"] = "migrating"
            transaction["migration_started"] = True
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
            transaction["data_modified"] = True
            deployment_step = "initial administrator provisioning"
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
            deployment_step = "release activation"
            self.activate(target)
            shutil.copyfile(
                target / "deployment/hnefatafl.service",
                self.unit_file,
            )
            deployment_step = "service unit update"
            self.run(["systemctl", "daemon-reload"])
            transaction["phase"] = "activated"
            self.record(transaction)
            deployment_step = "service startup"
            self.service("start")
            deployment_step = "readiness verification"
            self._diagnostic_failure_phase = "readiness verification"
            self.probe()
            deployment_step = "commit"
            self._mark_release_activated(target)
            release_metadata = json.loads((target / ".release.json").read_text())
            committed_state = {
                **release_metadata,
                "version": release_metadata.get("version"),
                "previous": transaction["previous"],
                "backup": transaction["backup"],
                "target": transaction["target"],
                "phase": "committed",
            }
            atomic_json(self.state, committed_state)
            transaction["phase"] = "committed"
            transaction["prior_state"] = None
            self.record(transaction)
            print("Release ready: " + str(target))
        except BaseException as error:
            recovery_step = "cleaning interrupted staging artifacts"
            self._diagnostic_recovery_target = self.release_version(old) if old else "none"
            self._diagnostic_failure_phase = f"{deployment_step}; automatic recovery"
            try:
                self._remove_orphan_staging()
            except BaseException as cleanup_error:
                if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)):
                    raise
                print(
                    "Staging cleanup warning before automatic recovery: "
                    f"{describe_exception(cleanup_error)}",
                    file=sys.stderr,
                )
            recovery_step = "stopping failed target service"
            legacy_phase = transaction.get("phase")
            needs_restore = bool(
                transaction.get("migration_started")
                or (
                    transaction.get("migration_started") is None
                    and legacy_phase in {"migrating", "activated"}
                )
            ) and bool(transaction.get("backup"))
            try:
                if old or self.unit_file.exists():
                    self.service("stop")
                recovery_step = "unsupported rollback policy check"
                if old:
                    self._ensure_rollback_target_supported(old, action="automatic recovery")
                recovery_step = "snapshot compatibility preflight"
                if needs_restore:
                    self._verify_restore_preflight(Path(str(transaction["backup"])))
                recovery_step = "snapshot restoration"
                if needs_restore:
                    self.restore(Path(str(transaction["backup"])))
                if old:
                    recovery_step = "previous release activation"
                    self.activate(old)
                    shutil.copyfile(
                        old / "deployment/hnefatafl.service",
                        self.unit_file,
                    )
                    self.run(["systemctl", "daemon-reload"])
                    recovery_step = "previous release service startup"
                    self.service("start")
                    recovery_step = "previous release readiness verification"
                    self._diagnostic_failure_phase = (
                        "readiness verification during automatic recovery"
                    )
                    self._recovery_probe()
                elif self.current.is_symlink():
                    recovery_step = "release symlink cleanup"
                    self.current.unlink()
                recovery_step = "installed state restoration"
                if old_state is not None:
                    atomic_json(self.state, old_state)
                else:
                    self.state.unlink(missing_ok=True)
                transaction["phase"] = "recovered"
                self.record(transaction)
            except BaseException as recovery_error:
                if isinstance(error, ReadinessError):
                    detail = self._safe_failure_detail(recovery_error)
                    raise ReadinessError(
                        f"{error} Automatic recovery: failed during {recovery_step}. {detail}"
                    ) from recovery_error
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                detail = self._safe_failure_detail(recovery_error)
                primary = self._safe_failure_detail(error)
                raise DeploymentStepError(
                    f"Deployment failed during {deployment_step}: {primary}; "
                    f"automatic recovery failed during {recovery_step}. {detail}. "
                    f"Inspect {self.journal} and journalctl -u hnefatafl -n 200 --no-pager."
                ) from recovery_error
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            self._post_operation_cleanup("deployment recovery")
            context = (
                "Database migration started: "
                + ("yes" if transaction.get("migration_started") else "no")
                + "; persistent data: "
                + ("restored from the verified snapshot" if needs_restore else "unchanged")
                + "; service: "
                + ("started and verified on the previous release" if old else "stopped")
                + "."
            )
            if isinstance(error, ReadinessError):
                if old:
                    raise ReadinessError(
                        f"{error} Automatic recovery: succeeded; "
                        "previous release restored and verified. " + context
                    ) from error
                raise ReadinessError(
                    f"{error} Automatic recovery: not applicable; "
                    "initial installation has no previous release and remains stopped. " + context
                ) from error
            if old:
                outcome = "automatic recovery succeeded; previous release restored and verified"
            else:
                outcome = "automatic recovery not applicable; initial installation remains stopped"
            raise DeploymentStepError(
                f"Deployment failed during {deployment_step}: "
                f"{self._safe_failure_detail(error)}; {outcome}. {context} "
                f"Inspect {self.journal} and journalctl -u hnefatafl -n 200 --no-pager."
            ) from error
        self._post_operation_cleanup("deployment")

    def recover(self, *, rollback=False):
        with self.write_gate():
            return self._recover_locked(rollback=rollback)

    def _service_is_active(self) -> bool:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", "hnefatafl.service"],
                env=self.env,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _recover_locked(self, *, rollback=False):
        self._diagnostic_failure_phase = None
        self._diagnostic_recovery_target = None
        source = self.state if rollback else self.journal
        transaction, valid, reason = self._load_metadata(source, "recovery metadata")
        if not valid or transaction is None:
            raise DeploymentError(
                f"Cannot recover from invalid {source.name}: {reason}. "
                "No service stop, data restore or release switch was attempted."
            )
        self._remove_orphan_staging()
        phase = transaction.get("phase")
        if not rollback and phase in {"committed", "recovered"}:
            print("No interrupted deployment to recover.")
            return
        previous, backup = transaction.get("previous"), transaction.get("backup")
        if rollback and not previous:
            raise ValueError("No previous release to roll back to")
        if previous:
            self._diagnostic_recovery_target = self.release_version(Path(str(previous)))
            self._ensure_rollback_target_supported(
                Path(str(previous)), action="rollback" if rollback else "interrupted recovery"
            )
        if rollback and previous and not backup:
            raise DeploymentError(
                "No verified pre-deployment snapshot is recorded for this rollback. "
                "Running the previous release against the current database would be "
                "unsafe. The service was not stopped and no release or data was changed. "
                "Inspect installed.json and perform reviewed manual recovery if needed."
            )
        if active_jobs(self.data):
            raise DeploymentError("Finish/cancel training and tournaments before recovery")
        if rollback:
            needs_restore = bool(backup)
        else:
            migrated = bool(transaction.get("migration_started"))
            if transaction.get("migration_started") is None:
                migrated = phase in {"migrating", "activated"}
            needs_restore = bool(migrated and backup)
            if migrated and not backup:
                raise DeploymentError(
                    "Interrupted deployment may have started database migration but no "
                    "snapshot is recorded. Automatic recovery would be unsafe. The service "
                    "remains stopped; inspect the transaction journal and journalctl, then "
                    "perform reviewed manual recovery."
                )
        prior_state = transaction.get("prior_state")
        if not isinstance(prior_state, dict):
            prior_state = None
        if needs_restore:
            self._diagnostic_failure_phase = "rollback compatibility preflight"
            self._verify_restore_preflight(Path(str(backup)))
            self.preflight_deployment(
                Path(str(previous)) if previous else self.current,
                service_state="stopped",
            )
        recovery_step = "checking current service"
        try:
            current = self.current.resolve() if self.current.is_symlink() else None
            already_healthy = False
            if not needs_restore and previous and current == Path(str(previous)):
                if self._service_is_active():
                    recovery_step = "existing service readiness verification"
                    self._diagnostic_failure_phase = "readiness verification during " + (
                        "rollback" if rollback else "interrupted recovery"
                    )
                    try:
                        self._recovery_probe()
                    except ReadinessError:
                        already_healthy = False
                    else:
                        already_healthy = True
            if not already_healthy:
                recovery_step = "stopping current service"
                self.service("stop")
                if needs_restore:
                    recovery_step = "snapshot restoration"
                    self.restore(Path(str(backup)))
                if previous:
                    recovery_step = "previous release activation"
                    self.activate(Path(str(previous)))
                    shutil.copyfile(
                        Path(str(previous)) / "deployment/hnefatafl.service",
                        self.unit_file,
                    )
                    self.run(["systemctl", "daemon-reload"])
                    recovery_step = "previous release service startup"
                    self.service("start")
                    recovery_step = "previous release readiness verification"
                    self._diagnostic_failure_phase = "readiness verification during " + (
                        "rollback" if rollback else "interrupted recovery"
                    )
                    self._recovery_probe()
                elif self.current.is_symlink():
                    recovery_step = "release symlink cleanup"
                    self.current.unlink()
            recovery_step = "installed state restoration"
            if rollback:
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
            elif prior_state is not None:
                # Restoring the pre-deployment snapshot restores the committed
                # application state that described it. Keep its rollback target
                # and snapshot instead of writing previous/backup as null.
                atomic_json(self.state, prior_state)
            else:
                installed, installed_valid, _ = self._load_metadata(self.state, "installed state")
                previous_version = self.release_version(Path(str(previous))) if previous else None
                recorded_version = (
                    installed.get("version") if installed_valid and installed else None
                )
                if previous and recorded_version != previous_version:
                    # The state file records a target that was never committed (or
                    # was externally corrupted). Reconstruct only the release metadata
                    # and do not invent rollback references that are no longer valid.
                    atomic_json(
                        self.state,
                        {
                            **json.loads((Path(previous) / ".release.json").read_text()),
                            "previous": None,
                            "backup": None,
                        },
                    )
                elif not previous:
                    self.state.unlink(missing_ok=True)
            self.record({"phase": "recovered"})
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, ReadinessError):
                raise
            raise DeploymentStepError(
                f"Recovery failed during {recovery_step}: "
                f"{self._safe_failure_detail(error)}. "
                f"Inspect {self.journal} and journalctl -u hnefatafl -n 200 --no-pager."
            ) from error
        self._post_operation_cleanup("recovery")


def protected_json(path: Path):
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o077 or path.is_symlink():
        raise ValueError(f"Configuration must be a root-owned 0600 regular file: {path}")
    return json.loads(path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    for flag in (
        "check",
        "list",
        "rollback",
        "recover",
        "backup",
        "cleanup",
        "refresh-updater",
        "initialize",
    ):
        options.add_argument("--" + flag, action="store_true")
    parser.add_argument("--version")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview --cleanup without modifying files or restarting the service",
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.cleanup:
        parser.error("--dry-run is only supported with --cleanup")
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
            if args.cleanup:
                report = deployment.cleanup(
                    dry_run=args.dry_run,
                    include_caches=True,
                )
                deployment.print_cleanup_report(report)
                if report.failed or report.errors:
                    return 1
                if report.refused and not args.dry_run:
                    return 1
                return 0
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
                created = None
                with deployment.write_gate():
                    if active_jobs(deployment.data):
                        raise ValueError("Stop/cancel active jobs before backup")
                    deployment.service("stop")
                    try:
                        created = deployment.snapshot()
                        print("Verified backup: " + str(created))
                    finally:
                        deployment.service("start")
                # The new manual backup is protected for this cleanup pass even when
                # pre-existing recovery references already consume the configured
                # slots; the resulting exception is reported rather than silently
                # deleting the backup the operator just requested.
                report = deployment.cleanup(
                    dry_run=False,
                    automatic=True,
                    protected_backups={created} if created else None,
                )
                deployment.print_cleanup_report(report)
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
            if args.refresh_updater:
                print(
                    f"Installed: {current or 'none'}; refreshing deployment utility from "
                    f"release {selected['tag_name']}"
                )
                deployment.refresh_updater(github, selected)
                return 0
            print(
                f"Installed: {current or 'none'}; selected stable release: {selected['tag_name']}"
            )
            if not newer:
                print("No newer stable release. Use --rollback for a recorded recovery.")
                return 0
            if args.check:
                return 0
            try:
                target = deployment.prepare(github, selected)
            except (ValueError, OSError, subprocess.CalledProcessError, shutil.Error) as error:
                if isinstance(error, StorageError):
                    raise
                raise DeploymentStepError(
                    "Release preparation failed: " + describe_exception(error) + ". "
                    "The active release and persistent data are unchanged."
                ) from error
            deployment.deploy(target, initialize=args.initialize)
            return 0
    except (
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        shutil.Error,
        sqlite3.Error,
        KeyboardInterrupt,
    ) as error:
        # No credentials, authorization headers or remote URL query values appear
        # in exception output. Deliberate deployment errors carry actionable text;
        # filesystem errors are reduced to errno/strerror and affected paths.
        print(f"Deployment failed: {describe_exception(error)}", file=sys.stderr)
        print(
            "Inspect /etc/hnefatafl/transaction.json for the recorded phase and "
            "journalctl -u hnefatafl -n 200 --no-pager for service errors.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
