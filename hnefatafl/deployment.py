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
import importlib.util
import json
import os
import pwd
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
import types
from contextlib import ExitStack, closing, contextmanager, suppress
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
# Transaction phases this utility understands. A utility must never install a
# replacement with a lower protocol number: it could not resume the journal phases
# or interpret the retention/restore metadata written by this one.
UPDATER_ABI = 3
MIN_GENERATION_ABI = 3
MIN_LAUNCHER_PROTOCOL = 1
JOURNAL_PHASES = {
    "prepared",
    "snapshot",
    "migrating",
    "activating",
    "activated",
    "committed",
    "committing",
    "recovered",
    "recovering",
}
RESTORE_PHASES = {
    "prepared",
    "staged",
    "stopping",
    "data-renamed",
    "published",
    "backups-moved",
    "gate-moved",
    "ownership-set",
    "activated",
    "started",
    "verified",
}
# Root-owned updater support package installed beside deployment.py so the
# standalone utility can perform schema-aware restore without importing a source
# checkout. Order matters only for the coherent publication set.
UPDATER_SUPPORT_MODULES = (
    "online_migrations",
    "league_migrations",
    "league_policy",
    "league_recovery_migrations",
    "rating",
)
STAGING_MARKER = ".staging.json"
RELEASE_MARKER = ".release.json"
SNAPSHOT_METADATA = "snapshot.json"
GATE_NAME = ".deployment-gate"
RESTORE_MARKER = ".restore.json"
JOURNAL_RESERVE = ".transaction-reserve"
JOURNAL_RESERVE_BYTES = MIB
JOURNAL_HEADROOM_BYTES = 16 * MIB
RESTORE_STAGING_DIRECTORY = re.compile(r"^\.restore-[0-9a-f]{8,32}$")
RETAINED_DIRECTORY = re.compile(r"^\.retained-[0-9a-f]{8,32}$")
TREE_OVERHEAD_PER_DIRECTORY = 4096
TREE_OVERHEAD_PER_FILE = 512
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
PENDING_UPDATER_DIRECTORY = re.compile(r"^\.pending-[a-f0-9]{16}$")
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


class JournalWriteError(DeploymentError):
    """A transaction publication failed and still needs operator attention."""


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
    backup_directory = storage.get("backup_directory")
    if backup_directory is not None:
        if not isinstance(backup_directory, str) or not backup_directory:
            raise ValueError("storage.backup_directory must be an absolute path string")
        candidate = Path(backup_directory)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(
                "storage.backup_directory must be a normal absolute path outside the "
                "application-writable data root"
            )
        storage["backup_directory"] = str(candidate)
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
    stage: str | None = None

    @property
    def inodes(self) -> int:
        return self.files


@dataclass(frozen=True)
class FilesystemInfo:
    device: int
    anchor: Path
    available_bytes: int
    available_inodes: int
    # Some filesystems (network/overlay/tmpfs with inode64) do not report a
    # meaningful unprivileged inode budget. Byte checks still apply and the
    # inode budget is reported as unavailable instead of silently passing.
    inode_reporting: bool = True
    allocation_unit: int = TREE_OVERHEAD_PER_DIRECTORY


@dataclass(frozen=True)
class Usage:
    """Existing tree usage and the entry counts needed to estimate copies.

    ``bytes`` estimates existing size for historical release and cleanup reports.
    ``allocated_bytes`` counts observed blocks once per inode. The reclaimable
    totals exclude inodes with links outside the scanned tree. The apparent
    size and entry counts include each name independently, including hardlinks.
    """

    bytes: int = 0
    inodes: int = 0
    files: int = 0
    directories: int = 0
    apparent_bytes: int = 0
    allocated_bytes: int = 0
    hardlink_bytes: int = 0
    reclaimable_bytes: int = 0
    reclaimable_inodes: int = 0

    def independent_copy_bytes(self, allocation_unit: int) -> int:
        # Charge the full logical payload, including sparse regions, and allow
        # for rounding each file separately to a destination allocation unit.
        return (
            self.apparent_bytes
            + self.files * (allocation_unit - 1)
            + self.inodes * TREE_OVERHEAD_PER_FILE
            + self.directories * max(allocation_unit, TREE_OVERHEAD_PER_DIRECTORY)
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            bytes=self.bytes + other.bytes,
            inodes=self.inodes + other.inodes,
            files=self.files + other.files,
            directories=self.directories + other.directories,
            apparent_bytes=self.apparent_bytes + other.apparent_bytes,
            allocated_bytes=self.allocated_bytes + other.allocated_bytes,
            hardlink_bytes=self.hardlink_bytes + other.hardlink_bytes,
            reclaimable_bytes=self.reclaimable_bytes + other.reclaimable_bytes,
            reclaimable_inodes=self.reclaimable_inodes + other.reclaimable_inodes,
        )


@dataclass(frozen=True)
class StagingRecord:
    operation: str
    mixed: bool
    keep: bool
    preparation_id: str | None = None


@dataclass
class CleanupEntry:
    category: str
    action: str
    path: Path
    reason: str
    bytes: int = 0
    files: int = 0
    version: str = ""
    allocated_bytes: int = 0
    hardlink_bytes: int = 0
    status: str = ""
    identity: tuple[int, int] | None = None


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
    # Apparent size of artifacts selected for removal.
    reclaimable_bytes: int = 0
    # Observed blocks excluding inodes with links outside each selected artifact.
    reclaimable_allocated_bytes: int = 0
    # Actual filesystem free-space delta observed after removal.
    reclaimed_bytes: int = 0
    hardlink_bytes: int = 0
    status_counts: dict = field(default_factory=dict)
    exception_reason: str = ""
    refused: bool = False
    verified_backups: int = 0
    manifest_bytes_hashed: int = 0
    duration_seconds: float = 0.0
    cache_failures: list[str] = field(default_factory=list)
    cleanup_complete: bool = True

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


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value) -> None:
    atomic_bytes(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def unique_metadata_fields(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON metadata fields")
    return value


def raise_walk_error(error: OSError) -> None:
    raise error


def sync_tree(path: Path, *, allow_symlinks: bool = False) -> None:
    for directory, _, files in os.walk(path, topdown=False, onerror=raise_walk_error):
        for name in files:
            entry = Path(directory) / name
            if allow_symlinks and entry.is_symlink():
                continue
            descriptor = os.open(entry, os.O_NOFOLLOW | os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        sync_directory(Path(directory))


def file_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def valid_identity(value) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(type(part) is int and part >= 0 for part in value)
        and value[1] > 0
    )


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _has_escape(root: Path, candidate: Path) -> bool:
    """Return True when a resolved path would escape its managed root."""
    try:
        Path(candidate).relative_to(root)
        return False
    except ValueError:
        return True


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


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


@dataclass(frozen=True)
class RootIdentity:
    """Validated identity of a managed directory."""

    path: Path
    device: int
    inode: int
    description: str


class PrivilegedFilesystem:
    """Descriptor-relative, fail-closed filesystem operations for root code.

    The application user owns the persistent data root and its ancestors are the
    only paths this class trusts blindly. Managed directories are validated
    (regular directory, expected owner, not group/world writable) before use, and
    every recursive deletion walks by directory descriptor with ``O_NOFOLLOW`` so
    that a concurrent rename to a symlink cannot redirect the operation. Nested
    mounts are rejected from the kernel mount table, which also detects
    same-filesystem bind mounts and single-file mounts that ``st_dev`` misses.
    """

    def __init__(self, *, mountinfo: Path = Path("/proc/self/mountinfo"), euid=None):
        self.mountinfo = Path(mountinfo)
        self.euid = os.geteuid() if euid is None else euid
        # An unprivileged namespace can present its system root with a mapped
        # owner. The privileged CLI still requires uid 0 for trusted ancestors.
        self.system_uid = 0 if self.euid == 0 else os.stat("/").st_uid
        self._mounts: tuple[Path, ...] | None = None
        self._mount_set: set[str] | None = None

    def mount_points(self) -> tuple[Path, ...]:
        if self._mounts is not None:
            return self._mounts
        try:
            lines = self.mountinfo.read_text().splitlines()
        except OSError as error:
            raise DeploymentError(
                "Cannot inspect the kernel mount table; refusing privileged "
                f"filesystem operations ({describe_exception(error)})"
            ) from error
        points = set()
        for line in lines:
            fields = line.split()
            if len(fields) < 5:
                continue
            raw = _unescape_mount(fields[4])
            point = Path(os.path.normpath(raw))
            if not point.is_absolute():
                raise DeploymentError(
                    "Cannot interpret the kernel mount table; refusing privileged "
                    "filesystem operations"
                )
            points.add(point)
        self._mounts = tuple(sorted(points))
        self._mount_set = {str(point) for point in self._mounts}
        return self._mounts

    def nested_mounts(self, path: Path) -> list[Path]:
        root = Path(os.path.normpath(str(Path(path).absolute())))
        prefix = str(root) + os.sep
        return [
            point
            for point in self.mount_points()
            if str(point).startswith(prefix) and point != root
        ]

    def is_mount(self, path: Path) -> bool:
        return str(Path(os.path.normpath(str(Path(path).absolute())))) in self.mount_points_set()

    def mount_points_set(self) -> set[str]:
        self.mount_points()
        assert self._mount_set is not None
        return self._mount_set

    def _validate_ancestors(self, path: Path, description: str) -> None:
        for ancestor in reversed(path.parents):
            try:
                info = os.lstat(ancestor)
            except OSError as error:
                raise DeploymentError(
                    f"Cannot inspect the ancestor {ancestor} of {description}: "
                    f"{describe_exception(error)}. Privileged filesystem operations "
                    "are refused."
                ) from error
            if stat.S_ISLNK(info.st_mode):
                raise DeploymentError(
                    f"The ancestor {ancestor} of {description} is a symlink; refusing "
                    "privileged filesystem operations"
                )
            elif not stat.S_ISDIR(info.st_mode):
                raise DeploymentError(
                    f"The ancestor {ancestor} of {description} is not a directory; "
                    "refusing privileged filesystem operations"
                )
            elif info.st_uid not in {self.system_uid, self.euid}:
                raise DeploymentError(
                    f"The ancestor {ancestor} of {description} is owned by an untrusted "
                    "account; refusing privileged filesystem operations"
                )
            elif not self._acceptable_ancestor_mode(info.st_mode):
                raise DeploymentError(
                    f"The ancestor {ancestor} of {description} is writable by another "
                    "account; refusing privileged filesystem operations"
                )

    @staticmethod
    def _acceptable_ancestor_mode(mode: int) -> bool:
        if not mode & 0o022:
            return True
        # Sticky world-writable temporary directories (for example /tmp) cannot be
        # used to rename or replace another account's entries.
        return bool(mode & stat.S_ISVTX)

    def validate_root(
        self,
        path: Path,
        *,
        owner: int,
        description: str,
        mode: int | None = None,
        allow_nested_mounts: bool = False,
    ) -> RootIdentity:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise DeploymentError(f"The {description} path must be a normal absolute path: {path}")
        try:
            info = os.lstat(path)
        except FileNotFoundError as error:
            raise DeploymentError(f"The {description} does not exist: {path}") from error
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect the {description} ({path}): {describe_exception(error)}"
            ) from error
        if stat.S_ISLNK(info.st_mode):
            raise DeploymentError(
                f"The {description} is a symlink ({path}); refusing privileged filesystem "
                "operations. Replace it with a real root-owned directory."
            )
        if not stat.S_ISDIR(info.st_mode):
            raise DeploymentError(f"The {description} is not a directory: {path}")
        if info.st_uid != owner:
            raise DeploymentError(
                f"The {description} ({path}) is owned by uid {info.st_uid}, expected "
                f"uid {owner}; refusing privileged filesystem operations"
            )
        if mode is not None and stat.S_IMODE(info.st_mode) & ~mode:
            raise DeploymentError(
                f"The {description} ({path}) has permissions "
                f"{stat.S_IMODE(info.st_mode):04o}, which exceed the permitted "
                f"{mode:04o}; refusing privileged filesystem operations"
            )
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise DeploymentError(
                f"The {description} ({path}) is group/world writable; refusing "
                "privileged filesystem operations"
            )
        self._validate_ancestors(path, description)
        nested = self.nested_mounts(path)
        if nested and not allow_nested_mounts:
            shown = ", ".join(str(item) for item in nested[:3])
            raise DeploymentError(
                f"The {description} ({path}) contains nested mount points ({shown}); "
                "refusing privileged filesystem operations that could cross or hide "
                "another filesystem"
            )
        return RootIdentity(path, int(info.st_dev), int(info.st_ino), description)

    @contextmanager
    def open_directory(self, root: RootIdentity):
        descriptor = os.open(root.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (root.device, root.inode):
                raise DeploymentError(
                    f"The {root.description} changed during inspection: {root.path}"
                )
            yield descriptor
        finally:
            os.close(descriptor)

    def identity_of(self, path: Path) -> tuple[int, int]:
        try:
            info = os.lstat(path)
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect {path}: {describe_exception(error)}; refusing to delete"
            ) from error
        return int(info.st_dev), int(info.st_ino)

    def remove_tree(
        self,
        root: RootIdentity,
        target: Path,
        *,
        expected_owner: int | set[int],
        identity: tuple[int, int] | None = None,
        allow_symlinks_inside: bool = True,
        allow_nested: bool = False,
    ) -> None:
        """Remove one validated artifact below ``root`` without following links."""
        target = Path(target)
        owners = {expected_owner} if isinstance(expected_owner, int) else expected_owner
        try:
            relative = target.relative_to(root.path)
        except ValueError as error:
            raise DeploymentError(
                f"Refusing to remove {target}: it is outside the managed {root.description} "
                f"({root.path})"
            ) from error
        if not relative.parts or relative.name in {"", ".", ".."}:
            raise DeploymentError(
                f"Refusing to remove {target}: it is not a named artifact below the "
                f"managed {root.description}"
            )
        if len(relative.parts) != 1 and not allow_nested:
            raise DeploymentError(
                f"Refusing to remove {target}: only direct children of the managed "
                f"{root.description} may be removed"
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            root_fd = os.open(root.path, flags)
        except OSError as error:
            raise DeploymentError(
                f"Cannot open the managed {root.description} ({root.path}): "
                f"{describe_exception(error)}; refusing to delete"
            ) from error
        try:
            current = os.fstat(root_fd)
            if (int(current.st_dev), int(current.st_ino)) != (root.device, root.inode):
                raise DeploymentError(
                    f"The managed {root.description} ({root.path}) changed identity after "
                    "validation; refusing to delete"
                )
            parent_fd = root_fd
            opened: list[int] = []
            try:
                for part in relative.parts[:-1]:
                    try:
                        descriptor = os.open(part, flags, dir_fd=parent_fd)
                    except OSError as error:
                        raise DeploymentError(
                            f"Cannot traverse {root.path / part} for deletion: "
                            f"{describe_exception(error)}; refusing to continue"
                        ) from error
                    opened.append(descriptor)
                    info = os.fstat(descriptor)
                    if int(info.st_dev) != root.device:
                        raise DeploymentError(
                            f"Refusing to cross the filesystem boundary at {root.path / part}"
                        )
                    if info.st_uid not in owners:
                        raise DeploymentError(
                            f"Refusing to traverse {root.path / part}: it is owned by "
                            f"uid {info.st_uid}, expected {expected_owner}"
                        )
                    parent_fd = descriptor
                self._remove_entry(
                    root,
                    parent_fd,
                    relative.name,
                    root.path / relative,
                    expected_owner=expected_owner,
                    identity=identity,
                    top=True,
                    allow_symlinks_inside=allow_symlinks_inside,
                )
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
            try:
                os.fsync(root_fd)
            except OSError:
                pass
        finally:
            os.close(root_fd)

    def _remove_entry(
        self,
        root: RootIdentity,
        parent_fd: int,
        name: str,
        display: Path,
        *,
        expected_owner: int | set[int],
        identity: tuple[int, int] | None,
        top: bool,
        allow_symlinks_inside: bool,
    ) -> None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect {display}: {describe_exception(error)}; refusing to delete"
            ) from error
        if stat.S_ISLNK(info.st_mode):
            if top:
                raise DeploymentError(
                    f"Refusing to remove the symlink {display}; managed artifacts must be "
                    "real directories or files"
                )
            # Unlinking an inner symlink removes the link itself, never its target.
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError as error:
                raise DeploymentError(
                    f"Cannot remove the symlink {display}: {describe_exception(error)}"
                ) from error
            return
        if identity is not None and (int(info.st_dev), int(info.st_ino)) != identity:
            raise DeploymentError(
                f"Refusing to remove {display}: its identity changed since it was inspected"
            )
        owners = {expected_owner} if isinstance(expected_owner, int) else expected_owner
        if info.st_uid not in owners:
            raise DeploymentError(
                f"Refusing to remove {display}: it is owned by uid {info.st_uid}, "
                f"expected uid {expected_owner}"
            )
        if os.path.normpath(str(display)) in self.mount_points_set():
            raise DeploymentError(
                f"Refusing to remove {display}: it is a mount point, not a managed artifact"
            )
        if stat.S_ISDIR(info.st_mode):
            try:
                child_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise DeploymentError(
                    f"Cannot open {display} for deletion: {describe_exception(error)}; "
                    "refusing to continue"
                ) from error
            try:
                current = os.fstat(child_fd)
                if (int(current.st_dev), int(current.st_ino)) != (
                    int(info.st_dev),
                    int(info.st_ino),
                ):
                    raise DeploymentError(
                        f"Refusing to delete {display}: the directory was replaced during deletion"
                    )
                if int(current.st_dev) != root.device:
                    raise DeploymentError(
                        f"Refusing to cross the filesystem boundary at {display}: it is "
                        "not part of the managed filesystem"
                    )
                with os.scandir(child_fd) as entries:
                    names = [entry.name for entry in entries]
                for child in names:
                    self._remove_entry(
                        root,
                        child_fd,
                        child,
                        display / child,
                        expected_owner=expected_owner,
                        identity=None,
                        top=False,
                        allow_symlinks_inside=allow_symlinks_inside,
                    )
            finally:
                os.close(child_fd)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError as error:
                raise DeploymentError(
                    f"Cannot remove {display}: {describe_exception(error)}. If an "
                    "application process is still writing there, stop it and retry."
                ) from error
            return
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError as error:
            raise DeploymentError(
                f"Cannot remove {display}: {describe_exception(error)}"
            ) from error

    def write_staging_marker(self, directory: Path, operation: str) -> None:
        marker = Path(directory) / STAGING_MARKER
        atomic_json(
            marker,
            {
                "schema": 1,
                "operation": operation,
                "created_at": time.time(),
                "pid": os.getpid(),
            },
        )

    def read_staging_marker(self, directory: Path) -> dict | None:
        marker = Path(directory) / STAGING_MARKER
        try:
            info = os.lstat(marker)
        except OSError:
            return None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self.euid
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            return None
        try:
            value = json.loads(marker.read_text())
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        if value.get("schema") != 1:
            return None
        if not isinstance(value.get("operation"), str):
            return None
        created = value.get("created_at")
        if isinstance(created, bool) or not isinstance(created, (int, float)):
            return None
        return value


def scan_usage(path: Path, skip_top=frozenset()) -> Usage:
    """Measure a tree without following links; fail closed on inspection errors."""
    path = Path(path)
    try:
        top = os.lstat(path)
    except FileNotFoundError:
        return Usage()
    except OSError as error:
        raise DeploymentError(
            f"Cannot inspect {path}: {describe_exception(error)}. Storage requirements "
            "cannot be estimated safely."
        ) from error
    if stat.S_ISLNK(top.st_mode):
        raise DeploymentError(
            f"Refusing to measure the symlinked storage location {path}; inspect the "
            "installation before continuing"
        )
    if not stat.S_ISDIR(top.st_mode):
        raise DeploymentError(f"Storage location is not a directory: {path}")
    usage = Usage()
    links: dict[tuple[int, int], tuple[int, int, int]] = {}

    def add(info, *, directory: bool) -> None:
        nonlocal usage
        allocated = int(getattr(info, "st_blocks", 0)) * 512
        if directory:
            usage = usage + Usage(
                bytes=TREE_OVERHEAD_PER_DIRECTORY,
                inodes=1,
                directories=1,
                allocated_bytes=allocated,
                reclaimable_bytes=allocated,
                reclaimable_inodes=1,
            )
            return
        size = int(info.st_size)
        seen = 0
        if info.st_nlink > 1:
            key = file_identity(info)
            seen, link_count, _ = links.get(key, (0, 0, 0))
            links[key] = (seen + 1, max(link_count, info.st_nlink), allocated)
        usage = usage + Usage(
            bytes=(0 if seen else max(size, allocated)) + TREE_OVERHEAD_PER_FILE,
            inodes=1,
            files=1,
            apparent_bytes=size,
            allocated_bytes=0 if seen else allocated,
            hardlink_bytes=size if seen else 0,
            reclaimable_bytes=allocated if info.st_nlink == 1 else 0,
            reclaimable_inodes=int(info.st_nlink == 1),
        )

    add(top, directory=True)
    stack = [(path, set(skip_top))]
    while stack:
        current, skipped = stack.pop()
        try:
            with os.scandir(current) as entries:
                names = list(entries)
        except OSError as error:
            raise DeploymentError(
                f"Cannot scan {current}: {describe_exception(error)}. Storage "
                "requirements cannot be estimated safely."
            ) from error
        for entry in names:
            if entry.name in skipped:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise DeploymentError(
                    f"Cannot inspect {entry.path}: {describe_exception(error)}. Storage "
                    "requirements cannot be estimated safely."
                ) from error
            if stat.S_ISDIR(info.st_mode):
                add(info, directory=True)
                stack.append((Path(entry.path), set()))
            elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                add(info, directory=False)
    contained = [blocks for seen, count, blocks in links.values() if seen == count]
    return usage + Usage(reclaimable_bytes=sum(contained), reclaimable_inodes=len(contained))


class VerificationCache:
    """Per-operation snapshot verification cache invalidated by content change.

    The cache key includes inode, size, modification and change time, so replacing
    or rewriting a snapshot forces a fresh hash. A manifest or timestamp is never
    accepted on its own as proof of integrity.
    """

    def __init__(self):
        self._verified: dict[Path, tuple[tuple[int, ...], str]] = {}
        self.bytes_hashed = 0
        self.verifications = 0

    @staticmethod
    def signature(path: Path) -> tuple[int, ...]:
        """Metadata-only signature covering the whole tree.

        Directory mtimes do not change when a contained file is rewritten, so the
        signature walks entry metadata (no content reads) and folds inode, size,
        modification and change times into an aggregate. A replaced or rewritten
        file therefore forces a fresh content verification.
        """
        path = Path(path)
        info = os.lstat(path)
        base = (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns),
        )
        if not stat.S_ISDIR(info.st_mode):
            return base
        aggregate = hashlib.sha256()
        count = 0
        digest = 0
        for current, directories, names in os.walk(path, topdown=True, followlinks=False):
            for name in (*directories, *names):
                entry = os.path.join(current, name)
                try:
                    entry_info = os.lstat(entry)
                except OSError:
                    aggregate.update(b"unreadable\0")
                    count += 1
                    continue
                aggregate.update(
                    (
                        f"{entry}\0{entry_info.st_ino}\0{entry_info.st_size}\0"
                        f"{entry_info.st_mtime_ns}\0{entry_info.st_ctime_ns}\0"
                    ).encode("utf-8", "surrogateescape")
                )
                count += 1
        if count:
            digest = int.from_bytes(aggregate.digest()[:16], "big")
        return base + (count, digest)

    def get(self, path: Path) -> str | None:
        entry = self._verified.get(Path(path))
        if entry is None:
            return None
        try:
            if self.signature(path) != entry[0]:
                self._verified.pop(Path(path), None)
                return None
        except OSError:
            self._verified.pop(Path(path), None)
            return None
        return entry[1]

    def put(self, path: Path, digest: str) -> None:
        try:
            self._verified[Path(path)] = (self.signature(path), digest)
        except OSError:
            self._verified.pop(Path(path), None)

    def hashed(self, size: int) -> None:
        self.bytes_hashed += max(0, int(size))


class Deployment:
    def __init__(
        self,
        config: dict,
        *,
        base=Path("/opt/hnefatafl"),
        data=Path("/var/lib/hnefatafl"),
        etc=Path("/etc/hnefatafl"),
        unit_file=Path("/etc/systemd/system/hnefatafl.service"),
        backups: Path | None = None,
        filesystem: PrivilegedFilesystem | None = None,
        updater_root: Path | None = None,
        custom_layout: bool = False,
    ):
        self.config = normalized_config(config)
        self.base, self.data, self.etc = base, data, etc
        self.unit_file = unit_file
        self.releases = base / "releases"
        self.temporary_root = base / "tmp"
        # Snapshots are root-owned secrets. Keeping them directly inside the
        # application-user-owned data root let that account rename the directory
        # and redirect privileged snapshot writes; the sibling default is outside
        # the writable tree and on the same filesystem in the standard layout.
        configured = self.config["storage"].get("backup_directory")
        if backups is not None:
            self.backups = Path(backups)
        elif configured:
            self.backups = Path(configured)
        else:
            self.backups = data.parent / (data.name + "-backups")
        self.legacy_backups = data / "backups"
        self.current = base / "current"
        self.journal = etc / "transaction.json"
        self.state = etc / "installed.json"
        self.updater_state = etc / "updater.json"
        self.migration_journal = etc / "backup-migration.json"
        self.retention = self.config["retention"]
        self.storage_settings = self.config["storage"]
        self.recovery_settings = self.config["recovery"]
        self.readiness_attempts = self.recovery_settings["readiness_attempts"]
        self.recovery_attempts = self.recovery_settings["recovery_readiness_attempts"]
        self.readiness_interval = self.recovery_settings["readiness_interval_seconds"]
        # Only a standard production layout manages the installed updater itself.
        # Tests and custom installations can opt in explicitly with temporary paths.
        self.manage_updater = base == Path("/opt/hnefatafl")
        self.updater_root = (
            Path(updater_root) if updater_root is not None else Path("/usr/local/lib/hnefatafl")
        )
        self.updater_module = self.updater_root / "deployment.py"
        self.updater_support = self.updater_root / "updater_support"
        self.updater_commands = (
            Path("/usr/local/bin/hnefatafl-update"),
            Path("/usr/local/bin/hnefatafl-backup"),
        )
        self.fs = filesystem or PrivilegedFilesystem()
        # In production this is uid 0. The disposable test suite runs unprivileged,
        # where the same checks apply to the suite's own account instead.
        self.privileged_uid = os.geteuid()
        self._release_size_cache: dict[Path, tuple[tuple[int, ...], Usage]] = {}
        self.verification = VerificationCache()
        self._snapshot_schema_cache: dict[tuple[int, ...], int] = {}
        self._online_support: types.ModuleType | None = None
        self._layout_ready = False
        self._blocked_staging: dict[Path, str] = {}
        self._held_gate: tuple[int, int] | None = None
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
        if custom_layout and os.environ.get("PATH"):
            # Disposable integration layouts (never the fixed production layout)
            # may provide their own tool shims such as a systemd stand-in.
            self.env["PATH"] = os.environ["PATH"] + ":" + self.env["PATH"]
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
        ):
            if name in os.environ:
                self.env[name] = os.environ[name]

    def run(self, args, *, cwd=None, user=False, extra_env=None, preparation=False):
        command = [str(value) for value in args]
        environment = {**self.env, **(extra_env or {})}
        if user:
            uid, gid = self._data_account()
            if uid != self.privileged_uid:
                if preparation:
                    self._run_isolated_preparation(command, cwd, environment, uid, gid)
                    return
                command = ["runuser", "-u", pwd.getpwuid(uid).pw_name, "--", *command]
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    def _run_isolated_preparation(self, command, cwd, environment, uid: int, gid: int) -> None:
        workspace = Path(environment["HNEFATAFL_DATA_DIR"]).parent
        executable = shutil.which(command[0], path=environment["PATH"])
        if executable is None:
            raise DeploymentError(f"Preparation executable is unavailable: {command[0]}")
        unit = (
            f"hnefatafl-{workspace.name.removeprefix('.staging-')}-{secrets.token_hex(4)}.service"
        )
        units = self._preparation_units(workspace) or set()
        units.add(unit)
        atomic_json(workspace / ".units.json", {"units": sorted(units)})
        armed = workspace / ".units-allowed"
        atomic_bytes(armed, b"")
        # Systemd reads this root-only file before switching to the service
        # account. Proxy credentials stay out of arguments and unit properties.
        environment_file = workspace / "environment"
        lines = []
        for key, value in environment.items():
            value = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("`", "\\`")
                .replace("$", "\\$")
            )
            lines.append(f'{key}="{value}"\n')
        atomic_bytes(environment_file, "".join(lines).encode())
        subprocess.run(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--pipe",
                "--wait",
                "--collect",
                f"--unit={unit}",
                f"--uid={uid}",
                f"--gid={gid}",
                f"--working-directory={cwd}",
                "--property=ProtectSystem=strict",
                "--property=KillMode=control-group",
                "--property=TimeoutStopSec=30s",
                f"--property=ConditionPathExists={armed}",
                f"--property=InaccessiblePaths={self.data}",
                f"--property=ReadWritePaths={workspace} {cwd}",
                "--property=NoNewPrivileges=yes",
                "--property=ProtectProc=invisible",
                f"--property=EnvironmentFile={environment_file}",
                "--",
                executable,
                *command[1:],
            ],
            env=environment,
            check=True,
        )

    def service(self, action):
        self.run(["systemctl", action, "hnefatafl.service"])

    def record(self, value):
        phase = value["phase"]
        self._publish_transaction_metadata(
            self.journal,
            value,
            phase=phase,
            use_reserve=value.get("operation") in {"automatic-recovery", "rollback"}
            or phase in {"committing", "committed", "recovered"},
        )

    def _journal_storage_requirement(self) -> StorageRequirement:
        try:
            reserved = (self.etc / JOURNAL_RESERVE).lstat()
        except FileNotFoundError:
            reserved = None
        allocated = min(reserved.st_blocks * 512, JOURNAL_RESERVE_BYTES) if reserved else 0
        return StorageRequirement(
            self.etc,
            JOURNAL_HEADROOM_BYTES + JOURNAL_RESERVE_BYTES - allocated,
            "transaction journal and recovery reserve",
            64 + int(reserved is None),
        )

    def _reserve_journal_space(self, *, reclaim: bool = False) -> bool:
        root = self.fs.validate_root(
            self.etc, owner=self.privileged_uid, description="configuration directory"
        )
        with self.fs.open_directory(root) as directory:
            flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            try:
                fd = os.open(
                    JOURNAL_RESERVE,
                    flags | (os.O_RDONLY if reclaim else os.O_WRONLY | os.O_CREAT),
                    0o600,
                    dir_fd=directory,
                )
            except FileNotFoundError:
                if reclaim:
                    return False
                raise
            with os.fdopen(fd, "rb" if reclaim else "wb") as handle:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_dev != root.device
                    or info.st_uid != self.privileged_uid
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > JOURNAL_RESERVE_BYTES
                ):
                    raise DeploymentError(f"Unsafe journal reserve at {self.etc / JOURNAL_RESERVE}")
                if reclaim:
                    os.unlink(JOURNAL_RESERVE, dir_fd=directory)
                else:
                    if (
                        info.st_size < JOURNAL_RESERVE_BYTES
                        or info.st_blocks * 512 < JOURNAL_RESERVE_BYTES
                    ):
                        # Write real blocks on sparse- and compression-capable filesystems.
                        handle.write(os.urandom(JOURNAL_RESERVE_BYTES))
                        handle.flush()
                    os.fsync(fd)
            os.fsync(directory)
        return True

    def _publish_transaction_metadata(self, path, value, *, phase, use_reserve):
        def publish():
            if value is None:
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
            else:
                atomic_json(path, value)

        try:
            publish()
        except OSError as error:
            detail = describe_exception(error)
            if use_reserve and error.errno in {errno.ENOSPC, errno.EDQUOT}:
                try:
                    if self._reserve_journal_space(reclaim=True):
                        publish()
                        return
                except (OSError, DeploymentError) as retry:
                    detail += (
                        f". Using the recovery reserve also failed: {describe_exception(retry)}"
                    )
            if error.errno in {errno.ENOSPC, errno.EDQUOT}:
                action = (
                    f"Free blocks and an inode on the filesystem holding {path.parent} "
                    "or resolve its quota"
                )
            else:
                action = f"Restore reliable write access to the filesystem holding {path.parent}"
            raise JournalWriteError(
                f"Could not durably publish {path} for phase {phase}: {detail}. "
                f"{action} if recovery is pending, then run hnefatafl-update --recover."
            ) from error

    # ------------------------------------------------------------------
    # Managed directories and metadata validation
    # ------------------------------------------------------------------
    def _release_root(self, *, write: bool = False) -> RootIdentity:
        return self._managed_root(
            self.releases,
            owner=self.privileged_uid,
            mode=0o755,
            description="release directory",
            write=write,
        )

    def _backup_root(self, *, write: bool = False) -> RootIdentity:
        return self._managed_root(
            self.backups,
            owner=self.privileged_uid,
            mode=0o700,
            description="backup directory",
            write=write,
        )

    def _base_root(self) -> RootIdentity:
        root = self.fs.validate_root(
            self.base,
            owner=self.privileged_uid,
            mode=0o755,
            description="deployment base directory",
            allow_nested_mounts=True,
        )
        unexpected = set(self.fs.nested_mounts(self.base)) - {self.temporary_root}
        if unexpected:
            raise DeploymentError(
                f"The deployment base contains unsupported nested mount points: {sorted(unexpected)}"
            )
        return root

    def _managed_root(
        self, path: Path, *, owner: int, mode: int, description: str, write: bool = False
    ) -> RootIdentity:
        path = Path(path)
        if write and not path.exists():
            self.fs._validate_ancestors(path, description)
            try:
                path.mkdir(mode)
            except FileExistsError:
                pass
        return self.fs.validate_root(path, owner=owner, description=description, mode=mode)

    @staticmethod
    def _staging_intent(path: Path) -> Path:
        return path.with_name(f".staging-intent-{path.name.removeprefix('.')}.json")

    def _begin_staging(
        self,
        path: Path,
        operation: str,
        mode: int = 0o700,
        *,
        preparation_id: str | None = None,
    ) -> tuple[int, int]:
        root = self.fs.validate_root(
            path.parent,
            owner=self.privileged_uid,
            description="staging parent",
            allow_nested_mounts=path.parent == self.data.parent,
        )
        marker = self._staging_intent(path)
        if os.path.lexists(path) or os.path.lexists(marker):
            raise DeploymentError(f"Staging already exists: {path}")
        intent = {
            "schema": 1,
            "operation": operation,
            "name": path.name,
            "parent": [root.device, root.inode],
            "identity": None,
        }
        if preparation_id is not None:
            if not re.fullmatch(r"[a-f0-9]{16}", preparation_id):
                raise DeploymentError("Invalid preparation operation identity")
            intent["preparation_id"] = preparation_id
        atomic_json(marker, intent)
        with self.fs.open_directory(root) as directory:
            os.mkdir(path.name, mode, dir_fd=directory)
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            try:
                os.fchmod(descriptor, mode)
                identity = file_identity(os.fstat(descriptor))
                os.fsync(descriptor)
                os.fsync(directory)
            finally:
                os.close(descriptor)
        intent["identity"] = list(identity)
        atomic_json(marker, intent)
        return identity

    def _finish_staging(self, path: Path) -> None:
        self._staging_intent(path).unlink(missing_ok=True)
        sync_directory(path.parent)

    def _staging_identity(self, path: Path) -> tuple[int, int] | None:
        marker = self._staging_intent(path)
        intent, valid, _ = self._load_metadata(marker, "staging intent")
        if (
            valid
            and intent is None
            and path.parent == self.updater_root
            and PENDING_UPDATER_DIRECTORY.fullmatch(path.name)
        ):
            manifest, readable, _ = self._load_metadata(
                path / "manifest.json", "pending updater manifest"
            )
            digest = manifest.get("archive_sha256") if readable and manifest else None
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                or self._pending_updater_for(digest) != path
            ):
                raise DeploymentError(f"Pending updater has no verified operation record: {path}")
            self._updater_publication_plan(path)
            return self.fs.identity_of(path)
        if (
            not valid
            or intent is None
            or intent.get("schema") != 1
            or intent.get("name") != path.name
            or "identity" not in intent
            or "preparation_id" in intent
            and (
                not isinstance(intent["preparation_id"], str)
                or not re.fullmatch(r"[a-f0-9]{16}", intent["preparation_id"])
            )
            or intent.get("parent") != list(self.fs.identity_of(path.parent))
            or intent.get("identity") is not None
            and not valid_identity(intent["identity"])
        ):
            raise DeploymentError(f"Staging ownership cannot be established for {path}")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        identity = file_identity(info)
        expected = intent["identity"]
        if not stat.S_ISDIR(info.st_mode):
            raise DeploymentError(f"Staging is no longer a real directory: {path}")
        if expected is None:
            # Before the second record only mkdir/fchmod can have run.
            if info.st_uid != self.privileged_uid or any(path.iterdir()):
                raise DeploymentError(f"Unidentified staging directory retained: {path}")
        elif expected != list(identity):
            raise DeploymentError(f"Staging identity changed: {path}")
        return identity

    def _discard_staging(self, path: Path, *, mixed: bool = False) -> None:
        identity = self._staging_identity(path)
        if identity is not None:
            owners = {self.privileged_uid, self._data_owner()} if mixed else {self.privileged_uid}
            self._safe_remove(path.parent, path, identity=identity, owners=owners, mixed=mixed)
        self._finish_staging(path)

    def _temporary_workspace(self, operation: str, *, preparation_id: str | None = None) -> Path:
        self._base_root()
        if not os.path.lexists(self.temporary_root):
            self.temporary_root.mkdir(mode=0o755)
            descriptor = os.open(
                self.temporary_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            try:
                os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            sync_directory(self.base)
        self.fs.validate_root(
            self.temporary_root,
            owner=self.privileged_uid,
            mode=0o755,
            description="build temporary directory",
        )
        identity = preparation_id or secrets.token_hex(8)
        temporary = self.temporary_root / f".staging-{operation}-{identity}"
        self._begin_staging(temporary, operation, 0o711, preparation_id=identity)
        return temporary

    def _preparation_environment(self, workspace: Path) -> dict[str, str]:
        uid, gid = self._data_account()
        for name in ("data", "home", "tmp", "uv", "npm"):
            path = workspace / name
            path.mkdir(mode=0o700)
            if uid != self.privileged_uid:
                os.chown(path, uid, gid, follow_symlinks=False)
        return {
            "HNEFATAFL_DATA_DIR": str(workspace / "data"),
            "HOME": str(workspace / "home"),
            "XDG_CACHE_HOME": str(workspace / "home/.cache"),
            "TMPDIR": str(workspace / "tmp"),
            "UV_CACHE_DIR": str(workspace / "uv"),
            "npm_config_cache": str(workspace / "npm"),
            "UV_LINK_MODE": "copy",
        }

    def ensure_layout(self, *, protected_backups: set[Path] | None = None) -> None:
        """Validate and, when needed, migrate the root-owned deployment layout.

        A legacy installation kept verified snapshots directly inside the
        application-user-owned data directory. They are relocated once into the
        root-owned sibling backup root before any privileged snapshot operation, and
        recorded metadata references are rewritten atomically.
        """
        if self._layout_ready:
            return
        self._migrate_legacy_backups(protected_backups)
        self._layout_ready = True

    @contextmanager
    def _open_legacy_backups(self):
        legacy = self.legacy_backups
        if self.fs.is_mount(legacy) or self.fs.nested_mounts(legacy):
            raise DeploymentError(f"The legacy backup directory contains a mount: {legacy}")
        with self.fs.open_directory(self._data_directory(self.data)) as data_fd:
            try:
                legacy_fd = os.open(
                    "backups",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=data_fd,
                )
            except OSError as error:
                raise DeploymentError(
                    f"The legacy backup location {legacy} is not a real directory or changed during inspection"
                ) from error
            try:
                info = os.fstat(legacy_fd)
                if info.st_uid != self.privileged_uid or stat.S_IMODE(info.st_mode) != 0o700:
                    raise DeploymentError(
                        f"The legacy backup location {legacy} must be owner-controlled 0700"
                    )
                root = RootIdentity(legacy, info.st_dev, info.st_ino, "legacy backup directory")
                yield root, data_fd, legacy_fd
            finally:
                os.close(legacy_fd)

    def _inspect_legacy_backup(self, path: Path, legacy_fd: int) -> tuple[str, str]:
        status, detail = self._backup_status(path, parent_fd=legacy_fd)
        if status == "incomplete":
            parts = {item.name for item in path.iterdir()}
            if (
                "data" not in parts
                or parts - {"data", "config"}
                or any(
                    not stat.S_ISDIR(item.lstat().st_mode)
                    or item.lstat().st_uid != self.privileged_uid
                    for item in path.iterdir()
                )
            ):
                return (
                    "invalid",
                    "ambiguous legacy staging shape; review its contents before removing it",
                )
            try:
                self._check_legacy_tree_ownership(path)
            except DeploymentError as error:
                return "invalid", str(error)
        return status, detail

    def _check_legacy_tree_ownership(self, path: Path) -> None:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise DeploymentError("A legacy snapshot is no longer a directory")
        device = path.stat().st_dev
        for folder, dirs, files in os.walk(path, followlinks=False, onerror=raise_walk_error):
            for name in (*dirs, *files):
                info = (Path(folder) / name).lstat()
                if (
                    info.st_uid != self.privileged_uid
                    or info.st_mode & 0o022
                    or info.st_dev != device
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                ):
                    raise DeploymentError(
                        "Untrusted content in incomplete legacy snapshot; review it before removing it"
                    )

    @staticmethod
    def _backup_mount_id(directory: int) -> str:
        details = Path(f"/proc/self/fdinfo/{directory}").read_text()
        for line in details.splitlines():
            key, _, value = line.partition(":")
            if key == "mnt_id":
                return value.strip()
        raise DeploymentError("Cannot establish the mount identity of a backup location")

    def _legacy_copy_required(self, source_fd: int, dest_fd: int) -> bool:
        return os.fstat(source_fd).st_dev != os.fstat(dest_fd).st_dev or self._backup_mount_id(
            source_fd
        ) != self._backup_mount_id(dest_fd)

    def _backup_inventory(
        self,
    ) -> list[tuple[Path, os.stat_result, str, str, Path | None, str | None]]:
        with ExitStack() as stack:
            dest_fd = (
                stack.enter_context(self.fs.open_directory(self._backup_root()))
                if os.path.lexists(self.backups)
                else None
            )
            legacy_fd = (
                stack.enter_context(self._open_legacy_backups())[2]
                if os.path.lexists(self.legacy_backups)
                else None
            )
            planned = (
                self._legacy_migration_entries(legacy_fd, dest_fd) if legacy_fd is not None else []
            )
            return self._inventory_backups(legacy_fd, dest_fd, planned)

    def _inventory_backups(
        self,
        source_fd: int | None,
        dest_fd: int | None,
        planned: list[dict],
    ) -> list[tuple[Path, os.stat_result, str, str, Path | None, str | None]]:
        entries: list[tuple[Path, os.stat_result, str, str, Path | None, str | None]] = []
        if dest_fd is not None:
            for name in sorted(os.listdir(dest_fd)):
                if name.startswith(".staging-intent-"):
                    continue
                path = self.backups / name
                info = os.stat(name, dir_fd=dest_fd, follow_symlinks=False)
                status, detail = self._backup_status(path)
                entries.append((path, info, status, detail, None, None))
        if source_fd is None:
            return entries
        with ExitStack() as stack:
            target_fd = dest_fd
            if target_fd is None:
                self.fs._validate_ancestors(self.backups, "backup directory")
                target_fd = os.open(
                    self.backups.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                )
                stack.callback(os.close, target_fd)
            kind = "copy" if self._legacy_copy_required(source_fd, target_fd) else "move"
        names = {entry["source"]: entry["target"] for entry in planned}
        for name in sorted(os.listdir(source_fd)):
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            view = Path(f"/proc/self/fd/{source_fd}") / name
            status, detail = self._inspect_legacy_backup(view, source_fd)
            if status == "complete" and name not in names:
                raise DeploymentError(f"Legacy backup inventory changed during inspection: {view}")
            target = self.backups / names[name] if status == "complete" else None
            entries.append((self.legacy_backups / name, info, status, detail, target, kind))
        return entries

    def _retained_backups(
        self,
        entries: list[tuple[Path, float, Path]],
        protected: set[Path],
    ) -> set[Path]:
        kept = {path for path, _, _ in entries if path in protected}
        slots = max(0, self.retention["backups"] - len(kept))
        ordered = sorted(entries, key=lambda item: (item[1], item[2].name), reverse=True)
        kept.update([path for path, _, _ in ordered if path not in protected][:slots])
        return kept

    def _migration_protected_backups(self, extra: set[Path] | None = None) -> set[Path]:
        state = self._installed_metadata()
        journal, valid, reason = self._load_metadata(self.journal, "transaction journal")
        if valid and journal is not None:
            valid, reason = self.validate_transaction_metadata(journal)
        if not valid:
            raise DeploymentError(
                f"Cannot plan backup migration with ambiguous recovery metadata: {reason}"
            )
        if journal is None or journal["phase"] in {"committed", "recovered"}:
            active = self._require_active_state(state)
            if active is not None and state is None:
                raise DeploymentError(
                    "Cannot plan backup retention without the active release's installed state"
                )
        _, protected = self._cleanup_protection(state, journal, None, extra)
        protected.update(
            (self.legacy_backups / name, "recorded legacy rollback reference")
            for name in self._backup_references()
        )
        return set(protected)

    def _migration_copy_requirement(self, usage: Usage) -> StorageRequirement:
        return StorageRequirement(
            self.backups,
            usage.independent_copy_bytes(self.filesystem_info(self.backups).allocation_unit),
            "retained legacy snapshot copies",
            usage.inodes,
        )

    def _plan_cross_filesystem_migration(
        self,
        journal: dict,
        source_fd: int,
        dest_fd: int,
        protected_backups: set[Path] | None,
    ) -> None:
        inventory = self._inventory_backups(source_fd, dest_fd, journal["entries"])
        protected = self._migration_protected_backups(protected_backups)
        kept = self._retained_backups(
            [
                (path, info.st_mtime, target or path)
                for path, info, status, _, target, _ in inventory
                if status == "complete"
            ],
            protected,
        )
        statuses = {path: status for path, _, status, _, _, _ in inventory}
        copies = Usage()
        for entry in journal["entries"]:
            name = entry["source"]
            path = self.legacy_backups / name
            if statuses[path] != "complete":
                entry["phase"] = "retained"
            elif path not in kept:
                entry["phase"] = "prune"
            else:
                copies += scan_usage(Path(f"/proc/self/fd/{source_fd}") / name)
        journal["schema"] = 2
        journal["kept_destination"] = []
        journal["prune_destination"] = []
        credit = Usage()
        for path, info, status, _, _, migration in inventory:
            if migration is not None or status != "complete":
                continue
            record = {"name": path.name, "identity": list(file_identity(info))}
            if path in kept:
                journal["kept_destination"].append(record)
            else:
                journal["prune_destination"].append({**record, "phase": "prune"})
                credit += scan_usage(Path(f"/proc/self/fd/{dest_fd}") / path.name)
        requirement = self._migration_copy_requirement(copies)
        # Check the actual destination before trusting reclamation estimates.
        capacity = self.filesystem_info(self.backups)
        try:
            self.check_storage([requirement], phase="cross-filesystem backup migration")
        except StorageError as error:
            if (
                error.__cause__ is not None
                or capacity.available_bytes + credit.reclaimable_bytes < error.required_bytes
                or capacity.inode_reporting
                and capacity.available_inodes + credit.reclaimable_inodes < error.required_inodes
            ):
                raise
        journal["copy_bytes"] = requirement.bytes
        journal["copy_inodes"] = requirement.inodes

    def _prune_migration_backup(
        self,
        journal: dict,
        entry: dict,
        root: RootIdentity,
        directory: int | None,
        protected: set[Path],
        *,
        legacy: bool,
    ) -> None:
        if entry["phase"] in {"pruned", "retained"}:
            return
        name, expected = (
            (entry["source"], entry["source_identity"])
            if legacy
            else (entry["name"], entry["identity"])
        )
        path = root.path / name
        actual = self._entry_identity(directory, name)
        if path in protected or actual not in (None, expected):
            raise DeploymentError(
                f"Snapshot retained because migration protection or identity changed: {path}"
            )
        if entry["phase"] == "prune":
            if actual is None:
                raise DeploymentError(f"Snapshot disappeared before the migration removal: {path}")
            view = Path(f"/proc/self/fd/{directory}") / name
            status, detail = (
                self._inspect_legacy_backup(view, directory)
                if legacy and directory is not None
                else self._backup_status(path)
            )
            if status != "complete":
                entry["phase"] = "retained"
                atomic_json(self.migration_journal, journal)
                print(f"Retained changed snapshot {path}: {detail}", file=sys.stderr)
                return
            entry["phase"] = "pruning"
            atomic_json(self.migration_journal, journal)
        if actual is not None:
            assert directory is not None
            self.fs._remove_entry(
                root,
                directory,
                name,
                path,
                expected_owner=self.privileged_uid,
                identity=(expected[0], expected[1]),
                top=True,
                allow_symlinks_inside=False,
            )
            os.fsync(directory)
        entry["phase"] = "pruned"
        atomic_json(self.migration_journal, journal)
        print(f"Removed verified snapshot beyond retention during backup migration: {path}.")

    def _legacy_cleanup_entry(
        self, path: Path, identity: tuple[int, int] | None, *, dry_run: bool
    ) -> Usage:
        with self._open_legacy_backups() as (root, data_fd, legacy_fd):
            if identity is None or list(identity) != self._entry_identity(legacy_fd, path.name):
                raise DeploymentError(f"Legacy snapshot identity changed: {path}")
            view = Path(f"/proc/self/fd/{legacy_fd}") / path.name
            status, detail = self._inspect_legacy_backup(view, legacy_fd)
            if status not in ("incomplete", "complete"):
                raise DeploymentError(f"Legacy snapshot retained: {path}: {detail}")
            usage = scan_usage(view)
            if not dry_run:
                if status != "incomplete":
                    raise DeploymentError(
                        f"Verified legacy snapshot must be migrated before removal: {path}"
                    )
                self.fs._remove_entry(
                    root,
                    legacy_fd,
                    path.name,
                    path,
                    expected_owner=self.privileged_uid,
                    identity=identity,
                    top=True,
                    allow_symlinks_inside=False,
                )
                os.fsync(legacy_fd)
                if not os.listdir(legacy_fd):
                    self._remove_empty_legacy_root(data_fd, [root.device, root.inode])
            return usage

    def _migrate_legacy_backups(self, protected_backups: set[Path] | None = None) -> None:
        # The application can rename its legacy backup entry, but it cannot
        # change any contents behind an already opened, root-owned directory.
        legacy, current = self.legacy_backups, self.backups
        critical = (self.data, self.base, self.etc, self.updater_root)
        if any(current.is_relative_to(path) or current in path.parents for path in critical):
            raise DeploymentError(
                "The backup directory must be disjoint from the persistent data root"
            )
        journal, valid, reason = self._load_metadata(
            self.migration_journal, "backup migration journal"
        )
        if not valid:
            raise DeploymentError(
                f"Invalid backup migration journal: {reason}. Inspect {self.migration_journal}."
            )
        if journal is None and not os.path.lexists(legacy):
            self._adopt_previously_moved_backups()
            return
        if journal is not None:
            self._validate_backup_migration(journal)
        self.fs.validate_root(
            self.etc, owner=self.privileged_uid, description="configuration directory"
        )
        with ExitStack() as stack:
            source_fd = None
            source_identity = None
            parent_fd = None
            if os.path.lexists(legacy):
                root, parent_fd, source_fd = stack.enter_context(self._open_legacy_backups())
                source_identity = [root.device, root.inode]
            elif journal is None:
                raise DeploymentError(
                    f"The legacy backup location {legacy} disappeared during inspection"
                )

            destination_root = self._backup_root(write=True)
            destination_identity = [destination_root.device, destination_root.inode]
            destination_fd = stack.enter_context(self.fs.open_directory(destination_root))
            if journal is None:
                assert source_fd is not None
                if not os.listdir(source_fd):
                    # Leaving this reserved, empty directory would also be safe,
                    # but older installations expect it to disappear after migration.
                    self._remove_empty_legacy_root(parent_fd, source_identity)
                    self._adopt_previously_moved_backups()
                    return
                journal = {
                    "schema": 1,
                    "operation": "backup-migration",
                    "legacy": str(legacy),
                    "destination": str(current),
                    "source_identity": source_identity,
                    "destination_identity": destination_identity,
                    "entries": self._legacy_migration_entries(source_fd, destination_fd),
                }
                self._backup_references()
                if self._legacy_copy_required(source_fd, destination_fd):
                    self._plan_cross_filesystem_migration(
                        journal, source_fd, destination_fd, protected_backups
                    )
                atomic_json(self.migration_journal, journal)
            elif (
                journal["destination_identity"] != destination_identity
                or source_identity is not None
                and journal["source_identity"] != source_identity
            ):
                raise DeploymentError(
                    f"A managed backup location changed during migration. Inspect {self.migration_journal}."
                )

            if journal["schema"] == 2:
                protected = self._migration_protected_backups(protected_backups)
                print(
                    f"Backup migration plan requires {format_bytes(journal['copy_bytes'])} "
                    f"and {journal['copy_inodes']} inodes for retained copies on {current}, plus headroom."
                )
                for entry in journal["prune_destination"]:
                    self._prune_migration_backup(
                        journal, entry, destination_root, destination_fd, protected, legacy=False
                    )
                if all(
                    entry["phase"] in {"pending", "retained", "prune"}
                    for entry in journal["entries"]
                ):
                    requirement = self._migration_copy_requirement(
                        Usage(bytes=journal["copy_bytes"], inodes=journal["copy_inodes"])
                    )
                    self.check_storage([requirement], phase="cross-filesystem backup migration")
            for entry in journal["entries"]:
                if entry["phase"] not in {"prune", "pruning", "pruned"}:
                    self._migrate_backup_entry(journal, entry, source_fd, destination_fd)
            if journal["schema"] == 2:
                kept = [(entry["name"], entry["identity"]) for entry in journal["kept_destination"]]
                kept.extend(
                    (entry["target"], entry["target_identity"])
                    for entry in journal["entries"]
                    if entry["phase"] == "complete"
                )
                for name, identity in kept:
                    if (
                        self._entry_identity(destination_fd, name) != identity
                        or self._backup_status(current / name)[0] != "complete"
                    ):
                        raise DeploymentError(
                            f"A retained backup failed verification after migration: {current / name}"
                        )
                protected = self._migration_protected_backups(protected_backups)
                device, inode = journal["source_identity"]
                legacy_root = RootIdentity(legacy, device, inode, "legacy backup directory")
                for entry in journal["entries"]:
                    if entry["phase"] in {"prune", "pruning", "pruned"}:
                        self._prune_migration_backup(
                            journal, entry, legacy_root, source_fd, protected, legacy=True
                        )
            if source_fd is not None and not os.listdir(source_fd):
                self._remove_empty_legacy_root(parent_fd, source_identity)
            pending = self._backup_references()
            # Keep unverified artifacts and their references as evidence. If an
            # old reference has no remaining source or verified destination, the
            # journal must survive for reviewed recovery.
            retained = {
                entry["source"]
                for entry in journal["entries"]
                if entry["phase"] == "retained"
                and self._entry_identity(source_fd, entry["source"]) == entry["source_identity"]
            }
            if set(pending) - retained:
                raise DeploymentError(
                    f"Some recorded legacy snapshots are still missing. Inspect {self.migration_journal} and {legacy}."
                )
            self.migration_journal.unlink()
            sync_directory(self.etc)
            if source_fd is not None and os.listdir(source_fd):
                print(
                    f"Unverified legacy artifacts were retained for inspection in {legacy}.",
                    file=sys.stderr,
                )

    def _remove_empty_legacy_root(self, data_fd: int | None, identity) -> None:
        assert data_fd is not None
        info = os.stat("backups", dir_fd=data_fd, follow_symlinks=False)
        if list(file_identity(info)) != identity:
            raise DeploymentError(
                f"The legacy backup directory changed before finalization: {self.legacy_backups}"
            )
        os.rmdir("backups", dir_fd=data_fd)
        os.fsync(data_fd)

    def _legacy_migration_entries(self, source_fd: int, destination_fd: int | None) -> list[dict]:
        entries = []
        names = set(os.listdir(destination_fd)) if destination_fd is not None else set()
        for name in sorted(os.listdir(source_fd)):
            if BACKUP_DIRECTORY.fullmatch(name) is None:
                continue
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            staging = ".staging-backup-" + secrets.token_hex(8)
            target = name
            attempt = 0
            while target in names:
                suffix = hashlib.sha256(
                    f"{name}:{info.st_dev}:{info.st_ino}:{attempt}".encode()
                ).hexdigest()[:16]
                target = name.split("-")[0] + "-" + suffix
                attempt += 1
            if staging in names:
                raise DeploymentError(
                    "A reserved backup migration destination already exists. Retry migration."
                )
            names.update((target, staging))
            entries.append(
                {
                    "source": name,
                    "target": target,
                    "staging": staging,
                    "phase": "pending",
                    "source_identity": list(file_identity(info)),
                    "target_identity": None,
                }
            )
        return entries

    def _validate_backup_migration(self, journal: dict) -> None:
        def invalid():
            return DeploymentError(
                f"Invalid backup migration metadata. Inspect {self.migration_journal}."
            )

        if (
            journal.get("schema") not in (1, 2)
            or journal.get("operation") != "backup-migration"
            or journal.get("legacy") != str(self.legacy_backups)
            or journal.get("destination") != str(self.backups)
            or not valid_identity(journal.get("source_identity"))
            or not valid_identity(journal.get("destination_identity"))
            or not isinstance(journal.get("entries"), list)
        ):
            raise invalid()
        names: dict[str, set[str]] = {key: set() for key in ("source", "target", "staging")}
        for entry in journal["entries"]:
            if not isinstance(entry, dict):
                raise invalid()
            phase = entry.get("phase")
            allowed = {"pending", "copying", "copied", "published", "retained", "complete"}
            if journal["schema"] == 2:
                allowed.update(("prune", "pruning", "pruned"))
            if (
                not isinstance(phase, str)
                or phase not in allowed
                or not valid_identity(entry.get("source_identity"))
                or phase in ("copied", "published", "complete")
                and not valid_identity(entry.get("target_identity"))
                or phase in ("pending", "copying", "retained", "prune", "pruning", "pruned")
                and entry.get("target_identity") is not None
            ):
                raise invalid()
            for key, pattern in (
                ("source", BACKUP_DIRECTORY),
                ("target", BACKUP_DIRECTORY),
                ("staging", STAGING_DIRECTORY),
            ):
                name = entry.get(key)
                if (
                    not isinstance(name, str)
                    or pattern.fullmatch(name) is None
                    or name in names[key]
                ):
                    raise invalid()
                names[key].add(name)
        if journal["schema"] == 2:
            for key in ("copy_bytes", "copy_inodes"):
                if type(journal.get(key)) is not int or journal[key] < 0:
                    raise invalid()
            occupied = names["target"]
            for key in ("kept_destination", "prune_destination"):
                group = journal.get(key)
                if not isinstance(group, list):
                    raise invalid()
                for item in group:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("name"), str)
                        or not BACKUP_DIRECTORY.fullmatch(item["name"])
                        or item["name"] in occupied
                        or not valid_identity(item.get("identity"))
                        or key == "prune_destination"
                        and item.get("phase") not in ("prune", "pruning", "pruned", "retained")
                    ):
                        raise invalid()
                    occupied.add(item["name"])

    @staticmethod
    def _entry_identity(directory: int | None, name: str) -> list[int] | None:
        if directory is None:
            return None
        try:
            return list(file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)))
        except FileNotFoundError:
            return None

    def _migrate_backup_entry(
        self, journal: dict, entry: dict, source_fd: int | None, destination_fd: int
    ) -> None:
        def mark(phase: str) -> None:
            entry["phase"] = phase
            atomic_json(self.migration_journal, journal)

        name, target = entry["source"], entry["target"]
        source_identity = self._entry_identity(source_fd, name)
        target_identity = self._entry_identity(destination_fd, target)
        destination = self.backups / target
        if entry["phase"] == "retained":
            return
        if source_identity not in (None, entry["source_identity"]):
            raise DeploymentError(f"The legacy snapshot {name} changed during migration")
        if entry["phase"] == "complete":
            if target_identity != entry["target_identity"]:
                raise DeploymentError(
                    f"The migrated snapshot {destination} changed before finalization"
                )
            return
        same_inode = False
        if entry["phase"] == "pending":
            if target_identity is not None:
                if source_identity is not None or target_identity != entry["source_identity"]:
                    raise DeploymentError(
                        f"Ambiguous legacy snapshot migration destination: {destination}"
                    )
                entry["target_identity"] = target_identity
                mark("published")
            elif source_fd is not None and source_identity is not None:
                source = Path(f"/proc/self/fd/{source_fd}") / name
                if self._inspect_legacy_backup(source, source_fd)[0] != "complete":
                    if journal["schema"] == 2:
                        raise DeploymentError(
                            f"Planned legacy backup failed verification: {source}"
                        )
                    mark("retained")
                    return
                try:
                    os.rename(name, target, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
                except OSError as error:
                    if error.errno != errno.EXDEV:
                        raise
                    mark("copying")
                else:
                    os.fsync(destination_fd)
                    os.fsync(source_fd)
                    entry["target_identity"] = source_identity
                    mark("published")
                    same_inode = True
                    source_identity = None
            else:
                raise DeploymentError(
                    f"The legacy snapshot {name} is missing from both backup locations"
                )
        if entry["phase"] == "copying":
            if source_fd is None or source_identity is None or target_identity is not None:
                raise DeploymentError(f"Cannot resume the cross-filesystem copy of {name}")
            source = Path(f"/proc/self/fd/{source_fd}") / name
            staging = self.backups / entry["staging"]
            if os.path.lexists(staging):
                self._safe_remove(self.backups, staging, identity=self.fs.identity_of(staging))
            self._copy_verified_snapshot(source, staging, parent_fd=source_fd)
            entry["target_identity"] = self._entry_identity(destination_fd, entry["staging"])
            mark("copied")
        if entry["phase"] == "copied":
            if target_identity is None:
                if (
                    self._entry_identity(destination_fd, entry["staging"])
                    != entry["target_identity"]
                ):
                    raise DeploymentError(
                        f"The cross-filesystem copy of {name} changed before publication"
                    )
                os.rename(
                    entry["staging"], target, src_dir_fd=destination_fd, dst_dir_fd=destination_fd
                )
                os.fsync(destination_fd)
            elif (
                target_identity != entry["target_identity"]
                or self._entry_identity(destination_fd, entry["staging"]) is not None
            ):
                raise DeploymentError(
                    f"Ambiguous copied backup migration destination: {destination}"
                )
            mark("published")
        if self._entry_identity(destination_fd, target) != entry["target_identity"]:
            raise DeploymentError(f"The migrated snapshot {destination} changed identity")
        if same_inode:
            self.verification.put(destination, "complete")
        elif self._backup_status(destination)[0] != "complete":
            raise DeploymentError(f"The migrated snapshot {destination} failed verification")
        # Both references can be rewritten repeatedly while this journal protects
        # the verified destination. Only then can a leftover source be removed.
        self._backup_references({name: target})
        if source_fd is not None and source_identity is not None:
            device, inode = journal["source_identity"]
            source_root = RootIdentity(
                self.legacy_backups, device, inode, "legacy backup directory"
            )
            self.fs._remove_entry(
                source_root,
                source_fd,
                name,
                self.legacy_backups / name,
                identity=(source_identity[0], source_identity[1]),
                expected_owner=self.privileged_uid,
                top=True,
                allow_symlinks_inside=False,
            )
            os.fsync(source_fd)
        mark("complete")
        action = "Moved" if entry["source_identity"] == entry["target_identity"] else "Copied"
        print(f"{action} verified legacy snapshot {self.legacy_backups / name} to {destination}.")

    def _copy_verified_snapshot(self, source: Path, staging: Path, *, parent_fd: int) -> None:
        if self._backup_status(source, parent_fd=parent_fd)[0] != "complete":
            raise DeploymentError(f"The legacy snapshot {source.name} failed verification")
        usage = scan_usage(source)
        self.check_storage(
            [self._migration_copy_requirement(usage)],
            phase="cross-filesystem backup migration",
        )
        shutil.copytree(source, staging, symlinks=True)
        sync_tree(staging)
        self.verify_snapshot(staging)
        sync_directory(staging.parent)

    def _backup_references(
        self, replacements: dict[str, str] | None = None, *, require_valid: bool = True
    ) -> dict[str, set[str]]:
        references: dict[str, set[str]] = {}

        def visit(value: dict) -> bool:
            changed = False
            backup = value.get("backup")
            if isinstance(backup, str) and Path(backup).parent == self.legacy_backups:
                name = Path(backup).name
                if replacements and name in replacements:
                    value["backup"] = str(self.backups / replacements[name])
                    changed = True
                else:
                    previous = value.get("previous")
                    if isinstance(previous, str):
                        references.setdefault(name, set()).add(previous)
                    else:
                        references.setdefault(name, set())
            for key in ("prior_state", "restore", "rollback_state", "commit_state"):
                nested = value.get(key)
                if isinstance(nested, dict) and visit(nested):
                    changed = True
            return changed

        for path, validator in (
            (self.state, self.validate_state_metadata),
            (self.journal, self.validate_transaction_metadata),
        ):
            value, valid, reason = self._load_metadata(path, path.name)
            if valid and value is not None:
                valid, reason = validator(value)
            if not valid:
                if require_valid:
                    raise DeploymentError(f"Cannot migrate backup references in {path}: {reason}")
                continue
            if value is not None and visit(value):
                atomic_json(path, value)
        return references

    def _adopt_previously_moved_backups(self) -> None:
        references = self._backup_references(require_valid=False)
        if not references:
            return
        # Updaters predating the migration journal could rename the entire
        # legacy directory before updating its references. Require corroborating
        # release identity from the verified snapshot's copied installed state.
        self._backup_root()
        matches = {}
        for name, releases in references.items():
            artifact = self.backups / name
            if self._backup_status(artifact)[0] != "complete":
                raise DeploymentError(
                    f"The recorded legacy snapshot {name} was not found or did not verify at {artifact}"
                )
            state, valid, _ = self._load_metadata(
                artifact / "config/installed.json", "snapshot installed state"
            )
            if (
                not valid
                or state is None
                or len(releases) != 1
                or state.get("target") is not None
                and state["target"] not in releases
                or state.get("target") is None
                and state.get("version") != self.release_version(Path(next(iter(releases))))
            ):
                raise DeploymentError(
                    f"Cannot prove that {artifact} is the recorded legacy snapshot. Preserve both backup locations for review."
                )
            matches[name] = name
        self._backup_references(matches)

    def _release_reference(self, value) -> bool:
        if not isinstance(value, str):
            return False
        path = Path(value)
        return (
            path.is_absolute()
            and path.parent == self.releases
            and RELEASE_DIRECTORY.fullmatch(path.name) is not None
        )

    def _backup_reference(self, value) -> bool:
        if not isinstance(value, str):
            return False
        path = Path(value)
        return (
            path.is_absolute()
            and path.parent in (self.backups, self.legacy_backups)
            and BACKUP_DIRECTORY.fullmatch(path.name) is not None
        )

    def validate_state_metadata(self, state, *, depth: int = 0) -> tuple[bool, str]:
        if depth > 4 or not isinstance(state, dict):
            return False, "installed state is not a supported JSON object"
        version = state.get("version")
        if not isinstance(version, str) or VERSION.fullmatch(version) is None:
            return False, "installed state has no valid version"
        if "sha256" in state and (
            not isinstance(state["sha256"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", state["sha256"])
        ):
            return False, "installed state has an invalid release archive digest"
        target = state.get("target")
        if target is None:
            # Older rollback installations recorded only release metadata and
            # cleared previous/backup; version-only state also predates phases.
            if any(
                state.get(key) is not None
                for key in ("previous", "backup", "phase", "backup_association")
            ):
                return False, "legacy installed state has unexpected deployment references"
        elif not self._release_reference(target) or Path(target).name.partition("-")[0] != version:
            return False, "installed state target is invalid or disagrees with the version"
        if state.get("phase") not in (None, "committed"):
            return False, "installed state has an unsupported phase"
        if state.get("previous") is not None and not self._release_reference(state["previous"]):
            return (
                False,
                "installed state previous release is outside the managed release directory",
            )
        if state.get("backup") is not None and not self._backup_reference(state["backup"]):
            return False, "installed state backup is outside the managed backup root"
        abi = state.get("updater_abi")
        if abi is not None and (type(abi) is not int or not 1 <= abi <= UPDATER_ABI):
            return False, "installed state requires an unsupported updater ABI"
        if state.get("backup_association") is not None:
            association = state["backup_association"]
            if not state.get("backup") or not isinstance(association, dict):
                return False, "installed state has an invalid backup association"
            try:
                self._validate_snapshot_association(association)
            except DeploymentError as error:
                return False, str(error)
            expected_release = state.get("previous") or state.get("target")
            expected_purpose = "pre-deployment" if state.get("previous") else "initial-install"
            if (
                association["release"] != expected_release
                or association["purpose"] != expected_purpose
            ):
                return False, "installed state backup does not correspond to the recorded release"
        elif abi is not None and abi >= MIN_GENERATION_ABI and state.get("backup"):
            return False, "installed state is missing the current snapshot identity"
        if state.get("prior_state") is not None:
            valid, reason = self.validate_state_metadata(state["prior_state"], depth=depth + 1)
            if not valid:
                return False, "nested prior state is invalid: " + reason
        return True, ""

    def validate_transaction_metadata(self, journal) -> tuple[bool, str]:
        if not isinstance(journal, dict):
            return False, "transaction journal is not a JSON object"
        phase, operation, abi = (journal.get(key) for key in ("phase", "operation", "abi"))
        if not isinstance(phase, str) or phase not in JOURNAL_PHASES:
            return False, "transaction journal phase is unsupported"
        if abi is not None and (type(abi) is not int or not 1 <= abi <= UPDATER_ABI):
            return False, "transaction journal requires an unsupported updater ABI"
        if journal.get("backup") is not None and not self._backup_reference(journal["backup"]):
            return False, "transaction backup is outside the managed backup root"
        if operation is not None:
            identifier = journal.get("transaction")
            if (
                abi is None
                or not MIN_GENERATION_ABI <= abi <= UPDATER_ABI
                or operation
                not in (
                    "install",
                    "update",
                    "rollback",
                    "automatic-recovery",
                    "manual-backup",
                )
            ):
                return False, "unknown transaction operation or updater ABI"
            if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
                return False, "transaction has no valid identifier"
            allowed_phases = {
                "install": {
                    "prepared",
                    "snapshot",
                    "migrating",
                    "activating",
                    "activated",
                    "committing",
                    "committed",
                },
                "update": {
                    "prepared",
                    "snapshot",
                    "migrating",
                    "activating",
                    "activated",
                    "committing",
                    "committed",
                },
                "rollback": {"recovering", "committing", "recovered"},
                "automatic-recovery": {"recovering", "committing", "recovered"},
                "manual-backup": {"prepared", "snapshot", "recovering", "committed", "recovered"},
            }
            if phase not in allowed_phases[operation]:
                return False, "transaction phase does not match its operation"
            if journal.get("service_target") is not None and (
                operation not in {"rollback", "automatic-recovery"}
                or journal["service_target"] != journal.get("previous")
            ):
                return False, "recovery service target disagrees with the recorded previous release"
            if operation == "manual-backup":
                if (
                    phase not in {"prepared", "snapshot", "recovering", "committed", "recovered"}
                    or not isinstance(journal.get("service_was_running"), bool)
                    or journal.get("restore") is not None
                    or phase == "committed"
                    and not journal.get("backup")
                ):
                    return False, "manual backup service state or phase is invalid"
            elif not self._release_reference(journal.get("target")) or "previous" not in journal:
                return False, "transaction is missing its target or previous release"
            elif any(
                type(journal.get(key)) is not bool
                for key in ("migration_started", "service_stopped", "data_modified")
            ):
                return False, "transaction is missing service or migration flags"
            if operation in ("install", "update", "manual-backup"):
                name, identifier = (journal.get(key) for key in ("snapshot_name", "snapshot_id"))
                if (
                    not isinstance(name, str)
                    or BACKUP_DIRECTORY.fullmatch(name) is None
                    or not isinstance(identifier, str)
                    or re.fullmatch(r"[a-f0-9]{32}", identifier) is None
                ):
                    return False, "transaction has no valid planned snapshot identity"
            has_snapshot_plan = (
                operation in ("install", "update", "manual-backup")
                or operation == "automatic-recovery"
                and "snapshot_id" in journal
            )
            if has_snapshot_plan and journal.get("backup"):
                association = journal.get("backup_association")
                if not isinstance(association, dict):
                    return False, "transaction is missing its snapshot association"
                try:
                    self._validate_snapshot_association(association)
                except DeploymentError as error:
                    return False, str(error)
                purpose = {
                    "manual-backup": "manual",
                    "install": "initial-install",
                    "update": "pre-deployment",
                    "automatic-recovery": "pre-deployment"
                    if journal.get("previous")
                    else "initial-install",
                }[operation]
                if (
                    association["transaction"] != journal["transaction"]
                    or association["snapshot"] != journal.get("snapshot_id")
                    or association["purpose"] != purpose
                ):
                    return False, "transaction snapshot identity does not match"
            if operation == "rollback" and (
                not journal.get("previous") or not journal.get("backup")
            ):
                return False, "rollback transaction is missing its recovery target or snapshot"
            if (
                operation == "install"
                and journal.get("previous")
                or operation == "update"
                and not journal.get("previous")
            ):
                return False, "transaction operation disagrees with its previous release"
            if phase == "committing":
                if "commit_state" not in journal:
                    return False, "committing transaction has no installed-state intent"
                if journal["commit_state"] is not None:
                    valid, reason = self.validate_state_metadata(journal["commit_state"])
                    if not valid:
                        return False, "invalid installed-state intent: " + reason
        elif (abi is not None and abi >= MIN_GENERATION_ABI) or phase == "committing":
            return False, "current transaction is missing its operation"
        elif phase == "recovered" and set(journal) == {"phase"}:
            return True, ""  # Explicit terminal format written by legacy --rollback.
        elif not self._release_reference(journal.get("target")):
            return False, "legacy transaction has no valid target release"
        if journal.get("previous") is not None and not self._release_reference(journal["previous"]):
            return False, "transaction previous release is invalid"
        if phase in {"migrating", "activating", "activated"} or (
            phase in {"committing", "committed"} and operation in {"update", "install"}
        ):
            if not journal.get("backup"):
                return False, "transaction phase requires a recorded backup"
            if operation is not None and not journal.get("migration_started"):
                return False, "transaction phase requires durable migration intent"
            if operation is not None and phase != "migrating" and not journal.get("data_modified"):
                return False, "activation phase requires durable completion of migration"
        for flag in ("service_stopped", "migration_started", "data_modified"):
            if flag in journal and not isinstance(journal[flag], bool):
                return False, f"transaction {flag} is not a boolean"
        identities = ("previous_data_identity", "previous_release_identity")
        if any(key in journal for key in identities) and (
            operation not in {"update", "automatic-recovery"}
            or not all(valid_identity(journal.get(key)) for key in identities)
        ):
            return False, "transaction has invalid previous release or data identities"
        for key in ("prior_state", "rollback_state"):
            if journal.get(key) is not None:
                valid, reason = self.validate_state_metadata(journal[key])
                if not valid:
                    return False, f"transaction {key} is invalid: {reason}"
        restore = journal.get("restore")
        if restore is not None:
            if operation is not None and (
                operation not in {"rollback", "automatic-recovery"}
                or not journal.get("migration_started")
            ):
                return (
                    False,
                    "restore metadata is invalid for the recorded operation or migration phase",
                )
            valid, reason = self._validate_restore_metadata(restore)
            if not valid:
                return False, "transaction restore metadata is invalid: " + reason
            if restore["backup"] != journal.get("backup"):
                return False, "restore snapshot disagrees with the transaction snapshot"
        return True, ""

    def _validate_restore_metadata(self, restore) -> tuple[bool, str]:
        if not isinstance(restore, dict):
            return False, "not an object"
        phase, identifier = (restore.get(key) for key in ("phase", "transaction"))
        if not isinstance(phase, str) or phase not in RESTORE_PHASES:
            return False, "unsupported restore phase"
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{8,32}", identifier) is None:
            return False, "restore transaction identifier is invalid"
        if restore.get("data") != str(self.data) or not self._backup_reference(
            restore.get("backup")
        ):
            return False, "restore data or snapshot path does not match the configured layout"
        schema = restore.get("schema")
        if schema not in (None, 2):
            return False, "unsupported restore metadata schema"
        for field_name, prefix in (("staging", ".restore-"), ("retained", ".retained-")):
            value = restore.get(field_name)
            if schema is None and phase == "prepared" and value is None:
                continue
            if value != str(self.data.with_name(prefix + identifier)):
                return False, f"restore {field_name} does not match its transaction identifier"
        if schema == 2 and (
            not valid_identity(restore.get("gate_identity"))
            or not valid_identity(restore.get("original_identity"))
            or phase != "prepared"
            and not valid_identity(restore.get("staging_identity"))
            or phase == "prepared"
            and restore.get("staging_identity") is not None
        ):
            return False, "restore phase has missing or invalid filesystem identities"
        return True, ""

    def _data_account(self) -> tuple[int, int]:
        if (self.base, self.data) == (Path("/opt/hnefatafl"), Path("/var/lib/hnefatafl")):
            try:
                account = pwd.getpwnam("hnefatafl")
            except KeyError as error:
                raise DeploymentError(
                    "The deployment service account hnefatafl is missing"
                ) from error
            return account.pw_uid, account.pw_gid
        return self.privileged_uid, os.getegid()

    def _application_queries(
        self, statements: list[tuple[str, tuple]], *, write: bool = False
    ) -> list[list]:
        database = self.data / "hnefatafl.sqlite3"
        uid, gid = self._data_account()
        uri = database.as_uri() + ("?mode=rw" if write else "?mode=ro")
        if uid == self.privileged_uid:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                result = [connection.execute(sql, args).fetchall() for sql, args in statements]
                if write:
                    connection.commit()
                return result
        # SQLite can open journal and shared-memory files next to its database.
        # Every operation against live paths executes as the service account.
        helper = """
import json, sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect(sys.argv[1], uri=True)) as connection:
    rows = [connection.execute(sql, args).fetchall() for sql, args in json.load(sys.stdin)]
    if sys.argv[2] == 'write':
        connection.commit()
json.dump(rows, sys.stdout)
"""
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", helper, uri, "write" if write else "read"],
                input=json.dumps(statements),
                text=True,
                capture_output=True,
                check=True,
                timeout=15,
                env=self.env,
                user=uid,
                group=gid,
                extra_groups=[],
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DeploymentError(
                "The service account could not read or update its live SQLite database"
            ) from error
        return json.loads(completed.stdout)

    def _active_jobs(self) -> bool:
        if not os.path.lexists(self.data / "hnefatafl.sqlite3"):
            return False
        tables = self._application_queries(
            [
                ("SELECT name FROM sqlite_master WHERE name IN ('jobs', 'tournaments')", ()),
            ]
        )[0]
        return any(
            self._application_queries(
                [
                    (
                        f"SELECT 1 FROM {table} WHERE json_extract(data, '$.status') IN ('running', 'cancelling', 'queued', 'starting') LIMIT 1",
                        (),
                    ),
                ]
            )[0]
            for (table,) in tables
            if table in {"jobs", "tournaments"}
        )

    def _data_directory(self, path: Path, *, staging: bool = False) -> RootIdentity:
        owner, _ = self._data_account()
        if staging:
            info = path.lstat()
            if info.st_uid == self.privileged_uid:
                owner = self.privileged_uid
        return self.fs.validate_root(
            path, owner=owner, description="persistent data directory", allow_nested_mounts=True
        )

    def _gate_directory(self) -> tuple[RootIdentity, bool]:
        journal, readable, reason = self._load_metadata(self.journal, "transaction journal")
        if not readable:
            raise DeploymentError(
                f"Cannot acquire the deployment gate: {reason}. Inspect {self.journal}."
            )
        restore = journal.get("restore") if journal else None
        if restore is None:
            return self._data_directory(self.data), True
        valid, reason = self.validate_transaction_metadata(journal)
        if not valid:
            raise DeploymentError(
                f"Cannot acquire the deployment gate from recovery metadata: {reason}"
            )
        candidates = []
        for candidate in (self.data, restore.get("retained"), restore.get("staging")):
            if not candidate:
                continue
            directory = Path(candidate)
            if not os.path.lexists(directory):
                continue
            root = self._data_directory(directory, staging=directory != self.data or bool(restore))
            if os.path.lexists(directory / GATE_NAME):
                candidates.append(root)
        if len(candidates) == 1:
            return candidates[0], False
        if (
            not candidates
            and restore.get("schema") != 2
            and restore.get("phase") == "prepared"
            and self.data.is_dir()
        ):
            return self._data_directory(self.data), True
        # A committed restore can be followed by normal gate creation only once
        # the journal's authoritative data root has been confirmed.
        raise DeploymentError(
            f"Cannot locate a unique deployment gate for the interrupted restore. Inspect {self.journal}."
        )

    def _recorded_gate_identity(self):
        journal, valid, _ = self._load_metadata(self.journal, "transaction journal")
        restore = journal.get("restore") if valid and journal else None
        return restore.get("gate_identity") if isinstance(restore, dict) else None

    def _check_gate(self, directory: int, descriptor: int, identity=None) -> tuple[int, int]:
        info = os.fstat(descriptor)
        current = os.stat(GATE_NAME, dir_fd=directory, follow_symlinks=False)
        found = (info.st_dev, info.st_ino)
        application_uid, _ = self._data_account()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in {self.privileged_uid, application_uid}
            or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}
            or info.st_uid != application_uid
            and not info.st_mode & stat.S_IROTH
            or found != (current.st_dev, current.st_ino)
            or identity is not None
            and tuple(identity) != found
        ):
            raise DeploymentError("Unsafe or replaced deployment gate. Inspect the data directory.")
        return found

    @contextmanager
    def write_gate(self):
        # The application takes a shared flock on this same inode for writes.
        root, can_create = self._gate_directory()
        gate = root.path / GATE_NAME
        if self.fs.is_mount(gate):
            raise DeploymentError(f"The deployment gate is a mount point: {gate}")
        try:
            with self.fs.open_directory(root) as directory:
                created = False
                try:
                    pinned = os.open(
                        GATE_NAME, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
                    )
                except FileNotFoundError:
                    if not can_create:
                        raise
                    mask = os.umask(0o022)
                    try:
                        pinned = os.open(
                            GATE_NAME,
                            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                            0o644,
                            dir_fd=directory,
                        )
                    finally:
                        os.umask(mask)
                    created = True
                try:
                    identity = self._check_gate(directory, pinned, self._recorded_gate_identity())
                    # Reopen the already verified regular inode, never the
                    # application-writable name. O_PATH itself cannot be flocked.
                    descriptor = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_CLOEXEC)
                    try:
                        self._check_gate(directory, descriptor, identity)
                        if created:
                            os.fsync(descriptor)
                            os.fsync(directory)
                        deadline = time.monotonic() + 60
                        while True:
                            try:
                                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                break
                            except BlockingIOError:
                                if time.monotonic() >= deadline:
                                    raise DeploymentError(
                                        "HTTP mutations are still running. Retry after they finish."
                                    ) from None
                                time.sleep(0.25)
                        self._check_gate(directory, descriptor, identity)
                        self._held_gate = identity
                        try:
                            yield
                        finally:
                            self._held_gate = None
                    finally:
                        os.close(descriptor)
                finally:
                    os.close(pinned)
        except OSError as error:
            if not getattr(self, "_held_gate", None):
                # Operations inside the scope retain their original exception.
                if error.filename == GATE_NAME:
                    raise DeploymentError(
                        f"Cannot safely open the deployment gate at {gate}"
                    ) from error
            raise

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
        Filesystems that report no inode table (f_files == 0) are marked as such
        instead of pretending an inode budget exists.
        """
        anchor = self._existing_anchor(path)
        info = os.stat(anchor)
        vfs = os.statvfs(anchor)
        total_inodes = int(getattr(vfs, "f_files", 0))
        available_inodes = int(getattr(vfs, "f_favail", 0))
        reporting = total_inodes > 0
        return FilesystemInfo(
            info.st_dev,
            anchor,
            int(vfs.f_bavail) * int(vfs.f_frsize),
            available_inodes if reporting else 0,
            reporting,
            max(TREE_OVERHEAD_PER_DIRECTORY, int(vfs.f_bsize), int(vfs.f_frsize)),
        )

    def _free_space_snapshot(self, paths: list[Path]) -> dict[str, tuple[int, int]]:
        result = {}
        for path in paths:
            try:
                info = self.filesystem_info(path)
            except (OSError, DeploymentError):
                continue
            result[str(info.anchor)] = (info.available_bytes, info.available_inodes)
        return result

    @staticmethod
    def _free_space_delta(
        before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
    ) -> int:
        reclaimed = 0
        for key, (available, _inodes) in after.items():
            previous = before.get(key)
            if previous is not None:
                reclaimed += max(0, available - previous[0])
        return reclaimed

    def _usage(self, path: Path) -> Usage:
        key = Path(path)
        try:
            signature = VerificationCache.signature(key)
        except OSError:
            return scan_usage(key)
        cached = self._release_size_cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        usage = scan_usage(key)
        self._release_size_cache[key] = (signature, usage)
        return usage

    def _tree_stats(self, path: Path, skip_top=frozenset()) -> tuple[int, int]:
        if skip_top:
            usage = scan_usage(path, skip_top)
        else:
            usage = self._usage(path)
        return usage.bytes, usage.inodes

    def _release_stats(self, path: Path) -> Usage:
        return self._usage(path)

    def _snapshot_excluded(self) -> set[str]:
        return {
            "backups",
            "cache",
            GATE_NAME,
            RESTORE_MARKER,
            "hnefatafl.sqlite3",
            "hnefatafl.sqlite3-wal",
            "hnefatafl.sqlite3-shm",
            "hnefatafl.sqlite3-journal",
        }

    def _database_size(self, root: Path | None = None, *, allocation_unit: int) -> tuple[int, int]:
        """Conservatively account for SQLite and every live sidecar without opening them."""
        size, files = 0, 0
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = (root or self.data) / ("hnefatafl.sqlite3" + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise DeploymentError(f"Unexpected SQLite path: {path}")
            files += 1
            rounded = (info.st_size + allocation_unit - 1) // allocation_unit * allocation_unit
            size += max(rounded, info.st_blocks * 512) + TREE_OVERHEAD_PER_FILE
        return size, files

    def snapshot_requirement(
        self,
        *,
        data_usage: Usage | None = None,
        database: tuple[int, int] | None = None,
        include_conversion: bool = True,
    ) -> StorageRequirement:
        """Estimate a snapshot using exactly the snapshot inclusion rules."""
        excluded = self._snapshot_excluded()
        data_usage = data_usage if data_usage is not None else scan_usage(self.data, excluded)
        config_usage = scan_usage(self.etc, {JOURNAL_RESERVE})
        unit = self.filesystem_info(self.backups).allocation_unit
        database_bytes, database_inodes = database or self._database_size(allocation_unit=unit)
        manifest_bytes = max(MIB, (data_usage.inodes + config_usage.inodes + 32) * 512)
        usage = data_usage + config_usage
        total = (
            usage.independent_copy_bytes(unit)
            + (2 if include_conversion else 1) * database_bytes
            + manifest_bytes
        )
        margin = int(total * self.storage_settings["snapshot_margin_percent"] / 100)
        return StorageRequirement(
            self.backups,
            total + margin,
            "pre-deployment snapshot (data, configuration and SQLite copy) on the "
            "backup filesystem",
            usage.inodes + database_inodes + 32,
        )

    def _release_history_estimate(self) -> tuple[int, int]:
        peak = 0
        inodes = 0
        for entry in self._release_entries():
            if entry["kind"] != "complete":
                continue
            measured = self._release_stats(entry["path"])
            metadata = entry.get("metadata") or {}
            historical = 0
            historical_inodes = 0
            for key in ("peak_bytes", "size_bytes"):
                value = metadata.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > historical:
                    historical = value
            for key in ("peak_inodes", "size_inodes", "file_count"):
                value = metadata.get(key)
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value > historical_inodes
                ):
                    historical_inodes = value
            peak = max(peak, measured.bytes, historical)
            inodes = max(inodes, measured.inodes, historical_inodes)
        floor = int(self.storage_settings["release_estimate_mib"]) * MIB
        floor_inodes = 30000
        if peak <= 0:
            return floor, floor_inodes
        return max(peak, floor), max(inodes, 3000)

    def _prepare_requirements(self, archive_size: int) -> list[StorageRequirement]:
        release_bytes, release_inodes = self._release_history_estimate()
        archive_bytes = archive_size or 64 * MIB
        cache_bytes = int(self.storage_settings["cache_headroom_mib"]) * MIB
        # The cold uv/npm caches can hold extracted dependencies concurrently
        # with the copies in the release. Reuse observed workspace sizes when
        # a previous preparation has recorded them.
        temporary_bytes = max(128 * MIB, release_bytes // 2) + cache_bytes
        temporary_inodes = max(8000, release_inodes)
        for entry in self._release_entries():
            metadata = entry.get("metadata") or {}
            peak_bytes = metadata.get("temporary_peak_bytes")
            peak_inodes = metadata.get("temporary_peak_inodes")
            if type(peak_bytes) is int and peak_bytes > 0:
                temporary_bytes = max(temporary_bytes, peak_bytes * 115 // 100)
            if type(peak_inodes) is int and peak_inodes > 0:
                temporary_inodes = max(temporary_inodes, peak_inodes * 115 // 100)
        return [
            StorageRequirement(
                self.releases,
                int(release_bytes * 1.15),
                "release extraction, frozen environment and frontend build",
                release_inodes,
            ),
            StorageRequirement(
                self.temporary_root, archive_bytes, "compressed release download", 8, "download"
            ),
            StorageRequirement(
                self.temporary_root,
                temporary_bytes,
                "temporary builds, dependency caches and isolated verification data",
                temporary_inodes,
                "build",
            ),
            StorageRequirement(self.etc, 16 * MIB, "deployment metadata", 64),
            *(self.updater_requirement() if self.manage_updater else []),
        ]

    def _deployment_requirements(self) -> list[StorageRequirement]:
        usage = scan_usage(self.data, self._snapshot_excluded())
        backup_database = self._database_size(
            allocation_unit=self.filesystem_info(self.backups).allocation_unit
        )
        recovery_unit = self.filesystem_info(self.data.parent).allocation_unit
        database_bytes, database_inodes = self._database_size(allocation_unit=recovery_unit)
        persisted = self.snapshot_requirement(
            data_usage=usage,
            database=backup_database,
            include_conversion=False,
        )
        margin = self.storage_settings["snapshot_margin_percent"]
        # The original live tree already occupies disk space. Migration growth
        # remains in that tree when it is retained, while restore adds its own
        # candidate and a private copy of the possibly grown live database.
        migration = max(64 * MIB, database_bytes)
        recovery = self._restore_allocation(
            usage.independent_copy_bytes(recovery_unit) + database_bytes,
            usage.inodes + database_inodes,
            database_bytes,
            database_bytes + migration,
            stage="recovery",
        )
        return [
            persisted,
            StorageRequirement(
                self.backups,
                backup_database[0] * (100 + margin) // 100,
                "temporary second SQLite copy during snapshot conversion",
                4,
                "snapshot",
            ),
            StorageRequirement(
                self.data,
                migration,
                "database migration and retained database growth",
                256,
                "recovery",
            ),
            recovery,
            StorageRequirement(self.base, 16 * MIB, "release activation metadata", 32),
            self._journal_storage_requirement(),
        ]

    def _restore_allocation(
        self, tree: int, inodes: int, saved_db: int, live_db: int, *, stage: str | None = None
    ) -> StorageRequirement:
        # SQLite may need additional journal pages and growth while merging live
        # authority into the candidate. Keep a small floor for a fresh instance.
        total = tree + live_db + max(16 * MIB, saved_db, live_db)
        margin = self.storage_settings["snapshot_margin_percent"]
        return StorageRequirement(
            self.data.parent,
            total * (100 + margin) // 100,
            "restore candidate, private live SQLite authority and merge headroom",
            inodes + 128,
            stage,
        )

    def restore_requirement(self, backup: Path) -> StorageRequirement:
        """Restore-specific estimate against the actual snapshot destination."""
        data = Path(backup) / "data"
        usage = scan_usage(data)
        unit = self.filesystem_info(self.data.parent).allocation_unit
        return self._restore_allocation(
            usage.independent_copy_bytes(unit),
            usage.inodes,
            self._database_size(data, allocation_unit=unit)[0],
            self._database_size(allocation_unit=unit)[0],
        )

    def updater_requirement(
        self, archive_size: int | None = None, *, payload_size: int = 0
    ) -> list[StorageRequirement]:
        payload = max(payload_size, 8 * MIB)
        requirements = [
            StorageRequirement(
                self.updater_root,
                payload * 2,
                "verified updater staging and generation installation",
                128,
            ),
            *(
                StorageRequirement(parent, MIB, "launcher and command publication", 16)
                for parent in sorted({command.parent for command in self.updater_commands})
            ),
        ]
        if archive_size is not None:
            requirements.append(
                StorageRequirement(
                    self.temporary_root, archive_size or 64 * MIB, "updater archive download", 8
                )
            )
        return requirements

    def backup_requirements(self) -> list[StorageRequirement]:
        return [
            self.snapshot_requirement(),
            StorageRequirement(self.etc, 4 * MIB, "backup manifest and metadata", 32),
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
            if requirement.bytes <= 0 and requirement.inodes <= 0:
                continue
            try:
                info = self.filesystem_info(requirement.path)
            except (OSError, DeploymentError) as error:
                raise StorageError(
                    f"Storage preflight failed during {phase}: cannot inspect "
                    f"{requirement.path} ({describe_exception(error)}). Free space "
                    "cannot be established for every destination, so the operation "
                    "was refused.",
                    phase=phase,
                    filesystem=str(requirement.path),
                    available_bytes=0,
                    required_bytes=max(0, int(requirement.bytes)),
                    available_inodes=0,
                    required_inodes=max(0, int(requirement.inodes)),
                ) from error
            group = groups.setdefault(
                info.device,
                {
                    "info": info,
                    "stages": {},
                    "labels": [],
                    "paths": set(),
                },
            )
            stage = group["stages"].setdefault(requirement.stage, [0, 0])
            stage[0] += max(0, int(requirement.bytes))
            stage[1] += max(0, int(requirement.inodes))
            group["labels"].append(requirement.label)
            group["paths"].add(str(requirement.path))
        headroom = int(self.storage_settings["headroom_mib"]) * MIB
        inode_reserve = int(self.storage_settings["minimum_free_inodes"])
        failures = []
        for group in groups.values():
            common_bytes, common_inodes = group["stages"].get(None, (0, 0))
            phases = [cost for stage, cost in group["stages"].items() if stage is not None]
            required_bytes = common_bytes + max((cost[0] for cost in phases), default=0) + headroom
            required_inodes = (
                common_inodes + max((cost[1] for cost in phases), default=0) + inode_reserve
            )
            info = group["info"]
            bytes_short = info.available_bytes < required_bytes
            inodes_short = info.inode_reporting and info.available_inodes < required_inodes
            if not bytes_short and not inodes_short:
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
            inode_budget = (
                f"{info.available_inodes} inodes"
                if info.inode_reporting
                else "inode reporting unsupported on this filesystem"
            )
            lines.extend(
                [
                    f"Insufficient storage for {phase}.",
                    f"Filesystem: {info.anchor} (locations: {', '.join(sorted(group['paths']))})",
                    f"Available: {format_bytes(info.available_bytes)} ({inode_budget})",
                    f"Estimated requirement: {format_bytes(required_bytes)} "
                    f"({required_inodes} inodes) including configured headroom",
                    "Estimated contributors: " + "; ".join(dict.fromkeys(group["labels"])),
                    "The estimate is conservative, not a guarantee; allocation "
                    "overhead, quotas and the compressed archive size still vary.",
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

    def reclaim_caches(self, *, dry_run: bool = False, report: CleanupReport | None = None) -> int:
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
            before = scan_usage(cache).bytes
            print(f"Dependency cache {name}: {format_bytes(before)}")
            if dry_run:
                continue
            if not Path(command[0]).exists():
                message = f"{name} cache maintenance skipped: {command[0]} is missing"
                print(message, file=sys.stderr)
                if report is not None:
                    report.cache_failures.append(message)
                continue
            try:
                self.run(command, user=True)
            except (OSError, subprocess.CalledProcessError) as error:
                message = f"{name} cache maintenance failed ({describe_exception(error)})"
                print(
                    f"Cache cleanup warning: {message}. The original operation continues.",
                    file=sys.stderr,
                )
                if report is not None:
                    report.cache_failures.append(message)
                continue
            after = scan_usage(cache).bytes
            reclaimed += max(0, before - after)
            if before > after:
                print(f"Reclaimed {format_bytes(before - after)} from the {name} cache.")
        self._release_size_cache = {}
        return reclaimed

    def _referenced_release_paths(self) -> set[Path]:
        references: set[Path] = set()
        try:
            if self.current.is_symlink():
                references.add(Path(self.current.resolve()))
        except (OSError, RuntimeError):
            pass
        for source in (self.state, self.journal):
            value, valid, _reason = self._load_metadata(source, source.name)
            if not valid or not isinstance(value, dict):
                continue
            for key in ("target", "previous"):
                key_path = self._path_key(value.get(key))
                if key_path:
                    references.add(key_path)
            prior = value.get("prior_state")
            if isinstance(prior, dict):
                for key in ("target", "previous"):
                    key_path = self._path_key(prior.get(key))
                    if key_path:
                        references.add(key_path)
        return references

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
        referenced = self._referenced_release_paths()
        for path in children:
            if path.name.startswith(".staging-intent-"):
                continue
            try:
                info = path.lstat()
            except OSError as error:
                entries.append(
                    {
                        "path": path,
                        "kind": "unrecognized",
                        "version": "",
                        "order": 0.0,
                        "metadata": None,
                        "reason": "unreadable: " + describe_exception(error),
                    }
                )
                continue
            entry = {
                "path": path,
                "kind": "unrecognized",
                "version": "",
                "order": float(info.st_mtime),
                "metadata": None,
                "identity": (int(info.st_dev), int(info.st_ino)),
                "deployed": False,
            }
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                entry["reason"] = "not a real directory"
                entries.append(entry)
                continue
            if STAGING_DIRECTORY.fullmatch(path.name):
                marker = self.fs.read_staging_marker(path)
                if marker is None:
                    entry["reason"] = (
                        "staging-shaped directory without a recognised operation marker; "
                        "never automatically removed"
                    )
                else:
                    entry["kind"] = "staging"
                    entry["staging"] = marker
                entries.append(entry)
                continue
            match = RELEASE_DIRECTORY.fullmatch(path.name)
            if match is None:
                entry["reason"] = "unrecognized directory"
                entries.append(entry)
                continue
            version = match.group("version")
            entry["version"] = version
            metadata, valid, reason = self._load_metadata(path / RELEASE_MARKER, "release metadata")
            if metadata is None and valid:
                entry["kind"] = "incomplete"
            elif metadata is None or not valid or str(metadata.get("version")) != version:
                entry["kind"] = "invalid"
                entry["reason"] = reason or "release metadata disagrees with its version"
            else:
                entry["kind"] = "complete"
                entry["metadata"] = metadata
                activated_at = metadata.get("activated_at")
                is_deployed = isinstance(activated_at, (int, float)) and not isinstance(
                    activated_at, bool
                )
                if not is_deployed and path.resolve() in referenced:
                    is_deployed = True
                    entry["legacy_deployed"] = True
                entry["deployed"] = is_deployed
                if is_deployed and entry.get("legacy_deployed"):
                    entry["description"] = "deployed (legacy metadata; no activation timestamp)"
                elif is_deployed:
                    entry["description"] = "successfully deployed"
                else:
                    entry["description"] = "prepared but never activated"
                order = float(info.st_mtime)
                for key in ("prepared_at", "activated_at"):
                    value = metadata.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        order = max(order, float(value))
                entry["order"] = order
            entries.append(entry)
        return entries

    def _load_metadata(self, path: Path, label: str) -> tuple[dict | None, bool, str]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None, True, ""
        except OSError as error:
            return None, False, f"cannot inspect {label}: {describe_exception(error)}"
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self.privileged_uid
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            return None, False, f"{label} is not a trusted owner-controlled regular file"
        if (
            path in {self.state, self.journal, self.updater_state, self.migration_journal}
            and info.st_mode & 0o077
        ):
            return None, False, f"{label} must have permissions 0600"
        try:
            fd = os.open(path, os.O_NOFOLLOW | os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(fd, encoding="utf-8") as handle:
                opened = os.fstat(handle.fileno())
                if (
                    file_identity(opened) != file_identity(info)
                    or opened.st_ctime_ns != info.st_ctime_ns
                ):
                    return None, False, f"{label} changed during inspection"
                value = json.load(handle, object_pairs_hook=unique_metadata_fields)
        except (OSError, ValueError, TypeError, RecursionError):
            return None, False, f"{label} is unreadable or invalid JSON"
        if not isinstance(value, dict):
            return None, False, f"{label} is not a JSON object"
        return value, True, ""

    def _backup_status(self, path: Path, *, parent_fd: int | None = None) -> tuple[str, str]:
        path = Path(path)
        try:
            info = path.lstat()
        except OSError as error:
            return "unrecognized", "unreadable: " + describe_exception(error)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return "unrecognized", "not a real directory"
        if info.st_uid != self.privileged_uid:
            return "invalid", f"not root-owned (uid {info.st_uid})"
        if BACKUP_DIRECTORY.fullmatch(path.name) is None:
            return "unrecognized", "unrecognised backup name"
        if location := self._backup_location_error(path, parent_fd=parent_fd):
            return "invalid", location
        if not (path / "data").is_dir() or not (path / "config").is_dir():
            return "incomplete", "missing data or configuration directory"
        manifest = path / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            return "incomplete", "missing manifest"
        try:
            self.verify_snapshot(path, parent_fd=parent_fd)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return "invalid", "manifest verification failed: " + describe_exception(error)
        try:
            schema = self._verified_snapshot_schema(path)
            self._verify_snapshot_association(
                path, target=None, expected=None, allow_legacy=True, schema=schema
            )
        except (OSError, DeploymentError) as error:
            return "invalid", describe_exception(error)
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
            name = journal.get("snapshot_name")
            if isinstance(name, str) and BACKUP_DIRECTORY.fullmatch(name):
                backups.setdefault(self.backups / name, "planned unfinished transaction snapshot")
            prior = journal.get("prior_state")
            if isinstance(prior, dict):
                for field_name in ("target", "previous"):
                    key = self._path_key(prior.get(field_name))
                    if key:
                        releases.setdefault(key, "prior committed state reference")
                key = self._path_key(prior.get("backup"))
                if key:
                    backups.setdefault(key, "prior committed rollback state")
        # State intents nested inside recovery journals can carry another
        # rollback pair even before the new installed state is published.
        nested = [
            field
            for source in (state, journal)
            if source
            for key in ("prior_state", "rollback_state", "commit_state")
            if isinstance((field := source.get(key)), dict)
        ]
        while nested:
            prior = nested.pop()
            for field_name in ("target", "previous"):
                path = self._path_key(prior.get(field_name))
                if path is not None:
                    releases.setdefault(path, "recorded committed state reference")
            path = self._path_key(prior.get("backup"))
            if path is not None:
                backups.setdefault(path, "recorded committed rollback state")
            if isinstance(prior.get("prior_state"), dict):
                nested.append(prior["prior_state"])
        for value in protected_releases or set():
            key = self._path_key(str(value))
            if key:
                releases[key] = "release currently being prepared, activated or verified"
        for value in protected_backups or set():
            key = self._path_key(str(value))
            if key:
                backups[key] = "snapshot currently being created or verified"
        return releases, backups

    def _data_owner(self) -> int:
        return self._data_account()[0]

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
        started = time.monotonic()
        hashed_before = self.verification.bytes_hashed
        verified_before = self.verification.verifications
        report = CleanupReport(dry_run=dry_run, retention=dict(self.retention))
        if not dry_run:
            self.ensure_layout(protected_backups=protected_backups)
        state, state_ok, state_reason = self._load_metadata(self.state, "installed state")
        if state_ok and state is not None:
            state_ok, state_reason = self.validate_state_metadata(state)
        journal, journal_ok, journal_reason = self._load_metadata(
            self.journal, "transaction journal"
        )
        if journal_ok and journal is not None:
            journal_ok, journal_reason = self.validate_transaction_metadata(journal)
        unfinished = bool(
            journal
            and (journal.get("phase") not in {"committed", "recovered"} or journal.get("restore"))
        )
        if os.path.lexists(self.migration_journal):
            report.exception_reason = "unfinished backup migration; run hnefatafl-update --recover"
            report.refused = True
            return report
        can_delete = state_ok and journal_ok and (not unfinished or allow_unfinished)

        def note(reason: str) -> None:
            if reason and reason not in report.exception_reason:
                report.exception_reason = (
                    (report.exception_reason + " ") if report.exception_reason else ""
                ) + reason

        for valid, reason in ((state_ok, state_reason), (journal_ok, journal_reason)):
            if not valid:
                note(reason)
        if unfinished and not allow_unfinished:
            note("unfinished deployment transaction; run hnefatafl-update --recover")
        if (
            not self.state.exists()
            and not allow_unfinished
            and (self.journal.exists() or self.current.is_symlink())
        ):
            can_delete = False
            note("installed state metadata is missing for an active installation")
        if can_delete and state and self.current.is_symlink() and not unfinished:
            try:
                active = Path(self.current.resolve())
            except (OSError, RuntimeError):
                active = None
                can_delete = False
                note("the active release symlink cannot be resolved")
            target = self._path_key(state.get("target"))
            if active is not None and target is not None and active != target:
                can_delete = False
                note(
                    "installed state target and the active release symlink disagree; "
                    "refusing destructive cleanup to avoid deleting the active or "
                    "rollback-required release"
                )
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
        recorded = self._recorded_staging(journal_for_protection)
        blocked = self._settle_staging_units(recorded) if can_delete and not dry_run else {}
        report.errors.extend(dict.fromkeys(blocked.values()))
        if blocked:
            report.cleanup_complete = False
        now = time.time()

        def add(
            category,
            action,
            path,
            reason,
            *,
            bytes_size=0,
            allocated=0,
            hardlink=0,
            files=0,
            version="",
            status="",
            identity=None,
        ):
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
                allocated_bytes=allocated,
                hardlink_bytes=hardlink,
                status=status,
                identity=identity,
            )
            report.entries.append(entry)
            if action in {"keep", "retain"}:
                report.retained.append(entry)
            elif action == "skip":
                report.skipped.append(entry)
            if status:
                report.status_counts[status] = report.status_counts.get(status, 0) + 1
            return entry

        for path, record in recorded.items():
            if not os.path.lexists(path):
                continue
            if path in blocked:
                add("staging", "retain", path, blocked[path], status="protected")
                continue
            try:
                identity = self._staging_identity(path)
            except (OSError, DeploymentError) as error:
                add(
                    "staging",
                    "retain",
                    path,
                    f"{error}. Inspect the staging record.",
                    status="invalid",
                )
                continue
            if record.keep or path in protected_release_map or path in protected_backup_map:
                add(
                    "staging",
                    "keep",
                    path,
                    "recorded by an active or completed operation",
                    status="protected",
                )
            else:
                add(
                    "staging",
                    "remove",
                    path,
                    f"abandoned {record.operation} with a durable staging record",
                    status="incomplete",
                    identity=identity,
                )

        release_entries = self._release_entries()
        protected_release_count = 0
        protected_versions = set()
        complete_candidates = []
        for entry in release_entries:
            path = entry["path"]
            key = self._path_key(str(path))
            kind = entry["kind"]
            if path in blocked:
                protected_release_map.setdefault(path, blocked[path])
            if path in recorded and kind != "complete":
                continue
            if key in protected_release_map:
                protected_release_count += 1
                if entry["version"] and kind == "complete":
                    protected_versions.add(entry["version"])
                add(
                    "release",
                    "keep",
                    path,
                    protected_release_map[key],
                    version=entry["version"],
                    status="protected",
                    identity=entry.get("identity"),
                )
                continue
            age = max(0.0, now - entry["order"])
            if kind == "staging":
                if age < INCOMPLETE_GRACE_SECONDS:
                    add(
                        "staging",
                        "retain",
                        path,
                        "recent staging directory with a valid operation marker; may be in progress",
                        status="incomplete",
                        identity=entry.get("identity"),
                    )
                else:
                    add(
                        "staging",
                        "remove",
                        path,
                        "incomplete release preparation (marked staging directory)",
                        status="incomplete",
                        identity=entry.get("identity"),
                    )
            elif kind == "incomplete":
                marker = self.fs.read_staging_marker(path)
                if marker is None or marker["operation"] != "release":
                    add(
                        "release",
                        "retain",
                        path,
                        "incomplete release without a staging record; retained for manual review",
                        version=entry["version"],
                        status="incomplete",
                        identity=entry.get("identity"),
                    )
                elif age < INCOMPLETE_GRACE_SECONDS:
                    add(
                        "release",
                        "retain",
                        path,
                        "incomplete release preparation; recent and not yet eligible",
                        version=entry["version"],
                        status="incomplete",
                        identity=entry.get("identity"),
                    )
                else:
                    add(
                        "release",
                        "remove",
                        path,
                        "incomplete release preparation (no completion marker)",
                        version=entry["version"],
                        status="incomplete",
                        identity=entry.get("identity"),
                    )
            elif kind == "complete":
                complete_candidates.append(entry)
            elif kind == "invalid":
                add(
                    "release",
                    "retain",
                    path,
                    entry.get("reason")
                    or "invalid or inconsistent release metadata; retained for inspection",
                    version=entry["version"],
                    status="invalid",
                    identity=entry.get("identity"),
                )
            else:
                add(
                    "release",
                    "skip",
                    path,
                    entry.get("reason") or "unrecognized directory; never automatically removed",
                    status="unrecognized",
                    identity=entry.get("identity"),
                )

        slots = max(0, int(self.retention["releases"]) - protected_release_count)
        grouped: dict[str, list[dict]] = {}
        for entry in complete_candidates:
            grouped.setdefault(entry["version"], []).append(entry)
        selected = []
        for version, items in grouped.items():
            # Prefer a known-good deployed directory over an unactivated duplicate
            # of the same version, then the newest deterministic chronology.
            items.sort(
                key=lambda item: (
                    bool(item.get("deployed")),
                    item["order"],
                    item["path"].name,
                ),
                reverse=True,
            )
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
                        status="complete",
                        identity=duplicate.get("identity"),
                    )
                continue
            selected.append(items[0])
            for duplicate in items[1:]:
                add(
                    "release",
                    "remove",
                    duplicate["path"],
                    "duplicate release version; the deployed/newest directory is retained ("
                    + str(duplicate.get("description", "historical release"))
                    + ")",
                    version=version,
                    status="complete",
                    identity=duplicate.get("identity"),
                )
        selected.sort(
            key=lambda item: (
                bool(item.get("deployed")),
                item["order"],
                item["path"].name,
            ),
            reverse=True,
        )
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
                    status="complete",
                    identity=entry.get("identity"),
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
                    status="complete",
                    identity=entry.get("identity"),
                )
        if protected_release_count > int(self.retention["releases"]):
            note(
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
            except OSError as error:
                add(
                    "download",
                    "skip",
                    path,
                    "unreadable download staging: " + describe_exception(error),
                    status="unrecognized",
                )
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                add(
                    "download",
                    "skip",
                    path,
                    "download-shaped entry is not a real directory; never automatically removed",
                    status="unrecognized",
                )
                continue
            marker = self.fs.read_staging_marker(path)
            age = max(0.0, now - info.st_mtime)
            identity = (int(info.st_dev), int(info.st_ino))
            if marker is None:
                add(
                    "download",
                    "skip",
                    path,
                    "download-shaped directory without an operation marker; never "
                    "automatically removed",
                    status="unrecognized",
                    identity=identity,
                )
            elif age < INCOMPLETE_GRACE_SECONDS:
                add(
                    "download",
                    "retain",
                    path,
                    "recent download staging directory",
                    status="incomplete",
                    identity=identity,
                )
            else:
                add(
                    "download",
                    "remove",
                    path,
                    "interrupted archive download/extraction staging",
                    status="incomplete",
                    identity=identity,
                )

        backup_entries = []
        try:
            inventory = self._backup_inventory()
        except (OSError, DeploymentError) as error:
            report.errors.append(
                f"Cannot safely inspect backup locations: {describe_exception(error)}"
            )
            report.refused = True
            report.cleanup_complete = False
            return report
        if any(item[5] == "copy" for item in inventory) and can_delete:
            try:
                for path in self._migration_protected_backups():
                    protected_backup_map.setdefault(path, "required by backup migration")
            except (OSError, DeploymentError) as error:
                can_delete = False
                report.refused = True
                note(f"Cannot safely plan backup migration: {describe_exception(error)}")
        kept_backups = self._retained_backups(
            [
                (path, info.st_mtime, destination or path)
                for path, info, status, _, destination, _ in inventory
                if status == "complete"
            ],
            set(protected_backup_map),
        )
        copied = Usage()
        for path, info, status, detail, destination, migration in inventory:
            if path in recorded:
                continue
            identity = (int(info.st_dev), int(info.st_ino))
            key = self._path_key(str(path))
            if migration is None and STAGING_DIRECTORY.fullmatch(path.name):
                marker = self.fs.read_staging_marker(path)
                age = max(0.0, now - info.st_mtime)
                if key in protected_backup_map:
                    add(
                        "backup",
                        "keep",
                        path,
                        protected_backup_map[key],
                        status="protected",
                        identity=identity,
                    )
                elif marker is None:
                    add(
                        "backup",
                        "skip",
                        path,
                        "staging-shaped directory without an operation marker; never "
                        "automatically removed",
                        status="unrecognized",
                        identity=identity,
                    )
                elif age < INCOMPLETE_GRACE_SECONDS:
                    add(
                        "staging",
                        "retain",
                        path,
                        "recent snapshot staging directory",
                        status="incomplete",
                        identity=identity,
                    )
                else:
                    add(
                        "staging",
                        "remove",
                        path,
                        "incomplete snapshot creation (marked staging directory)",
                        status="incomplete",
                        identity=identity,
                    )
                continue
            entry_status = {
                "complete": "verified",
                "incomplete": "incomplete",
                "invalid": "invalid",
                "unrecognized": "unrecognized",
            }.get(status, "unrecognized")
            if status == "complete":
                if destination is not None and (migration == "move" or path in kept_backups):
                    detail = f"verified legacy snapshot to {destination}"
                    if migration == "copy":
                        usage = self._legacy_cleanup_entry(path, identity, dry_run=True)
                        copied += usage
                        cost = self._migration_copy_requirement(usage)
                        detail += f" requires {cost.bytes:,} bytes and {cost.inodes} inodes"
                    add("backup", migration, path, detail, status="migration", identity=identity)
                placement = (
                    path
                    if migration == "copy" and path not in kept_backups
                    else destination or path
                )
                backup_entries.append((path, info.st_mtime, identity, placement))
            elif status == "incomplete":
                if key in protected_backup_map:
                    add(
                        "backup",
                        "keep",
                        path,
                        protected_backup_map[key],
                        status=entry_status,
                        identity=identity,
                    )
                elif max(0.0, now - info.st_mtime) < INCOMPLETE_GRACE_SECONDS:
                    add(
                        "backup",
                        "retain",
                        path,
                        detail + "; recent and not yet eligible",
                        status=entry_status,
                        identity=identity,
                    )
                else:
                    add(
                        "backup",
                        "remove",
                        path,
                        "incomplete snapshot (" + detail + ")",
                        status=entry_status,
                        identity=identity,
                    )
            elif status == "invalid":
                add(
                    "backup",
                    "retain",
                    path,
                    detail + "; retained for manual inspection",
                    status=entry_status,
                    identity=identity,
                )
            else:
                add(
                    "backup",
                    "skip",
                    path,
                    "unrecognized backup directory; never automatically removed",
                    status=entry_status,
                    identity=identity,
                )
        protected_backup_count = sum(
            1
            for path, _mtime, _identity, _destination in backup_entries
            if self._path_key(str(path)) in protected_backup_map
        )
        if copied.inodes:
            cost = self._migration_copy_requirement(copied)
            print(
                f"Retained legacy copies require {cost.bytes:,} bytes and "
                f"{cost.inodes} inodes on {self.backups}, plus headroom."
            )
        backup_entries.sort(key=lambda item: (item[1], item[3].name), reverse=True)
        unprotected_backups = []
        for path, mtime, identity, destination in backup_entries:
            key = self._path_key(str(path))
            if key in protected_backup_map:
                add(
                    "backup",
                    "keep",
                    path,
                    protected_backup_map[key],
                    status="protected",
                    identity=identity,
                )
            else:
                unprotected_backups.append((path, mtime, identity, destination))
        for path, _mtime, identity, destination in unprotected_backups:
            if path in kept_backups:
                add(
                    "backup",
                    "keep",
                    path,
                    "within configured backup retention",
                    status="verified",
                    identity=identity,
                )
            else:
                add(
                    "backup",
                    "remove",
                    path,
                    "beyond configured backup retention"
                    + (f" after migration to {destination}" if destination != path else ""),
                    status="verified",
                    identity=identity,
                )
        if protected_backup_count > int(self.retention["backups"]):
            note(
                f"retention exception: {protected_backup_count} verified backups are "
                f"protected but the configured limit is {self.retention['backups']}; "
                "no protected backup was removed"
            )

        def add_restore(category, action, path, reason, **fields):
            if path not in recorded:
                add(category, action, path, reason, **fields)

        self._plan_interrupted_restores(report, add_restore, journal, journal_ok, now)

        if include_caches:
            for name in ("uv", "npm"):
                cache = self.data / "cache" / name
                if not cache.is_dir():
                    continue
                usage = scan_usage(cache)
                if dry_run:
                    add(
                        "cache",
                        "prune",
                        cache,
                        f"unused {name} cache entries would be pruned (current size)",
                        bytes_size=usage.bytes,
                        allocated=usage.allocated_bytes,
                        files=usage.inodes,
                    )
                elif can_delete:
                    add("cache", "prune", cache, f"unused {name} cache entries")
                else:
                    add("cache", "retain", cache, "cache cleanup disabled by metadata state")

        reusable_roots = {
            "release": self.releases,
            "backup": self.backups,
            "download": self.base,
            "restore-staging": self.data.parent,
            "retained": self.data.parent,
        }

        def location_root(candidate: CleanupEntry) -> Path:
            if candidate.category == "backup":
                return candidate.path.parent
            if candidate.category in {"staging"}:
                if candidate.path.parent in {self.releases, self.backups}:
                    return candidate.path.parent
                if candidate.path.parent == self.data.parent:
                    return self.data.parent
            if candidate.category == "restore-staging":
                return self.data.parent
            return reusable_roots.get(candidate.category, candidate.path.parent)

        def owners_for(candidate: CleanupEntry) -> set[int]:
            owners = {self.privileged_uid}
            if candidate.category in {"staging", "restore-staging"} and (
                candidate.path.parent in {self.releases, self.backups}
                or candidate.category == "restore-staging"
            ):
                owners.add(self._data_owner())
            return owners

        before_space = self._free_space_snapshot(
            [location_root(c) for c in report.entries if c.action == "remove"]
        )
        for candidate in report.entries:
            if candidate.action != "remove":
                continue
            try:
                usage = (
                    self._legacy_cleanup_entry(candidate.path, candidate.identity, dry_run=True)
                    if candidate.path.parent == self.legacy_backups
                    else scan_usage(candidate.path)
                )
            except (OSError, DeploymentError) as error:
                report.errors.append(
                    f"Cannot measure {candidate.path}: {describe_exception(error)}"
                )
                candidate.action = "failed"
                candidate.reason = describe_exception(error)
                report.failed.append(candidate)
                report.cleanup_complete = False
                continue
            candidate.bytes = usage.bytes
            candidate.files = usage.inodes
            candidate.allocated_bytes = usage.allocated_bytes
            candidate.hardlink_bytes = usage.hardlink_bytes
            report.hardlink_bytes += usage.hardlink_bytes
            if dry_run:
                if can_delete:
                    report.reclaimable_bytes += usage.bytes
                    report.reclaimable_allocated_bytes += usage.reclaimable_bytes
                continue
            root = location_root(candidate)
            try:
                if candidate.path in recorded:
                    self._discard_staging(candidate.path, mixed=recorded[candidate.path].mixed)
                elif root == self.legacy_backups:
                    self._legacy_cleanup_entry(candidate.path, candidate.identity, dry_run=False)
                else:
                    self._safe_remove(
                        root,
                        candidate.path,
                        identity=candidate.identity,
                        owners=owners_for(candidate),
                    )
            except (OSError, DeploymentError, subprocess.CalledProcessError) as error:
                candidate.action = "failed"
                candidate.reason = describe_exception(error)
                report.failed.append(candidate)
                report.errors.append(f"Cannot remove {candidate.path}: {describe_exception(error)}")
                report.cleanup_complete = False
            else:
                candidate.action = "removed"
                report.reclaimable_bytes += usage.bytes
                report.reclaimable_allocated_bytes += usage.reclaimable_bytes
                report.removed.append(candidate)

        if not dry_run and can_delete:
            for path, record in recorded.items():
                if (
                    not record.keep
                    and path not in blocked
                    and not os.path.lexists(path)
                    and os.path.lexists(self._staging_intent(path))
                ):
                    try:
                        self._discard_staging(path, mixed=record.mixed)
                    except (OSError, DeploymentError) as error:
                        report.errors.append(f"Cannot finalize staging record for {path}: {error}")
                        report.cleanup_complete = False
        if include_caches and not dry_run and can_delete:
            report.reclaimable_bytes += self.reclaim_caches(dry_run=False, report=report)
        if not dry_run:
            after_space = self._free_space_snapshot([Path(key) for key in before_space])
            report.reclaimed_bytes = self._free_space_delta(before_space, after_space)
        self._release_size_cache = {}
        report.verified_backups = self.verification.verifications - verified_before
        report.manifest_bytes_hashed = self.verification.bytes_hashed - hashed_before
        report.duration_seconds = time.monotonic() - started
        return report

    def _plan_interrupted_restores(self, report, add, journal, journal_ok, now) -> None:
        """Never prune retained data; clean only clearly abandoned restore staging."""
        try:
            children = sorted(self.data.parent.iterdir(), key=lambda item: item.name)
        except (FileNotFoundError, OSError):
            return
        active_restore = None
        if journal_ok and isinstance(journal, dict) and isinstance(journal.get("restore"), dict):
            active_restore = journal["restore"]
        for path in children:
            if RETAINED_DIRECTORY.fullmatch(path.name):
                add(
                    "retained",
                    "keep",
                    path,
                    "retained persistent application data; never pruned automatically",
                    status="protected",
                )
                continue
            if RESTORE_STAGING_DIRECTORY.fullmatch(path.name) is None:
                continue
            try:
                info = path.lstat()
            except OSError as error:
                add(
                    "restore-staging",
                    "skip",
                    path,
                    "unreadable restore staging: " + describe_exception(error),
                    status="unrecognized",
                )
                continue
            identity = (int(info.st_dev), int(info.st_ino))
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                add(
                    "restore-staging",
                    "skip",
                    path,
                    "restore-shaped entry is not a real directory; retained",
                    status="unrecognized",
                    identity=identity,
                )
                continue
            if os.path.lexists(path / GATE_NAME):
                add(
                    "restore-staging",
                    "keep",
                    path,
                    "restore staging contains a deployment gate",
                    status="protected",
                    identity=identity,
                )
                continue
            marker = self._load_metadata(path / RESTORE_MARKER, "restore staging marker")
            value, valid, reason = marker
            if not valid or value is None:
                add(
                    "restore-staging",
                    "retain",
                    path,
                    "restore staging without a valid identity marker; retained for manual review",
                    status="invalid",
                    identity=identity,
                )
                continue
            transaction = value.get("transaction")
            if not isinstance(transaction, str) or not re.fullmatch(r"[0-9a-f]{8,32}", transaction):
                add(
                    "restore-staging",
                    "retain",
                    path,
                    "restore staging marker has no valid transaction identity; retained",
                    status="invalid",
                    identity=identity,
                )
                continue
            if active_restore and active_restore.get("transaction") == transaction:
                add(
                    "restore-staging",
                    "keep",
                    path,
                    "restore staging required by the unfinished transaction journal",
                    status="protected",
                    identity=identity,
                )
                continue
            age = max(0.0, now - info.st_mtime)
            if age < INCOMPLETE_GRACE_SECONDS:
                add(
                    "restore-staging",
                    "retain",
                    path,
                    "recent restore staging directory",
                    status="incomplete",
                    identity=identity,
                )
            else:
                add(
                    "restore-staging",
                    "remove",
                    path,
                    "abandoned restore staging directory with no matching transaction",
                    status="incomplete",
                    identity=identity,
                )

    def _safe_remove(
        self,
        root: Path,
        path: Path,
        *,
        identity: tuple[int, int] | None = None,
        owners: set[int] | None = None,
        mixed: bool = False,
    ) -> None:
        """Remove one validated artifact below a validated managed root."""
        expected = owners or {self.privileged_uid}
        description = f"managed directory {Path(root)}"
        root_identity = self.fs.validate_root(
            Path(root),
            owner=self.privileged_uid,
            description=description,
            allow_nested_mounts=Path(root) == self.data.parent,
        )
        try:
            info = os.lstat(path)
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect {path}: {describe_exception(error)}; refusing to delete"
            ) from error
        if info.st_uid not in expected:
            raise DeploymentError(
                f"Refusing to remove {path}: it is owned by uid {info.st_uid}, expected "
                f"one of {sorted(expected)}"
            )
        self.fs.remove_tree(
            root_identity,
            Path(path),
            expected_owner=expected if mixed else int(info.st_uid),
            identity=identity,
        )

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
            "restore-staging": "Restore staging",
            "retained": "Retained data (never pruned)",
            "cache": "Dependency caches",
        }
        for category in (
            "release",
            "backup",
            "staging",
            "download",
            "restore-staging",
            "retained",
            "cache",
        ):
            entries = [entry for entry in report.entries if entry.category == category]
            if not entries:
                continue
            print(headings[category] + ":")
            for entry in sorted(entries, key=lambda item: str(item.path)):
                verb = entry.action.upper()
                if report.dry_run and entry.action in {"remove", "move", "copy"}:
                    verb = f"WOULD {verb}"
                label = f"{entry.version} " if entry.version else ""
                status = f" [{entry.status}]" if entry.status else ""
                size = f" ({format_bytes(entry.bytes)})" if entry.bytes else ""
                print(f"  {verb} {label}{entry.path}: {entry.reason}{status}{size}")
        if report.hardlink_bytes:
            print(
                "Hardlinked content in selected artifacts: "
                f"{format_bytes(report.hardlink_bytes)} in additional names"
            )
        print(f"Estimated apparent reclamation: {format_bytes(report.reclaimable_bytes)}")
        print(
            "Allocated artifact blocks eligible for reclamation: "
            f"{format_bytes(report.reclaimable_allocated_bytes)}"
        )
        if not report.dry_run:
            print(f"Actually reclaimed filesystem space: {format_bytes(report.reclaimed_bytes)}")
        if report.status_counts:
            print(
                "Artifact states: "
                + ", ".join(
                    f"{name}={count}" for name, count in sorted(report.status_counts.items())
                )
            )
        print(
            f"Verifications: {report.verified_backups} snapshot(s), "
            f"{format_bytes(report.manifest_bytes_hashed)} hashed; "
            f"duration {report.duration_seconds:.2f}s"
        )
        if report.exception_reason:
            print(f"Retention exception: {report.exception_reason}")
        if report.cache_failures:
            print("Cache maintenance:", file=sys.stderr)
            for failure in report.cache_failures:
                print("  " + failure, file=sys.stderr)
        if report.errors:
            print("Cleanup errors:", file=sys.stderr)
            for error in report.errors:
                print("  " + error, file=sys.stderr)
        if report.refused and not report.dry_run:
            print(
                "Destructive cleanup was refused; no artifact was removed.",
                file=sys.stderr,
            )

    def _recorded_staging(
        self,
        journal: dict | None,
    ) -> dict[Path, StagingRecord]:
        restore = (journal or {}).get("restore") or {}
        protected = {restore.get("staging"), restore.get("retained")}
        pending = set()
        for entry in self._release_entries():
            metadata = entry.get("metadata")
            if entry["kind"] == "complete" and metadata and isinstance(metadata.get("sha256"), str):
                path = self._pending_updater_for(metadata["sha256"])
                if path is not None:
                    pending.add(path)
        roots = (
            (self.temporary_root, STAGING_DIRECTORY, {"prepare", "verify", "refresh"}, True),
            (self.releases, RELEASE_DIRECTORY, {"release"}, True),
            (self.backups, STAGING_DIRECTORY, {"backup"}, False),
            (self.data.parent, RESTORE_STAGING_DIRECTORY, {"restore"}, True),
            (self.updater_root, PENDING_UPDATER_DIRECTORY, {"pending"}, False),
            (self.updater_root / "generations", STAGING_DIRECTORY, {"generation"}, False),
        )
        records = {}
        for root, pattern, operations, mixed in roots:
            for marker in root.glob(".staging-intent-*.json"):
                intent, valid, _ = self._load_metadata(marker, "staging intent")
                name = intent.get("name") if valid and intent else None
                operation = intent.get("operation") if intent else None
                preparation_id = intent.get("preparation_id") if intent else None
                if (
                    not isinstance(name, str)
                    or not pattern.fullmatch(name)
                    or operation not in operations
                    or self._staging_intent(root / name) != marker
                ):
                    print(f"Unrecognized staging record retained: {marker}", file=sys.stderr)
                    if root == self.temporary_root:
                        name_hint = "." + marker.stem.removeprefix(".staging-intent-")
                        path = root / name_hint
                        if STAGING_DIRECTORY.fullmatch(name_hint) and os.path.lexists(path):
                            records[path] = StagingRecord("prepare", True, True)
                    continue
                path = root / name
                keep = (
                    str(path) in protected
                    or path in pending
                    or operation == "restore"
                    and os.path.lexists(path / GATE_NAME)
                    or operation == "release"
                    and os.path.lexists(path / RELEASE_MARKER)
                )
                if preparation_id is not None and (
                    not isinstance(preparation_id, str)
                    or not re.fullmatch(r"[a-f0-9]{16}", preparation_id)
                    or root == self.temporary_root
                    and path.name != f".staging-{operation}-{preparation_id}"
                ):
                    # Keep the workspace visible to the blocker pass, including
                    # records from which dependent artifacts cannot be identified.
                    preparation_id = None
                    keep = True
                records[path] = StagingRecord(operation, mixed, keep, preparation_id)
        for path in self.updater_root.glob(".pending-*"):
            if PENDING_UPDATER_DIRECTORY.fullmatch(path.name) and path not in records:
                records[path] = StagingRecord("pending updater", False, path in pending)
        return records

    def _settle_staging_units(self, records: dict[Path, StagingRecord]) -> dict[Path, str]:
        blocked: dict[Path, str] = {}
        groups: dict[str | None, str] = {}
        for path, record in records.items():
            if path.parent != self.temporary_root or not os.path.lexists(path):
                continue
            try:
                if record.keep:
                    raise DeploymentError("The preparation workspace association is invalid")
                self._staging_identity(path)
                self._stop_staging_units(path)
            except (OSError, DeploymentError, subprocess.SubprocessError) as error:
                message = (
                    f"Cannot confirm termination of the preparation units for {path}: "
                    f"{describe_exception(error)}. Inspect systemctl and retry cleanup."
                )
                blocked[path] = message
                groups[record.preparation_id] = message
                if record.keep:
                    # An invalid new identifier cannot safely exclude any group.
                    groups[""] = message
        for path, record in records.items():
            if record.operation in {"release", "pending", "pending updater"}:
                reason = groups.get(record.preparation_id) or groups.get("")
                if reason:
                    blocked[path] = reason
        return blocked

    def _remove_recorded_staging(self, journal: dict | None, protected: set[Path]) -> None:
        records = self._recorded_staging(journal)
        blocked = self._settle_staging_units(records)
        self._blocked_staging = blocked
        for message in dict.fromkeys(blocked.values()):
            print(message, file=sys.stderr)
        for path, record in records.items():
            if path in protected or path in blocked:
                continue
            if record.keep:
                if record.operation == "release":
                    with suppress(DeploymentError):
                        self._require_complete_release(path)
                        self._finish_staging(path)
                continue
            try:
                self._discard_staging(path, mixed=record.mixed)
            except (OSError, DeploymentError) as error:
                print(
                    f"Staging cleanup warning for {path}: {describe_exception(error)}",
                    file=sys.stderr,
                )

    @staticmethod
    def _staging_unit_prefix(path: Path) -> str:
        if not re.fullmatch(r"\.staging-(?:prepare|verify|refresh)-[a-f0-9]{16}", path.name):
            raise DeploymentError(f"Unknown preparation workspace: {path}")
        return "hnefatafl-" + path.name.removeprefix(".staging-") + "-"

    def _preparation_units(self, path: Path) -> set[str] | None:
        prefix = self._staging_unit_prefix(path)
        record, valid, _ = self._load_metadata(path / ".units.json", "preparation unit record")
        if not valid:
            raise DeploymentError(f"Cannot read the preparation unit record for {path}")
        if record is None:
            return None
        names = record.get("units")
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not re.fullmatch(prefix + r"[a-f0-9]{8}\.service", name)
            for name in names
        ):
            raise DeploymentError(f"Invalid preparation unit names for {path}")
        return set(names)

    def _active_staging_units(self, path: Path, recorded: set[str] | None) -> set[str]:
        prefix = self._staging_unit_prefix(path)
        result = subprocess.run(
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--all",
                "--full",
                "--plain",
                "--no-legend",
                "--no-pager",
                prefix + "????????.service",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        active = set()
        for line in result.stdout.splitlines():
            columns = line.split()
            if not columns or not re.fullmatch(prefix + r"[a-f0-9]{8}\.service", columns[0]):
                continue
            if recorded is not None and columns[0] not in recorded:
                continue
            if len(columns) < 4:
                raise DeploymentError(f"Cannot interpret systemd state for {columns[0]}")
            if columns[2] not in {"inactive", "failed"}:
                active.add(columns[0])
        return active

    def _clear_preparation_workspace(self, path: Path) -> None:
        self._stop_staging_units(path)
        self._discard_staging(path, mixed=True)

    def _stop_staging_units(self, path: Path) -> None:
        if self._data_owner() == self.privileged_uid:
            return
        # Disarm jobs that have not yet reached systemd, then stop and inspect
        # the exact units recorded for this workspace. Older staging can only
        # be identified by its complete, unpredictable unit-name prefix.
        units = self._preparation_units(path)
        intent, valid, _ = self._load_metadata(self._staging_intent(path), "staging intent")
        if units is None and valid and intent and intent.get("preparation_id") is not None:
            # Current staging records its unit names before systemd can run them.
            return
        (path / ".units-allowed").unlink(missing_ok=True)
        sync_directory(path)
        active = self._active_staging_units(path, units)
        if active:
            self.run(["systemctl", "stop", *sorted(active)])
        if remaining := self._active_staging_units(path, units):
            raise DeploymentError("Build units are still active: " + ", ".join(sorted(remaining)))

    def _remove_orphan_staging(self) -> None:
        """Remove only marked staging artifacts from an interrupted operation.

        The global deployment lock is held, so no other updater operation owns these
        paths. A directory name alone is never sufficient: the marker written when the
        staging directory was created must be present and valid.
        """
        journal, journal_valid, _reason = self._load_metadata(self.journal, "transaction journal")
        state, state_valid, _ = self._load_metadata(self.state, "installed state")
        if state is not None and state_valid:
            state_valid, _ = self.validate_state_metadata(state)
        if journal is not None and journal_valid:
            journal_valid, _ = self.validate_transaction_metadata(journal)
        if not state_valid or not journal_valid:
            return
        protected_release, protected_backup = self._cleanup_protection(state, journal, None, None)
        protected_restore = {str(path) for path in {*protected_release, *protected_backup}}
        if isinstance(journal, dict):
            restore = journal.get("restore")
            if isinstance(restore, dict):
                for key in ("staging", "retained"):
                    value = restore.get(key)
                    if isinstance(value, str) and value:
                        protected_restore.add(str(Path(value)))
        self._remove_recorded_staging(journal, {*protected_release, *protected_backup})
        for root, pattern, owners in (
            (
                self.releases,
                STAGING_DIRECTORY,
                {self.privileged_uid, self._data_owner()},
            ),
            (self.backups, STAGING_DIRECTORY, {self.privileged_uid}),
            (self.base, DOWNLOAD_DIRECTORY, {self.privileged_uid}),
            (self.data.parent, RESTORE_STAGING_DIRECTORY, {self.privileged_uid}),
        ):
            if not root.exists():
                continue
            try:
                children = list(root.iterdir())
            except (FileNotFoundError, OSError):
                continue
            for path in children:
                if pattern.fullmatch(path.name) is None or os.path.lexists(
                    self._staging_intent(path)
                ):
                    continue
                if (
                    str(path) in protected_restore
                    or pattern == RESTORE_STAGING_DIRECTORY
                    and os.path.lexists(path / GATE_NAME)
                ):
                    continue
                if (
                    self.fs.read_staging_marker(path) is None
                    and self._load_metadata(path / RESTORE_MARKER, "restore staging marker")[0]
                    is None
                ):
                    print(
                        f"Staging cleanup warning: {path} has no recognised operation "
                        "marker; retained for manual review.",
                        file=sys.stderr,
                    )
                    continue
                try:
                    identity = self.fs.identity_of(path)
                    self._safe_remove(
                        root,
                        path,
                        identity=identity,
                        owners=owners,
                    )
                except (OSError, DeploymentError) as error:
                    print(
                        f"Staging cleanup warning: {path}: {describe_exception(error)}",
                        file=sys.stderr,
                    )
                else:
                    print(f"Removed interrupted staging artifact: {path}")

    def _post_operation_cleanup(
        self, operation: str, *, protected_backups: set[Path] | None = None
    ) -> None:
        try:
            report = self.cleanup(
                dry_run=False, automatic=True, protected_backups=protected_backups
            )
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
                f"Storage retention removed {len(report.removed)} artifact(s); "
                f"apparent {format_bytes(report.reclaimable_bytes)}, actually reclaimed "
                f"{format_bytes(report.reclaimed_bytes)}."
            )
        if report.exception_reason:
            print(f"Retention note after {operation}: {report.exception_reason}")
        for entry in report.failed:
            print(
                f"Cleanup warning after {operation}: {entry.path}: {entry.reason}. "
                "The operation itself already completed.",
                file=sys.stderr,
            )

    def _preflight_or_reclaim(
        self,
        requirements,
        *,
        phase: str,
        service_state: str | None,
        protected_releases: set[Path] | None,
        protected_backups: set[Path] | None = None,
        persistent_data: str = "unchanged",
    ) -> None:
        def evaluated() -> list[StorageRequirement]:
            return requirements() if callable(requirements) else requirements

        try:
            self.check_storage(
                evaluated(),
                phase=phase,
                service_state=service_state,
                persistent_data=persistent_data,
                protected_releases=protected_releases,
            )
            return
        except StorageError:
            self._release_size_cache = {}
            try:
                report = self.cleanup(
                    dry_run=False,
                    automatic=True,
                    allow_unfinished=True,
                    protected_releases=protected_releases,
                    protected_backups=protected_backups,
                )
            except BaseException as error:
                print(
                    f"Storage cleanup before {phase} failed: {describe_exception(error)}",
                    file=sys.stderr,
                )
            else:
                if report.removed:
                    print(f"Reclaimed {format_bytes(report.reclaimed_bytes)} before {phase}.")
            self.reclaim_caches(dry_run=False)
            self._release_size_cache = {}
            # Recalculate after cleanup because the relevant assumptions changed.
            self.check_storage(
                evaluated(),
                phase=phase,
                service_state=service_state,
                persistent_data=persistent_data,
                protected_releases=protected_releases,
            )

    def preflight_prepare(self, archive_size: int) -> None:
        self._preflight_or_reclaim(
            lambda: self._prepare_requirements(archive_size),
            phase="release preparation",
            service_state="running",
            protected_releases=set(),
        )

    def preflight_deployment(self, target: Path, *, service_state: str | None) -> None:
        self._preflight_or_reclaim(
            self._deployment_requirements,
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
        before = scan_usage(target).bytes
        candidates = [
            target / "frontend/node_modules",
            target / "frontend/.vite",
            target / ".pytest_cache",
            target / ".mypy_cache",
            target / ".ruff_cache",
        ]
        candidates.extend(target.rglob("__pycache__"))
        root = self.fs.validate_root(
            Path(target),
            owner=self.privileged_uid,
            description="prepared release",
        )
        for path in candidates:
            if path.is_symlink() or not path.exists():
                continue
            self.fs.remove_tree(
                root,
                path,
                expected_owner=os.lstat(path).st_uid,
                identity=self.fs.identity_of(path),
                allow_nested=True,
            )
        after = scan_usage(target).bytes
        self._release_size_cache = {}
        if before > after:
            print(f"Removed build-only release files, reclaiming {format_bytes(before - after)}.")

    # ------------------------------------------------------------------
    # Trusted updater delivery
    # ------------------------------------------------------------------
    UPDATER_MEMBERS = {
        "deployment": "hnefatafl/deployment.py",
        "launcher": "scripts/linux/launcher.py",
        "update": "scripts/linux/update.sh",
        "backup": "scripts/linux/backup.sh",
        **{name: f"hnefatafl/{name}.py" for name in UPDATER_SUPPORT_MODULES},
    }

    # These version-one command entry points are stable across generations.
    # Future migrations must explicitly support both command bodies through an
    # interrupted replacement. Reviewed repair restores the canonical bytes.
    MAINTENANCE_WRAPPERS = {
        "scripts/linux/update.sh": (
            b"#!/usr/bin/env bash\nset -euo pipefail\n"
            b'exec /usr/bin/python3 -I -B /usr/local/lib/hnefatafl/deployment.py "$@"\n'
        ),
        "scripts/linux/backup.sh": (
            b"#!/usr/bin/env bash\nset -euo pipefail\n"
            b"exec /usr/local/bin/hnefatafl-update --backup\n"
        ),
    }

    @staticmethod
    def _read_archive_members(archive: Path, members: dict[str, str]) -> dict[str, bytes]:
        """Read exact regular-file members from a digest-verified archive."""
        wanted = set(members.values())
        payload: dict[str, bytes] = {}
        with tarfile.open(archive, "r:gz") as bundle:
            found = set()
            for member in bundle:
                name = member.name.removeprefix("./")
                if name not in wanted:
                    continue
                if name in found or not member.isfile() or member.issym() or member.islnk():
                    raise DeploymentError(
                        f"Trusted updater member {name} is not a regular file in the "
                        "verified archive; refusing to install it"
                    )
                if member.size > 16 * 1024 * 1024:
                    raise DeploymentError(
                        f"Trusted updater member {name} is unexpectedly large; refusing "
                        "to install it"
                    )
                handle = bundle.extractfile(member)
                if handle is None:
                    raise DeploymentError(
                        f"Trusted updater member {name} cannot be read from the archive"
                    )
                payload[name] = handle.read(16 * 1024 * 1024 + 1)
                if len(payload[name]) > 16 * 1024 * 1024:
                    raise DeploymentError(f"Trusted updater member {name} exceeds the size limit")
                found.add(name)
        missing = wanted - found
        if missing:
            raise DeploymentError(
                "The verified release archive is missing trusted updater members: "
                + ", ".join(sorted(missing))
            )
        return payload

    @staticmethod
    def _declared_protocol(source: str, name: str, *, default: int = 0) -> int:
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return default
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        value = node.value
                        if isinstance(value, ast.Constant) and type(value.value) is int:
                            return int(value.value)
                        return 0
        return default

    def _installed_updater_abi(self, *, verify_active: bool = True) -> int:
        active = self._active_updater(verify=verify_active)
        if active:
            return active["abi"]
        value, valid, reason = self._load_metadata(self.updater_state, "installed updater state")
        if not valid:
            raise DeploymentError(f"Invalid installed updater state: {reason}")
        abi = 1
        if value is not None:
            recorded = value.get("abi")
            if value.get("schema") != 1 or type(recorded) is not int or recorded < 1:
                raise DeploymentError(f"Invalid installed updater ABI at {self.updater_state}")
            abi = recorded
        if os.path.lexists(self.updater_module):
            source = self._utility_file(self.updater_module).read_text()
            compile(source, str(self.updater_module), "exec")
            abi = max(abi, self._declared_protocol(source, "UPDATER_ABI", default=1))
        return abi

    def _validate_updater_payload(
        self, payload: dict[str, bytes], *, verify_active: bool = True
    ) -> int:
        try:
            module_source = payload["hnefatafl/deployment.py"].decode("utf-8", "strict")
        except (KeyError, UnicodeDecodeError) as error:
            raise DeploymentError(
                "The candidate updater payload has no decodable deployment.py"
            ) from error
        try:
            compile(module_source, "deployment.py", "exec")
        except (SyntaxError, ValueError) as error:
            raise DeploymentError(
                f"Refusing to install an invalid deployment utility: {describe_exception(error)}"
            ) from error
        abi = self._declared_protocol(module_source, "UPDATER_ABI")
        installed = self._installed_updater_abi(verify_active=verify_active)
        if abi < 1:
            raise DeploymentError(
                "The candidate deployment utility does not declare a supported protocol "
                "version; refusing to install it"
            )
        if abi < installed:
            raise DeploymentError(
                f"Refusing to downgrade the deployment utility protocol from {installed} "
                f"to {abi}; the older utility cannot interpret the current transaction "
                "or retention metadata. Run a newer installer or use --refresh-updater "
                "from a newer verified release."
            )
        if abi < MIN_GENERATION_ABI:
            raise DeploymentError(
                "The candidate updater does not support coherent utility generations"
            )
        minimum = self._declared_protocol(module_source, "MIN_GENERATION_ABI", default=abi)
        upgrading = any(
            os.path.lexists(path)
            for path in (self.updater_module, self.updater_state, self.updater_root / "active.json")
        )
        if (
            not 3 <= minimum <= abi
            or upgrading
            and abi > max(MIN_GENERATION_ABI, installed + 1)
            or installed >= MIN_GENERATION_ABI
            and minimum > installed
        ):
            raise DeploymentError(
                "Unsupported updater ABI transition. Install the intervening compatible generation first."
            )
        journal, valid, reason = self._load_metadata(self.journal, "transaction journal")
        if not valid:
            raise DeploymentError(f"Cannot establish updater ABI compatibility: {reason}")
        recorded = journal.get("abi") if journal else None
        if recorded is not None and (
            type(recorded) is not int or recorded > abi or 3 <= recorded < minimum
        ):
            raise DeploymentError(
                "The candidate updater cannot read the recorded deployment transaction"
            )
        for member in (
            self.UPDATER_MEMBERS["launcher"],
            *(f"hnefatafl/{name}.py" for name in UPDATER_SUPPORT_MODULES),
        ):
            source = payload.get(member)
            if source is None:
                raise DeploymentError(f"The candidate updater payload is missing {member}")
            try:
                compile(source.decode("utf-8", "strict"), member, "exec")
            except (SyntaxError, ValueError, UnicodeDecodeError) as error:
                raise DeploymentError(
                    f"Refusing to install invalid restore support {member}: "
                    f"{describe_exception(error)}"
                ) from error
        for wrapper, canonical in self.MAINTENANCE_WRAPPERS.items():
            if payload.get(wrapper) != canonical:
                raise DeploymentError(
                    f"Unsupported maintenance wrapper for the stable command protocol: {wrapper}"
                )
        return abi

    def _stage_updater_payload(
        self,
        archive: Path,
        tag: str,
        digest: str,
        *,
        preparation_id: str | None = None,
    ) -> Path:
        """Persist authenticated updater sources in root-controlled storage."""
        self._managed_root(
            self.updater_root,
            owner=self.privileged_uid,
            mode=0o755,
            description="installed utility directory",
            write=True,
        )
        payload = self._read_archive_members(archive, self.UPDATER_MEMBERS)
        abi = self._validate_updater_payload(payload)
        token = re.sub(r"[^a-f0-9]", "", digest.lower())[-16:] or secrets.token_hex(8)
        staging = self.updater_root / (".pending-" + token)
        if staging.exists() or os.path.lexists(self._staging_intent(staging)):
            if staging in self._blocked_staging:
                raise DeploymentError(self._blocked_staging[staging])
            self._remove_pending_updater(staging)
        self._begin_staging(staging, "pending", preparation_id=preparation_id)
        files = {}
        for member, content in payload.items():
            destination = staging / member
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            files[member] = hashlib.sha256(content).hexdigest()
        sync_tree(staging)
        atomic_json(
            staging / "manifest.json",
            {
                "schema": 1,
                "tag": tag,
                "archive_sha256": digest,
                "abi": abi,
                "files": files,
                "created_at": time.time(),
            },
        )
        print(f"Staged trusted deployment utility from verified release archive {tag}.")
        return staging

    def _remove_pending_updater(self, staging: Path) -> None:
        staging = Path(staging)
        if os.path.lexists(self._staging_intent(staging)):
            self._discard_staging(staging)
            return
        try:
            info = os.lstat(staging)
        except OSError:
            return
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self.privileged_uid
            or not staging.name.startswith(".pending-")
        ):
            raise DeploymentError(
                f"Refusing to remove {staging}: it is not a root-owned updater staging directory"
            )
        self.fs.remove_tree(
            self.fs.validate_root(
                self.updater_root,
                owner=self.privileged_uid,
                description="installed utility directory",
            ),
            staging,
            expected_owner=self.privileged_uid,
            identity=(int(info.st_dev), int(info.st_ino)),
        )

    def _pending_updater_for(self, digest: str) -> Path | None:
        token = re.sub(r"[^a-f0-9]", "", digest.lower())[-16:]
        if not token:
            return None
        candidate = self.updater_root / (".pending-" + token)
        if not candidate.is_dir() or candidate.is_symlink():
            return None
        return candidate

    def _updater_publication_plan(self, staging: Path) -> dict[str, bytes]:
        self.fs.validate_root(
            staging, owner=self.privileged_uid, mode=0o700, description="pending updater directory"
        )
        manifest, valid, reason = self._load_metadata(
            staging / "manifest.json", "updater staging manifest"
        )
        files = manifest.get("files") if valid and manifest else None
        if not isinstance(files, dict) or set(files) != set(self.UPDATER_MEMBERS.values()):
            raise DeploymentError(
                f"Refusing to publish an unverified updater set: {reason or 'incomplete manifest'}"
            )
        payload = {}
        for member, digest in files.items():
            content = self._utility_file(staging / member).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise DeploymentError(f"Updater staging member {member} changed after staging")
            payload[member] = content
        return payload

    def _utility_file(self, path: Path) -> Path:
        self.fs._validate_ancestors(path, "deployment utility")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self.privileged_uid
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or self.fs.is_mount(path)
        ):
            raise DeploymentError(f"The deployment utility file is unsafe: {path}")
        return path

    def _generation_files(self, payload: dict[str, bytes]) -> dict[str, bytes]:
        return {
            "deployment.py": payload[self.UPDATER_MEMBERS["deployment"]],
            "launcher.py": payload[self.UPDATER_MEMBERS["launcher"]],
            "update.sh": payload[self.UPDATER_MEMBERS["update"]],
            "backup.sh": payload[self.UPDATER_MEMBERS["backup"]],
            **{
                f"updater_support/{name}.py": payload[self.UPDATER_MEMBERS[name]]
                for name in UPDATER_SUPPORT_MODULES
            },
        }

    def _active_updater(self, *, verify: bool = True) -> dict | None:
        path = self.updater_root / "active.json"
        active, valid, reason = self._load_metadata(path, "active updater generation")
        if not valid:
            raise DeploymentError(f"Invalid active updater generation: {reason}")
        if active is None:
            return None
        identifier = active.get("generation")
        previous = active.get("previous")
        if (
            active.get("schema") != 1
            or type(active.get("abi")) is not int
            or active["abi"] < 3
            or not isinstance(identifier, str)
            or not re.fullmatch(r"[a-f0-9]{64}", identifier)
            or previous is not None
            and (
                not isinstance(previous, str)
                or not re.fullmatch(r"[a-f0-9]{64}", previous)
                or identifier == previous
            )
        ):
            raise DeploymentError(f"Invalid active updater state: {path}")
        if verify:
            manifest = self._verify_generation(path.parent / "generations" / identifier)
            if manifest.get("abi") != active.get("abi"):
                raise DeploymentError(
                    f"The installed updater ABI conflicts with its generation: {path}"
                )
        return active

    def _verify_generation(self, path: Path) -> dict:
        self.fs.validate_root(path, owner=self.privileged_uid, description="updater generation")
        manifest_path = self._utility_file(path / "manifest.json")
        content = manifest_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != path.name:
            raise DeploymentError(f"Updater generation manifest did not verify: {path}")
        try:
            manifest = json.loads(content)
        except ValueError as error:
            raise DeploymentError(f"Updater generation manifest is invalid: {path}") from error
        expected = {"deployment.py", "launcher.py", "update.sh", "backup.sh"} | {
            f"updater_support/{name}.py" for name in UPDATER_SUPPORT_MODULES
        }
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(files, dict) or set(files) != expected:
            raise DeploymentError(f"Updater generation is incomplete: {path}")
        for name, digest in files.items():
            file = self._utility_file(path / name)
            if file.stat().st_mode & 0o222 or checksum(file) != digest:
                raise DeploymentError(f"Installed updater generation file did not verify: {file}")
        return manifest

    def _publish_updater_plan(
        self, payload: dict[str, bytes], *, tag: str, repair: bool = False
    ) -> None:
        """Make one verified generation visible through an atomic pointer."""
        self._managed_root(
            self.updater_root,
            owner=self.privileged_uid,
            mode=0o755,
            description="installed utility directory",
            write=True,
        )
        abi = self._validate_updater_payload(payload, verify_active=not repair)
        files = self._generation_files(payload)
        active = self._active_updater(verify=not repair)
        launcher = files["launcher.py"]
        installed_launcher = None
        if os.path.lexists(self.updater_module):
            installed_launcher = self._utility_file(self.updater_module).read_bytes()
        launcher_protocol = self._declared_protocol(
            launcher.decode(), "LAUNCHER_PROTOCOL", default=1
        )
        module_source = files["deployment.py"].decode()
        minimum_launcher = self._declared_protocol(
            module_source, "MIN_LAUNCHER_PROTOCOL", default=1
        )
        active_launcher = None
        active_minimum = 1
        if active:
            previous_generation = self.updater_root / "generations" / active["generation"]
            try:
                previous_metadata = self._verify_generation(previous_generation)
            except (OSError, DeploymentError):
                if not repair:
                    raise
            else:
                active_launcher = (previous_generation / "launcher.py").read_bytes()
                active_minimum = previous_metadata.get("minimum_launcher_protocol", 1)
        protocol_source = active_launcher
        if installed_launcher is not None and (
            active_launcher is None or installed_launcher == launcher
        ):
            protocol_source = installed_launcher
        installed_protocol = 1
        if protocol_source is not None:
            installed_protocol = self._declared_protocol(
                protocol_source.decode(errors="replace"), "LAUNCHER_PROTOCOL", default=1
            )
        if (
            type(active_minimum) is not int
            or not 1 <= minimum_launcher <= launcher_protocol
            or launcher_protocol < max(installed_protocol, active_minimum)
            or (active is not None or installed_launcher is not None)
            and launcher_protocol > installed_protocol + 1
        ):
            raise DeploymentError(
                "Unsupported launcher protocol transition for the installed or candidate generation"
            )
        legacy_entry = False
        if installed_launcher not in (None, launcher, active_launcher):
            if active is None:
                legacy_entry = True
            else:
                previous_flat = self.updater_root / "legacy-deployment.py"
                legacy_entry = (
                    previous_flat.is_file()
                    and self._utility_file(previous_flat).read_bytes() == installed_launcher
                )
                if not legacy_entry and not repair:
                    raise DeploymentError(
                        "The installed launcher is inconsistent with the active updater generation. Use the reviewed installer to repair it."
                    )
        wrappers: list[tuple[Path, bytes]] = []
        for command, name in zip(self.updater_commands, ("update.sh", "backup.sh"), strict=True):
            self._managed_root(
                command.parent,
                owner=self.privileged_uid,
                mode=0o755,
                description="updater command directory",
                write=True,
            )
            if not os.path.lexists(command):
                wrappers.append((command, files[name]))
                continue
            existing = self._utility_file(command)
            content = existing.read_bytes()
            if content == files[name] and stat.S_IMODE(existing.stat().st_mode) == 0o755:
                continue
            legacy_update = files[name].replace(b"/usr/bin/python3 -I -B ", b"/usr/bin/python3 ")
            if (
                not repair
                and content != files[name]
                and (name != "update.sh" or content != legacy_update)
            ):
                raise DeploymentError(
                    f"The existing updater wrapper is incompatible: {command}. "
                    "Use the reviewed installer to repair it."
                )
            wrappers.append((command, files[name]))
        if legacy_entry and installed_launcher is not None:
            atomic_bytes(self.updater_root / "legacy-deployment.py", installed_launcher, 0o444)

        generations = self.updater_root / "generations"
        self._managed_root(
            generations,
            owner=self.privileged_uid,
            mode=0o700,
            description="updater generations directory",
            write=True,
        )
        manifest = json.dumps(
            {
                "schema": 1,
                "abi": abi,
                "version": tag,
                "launcher_protocol": launcher_protocol,
                "minimum_launcher_protocol": minimum_launcher,
                "files": {
                    name: hashlib.sha256(content).hexdigest() for name, content in files.items()
                },
            },
            sort_keys=True,
        ).encode()
        identifier = hashlib.sha256(manifest).hexdigest()
        destination = generations / identifier
        damaged = None
        previous = active["generation"] if active else None
        if repair and active:
            try:
                self._active_updater()
            except (OSError, DeploymentError):
                damaged = generations / active["generation"]
                previous = active.get("previous")
                if previous is not None:
                    self._verify_generation(generations / previous)
                if damaged == destination and os.path.lexists(damaged):
                    os.rename(damaged, generations / (".damaged-" + secrets.token_hex(8)))
                    sync_directory(generations)
        if not os.path.lexists(destination):
            staging = generations / (".staging-generation-" + secrets.token_hex(8))
            self._begin_staging(staging, "generation")
            try:
                (staging / "updater_support").mkdir(mode=0o700)
                for name, content in files.items():
                    atomic_bytes(staging / name, content, 0o444)
                atomic_bytes(staging / "manifest.json", manifest, 0o444)
                sync_tree(staging)
                os.rename(staging, destination)
                sync_directory(generations)
            finally:
                self._discard_staging(staging)
        self._verify_generation(destination)
        smoke = """
import inspect, runpy, sys
from pathlib import Path
path = Path(sys.argv[1])
module = runpy.run_path(str(path / 'deployment.py'))
inspect.signature(module['main']).bind([], installed_generation=path, deployment_lock=None)
assert callable(module['guard_service_start'])
updater = module['Deployment'](
    {}, base=Path(sys.argv[2]), data=Path(sys.argv[3]), etc=Path(sys.argv[4]),
    backups=Path(sys.argv[5]), updater_root=path,
)
updater._load_online_support()
for name, validate in (
    ('journal', updater.validate_transaction_metadata),
    ('state', updater.validate_state_metadata),
):
    record, readable, reason = updater._load_metadata(getattr(updater, name), name)
    if readable and record is not None:
        readable, reason = validate(record)
    if not readable:
        raise ValueError(reason)
"""
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    smoke,
                    *(
                        str(path)
                        for path in (destination, self.base, self.data, self.etc, self.backups)
                    ),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DeploymentError(
                "The candidate utility generation failed its standalone import check"
            ) from error
        # Existing installations switch to the compatible launcher while it
        # still selects the verified old generation. Flat bootstrap must put
        # the pointer in place first because the flat command ignores it.
        switched_launcher = active is not None and damaged is None and not legacy_entry
        if switched_launcher and installed_launcher != launcher:
            atomic_bytes(self.updater_module, launcher, 0o444)
        if previous != identifier or damaged is not None:
            atomic_json(
                self.updater_root / "active.json",
                {
                    "schema": 1,
                    "abi": abi,
                    "generation": identifier,
                    "previous": previous if previous != identifier else None,
                },
            )
        # Older wrappers already invoke this exact file. The old module and its
        # support files remain usable up to this final atomic bootstrap step.
        if not switched_launcher and installed_launcher != launcher:
            atomic_bytes(self.updater_module, launcher, 0o444)
        for command, content in wrappers:
            atomic_bytes(command, content, 0o755)
        protected = {identifier, previous}
        if damaged is not None:
            protected.add(damaged.name)
        if active:
            protected.add(active.get("previous"))
        for entry in generations.iterdir():
            if entry.name in protected or not re.fullmatch(r"[a-f0-9]{64}", entry.name):
                continue
            try:
                self._verify_generation(entry)
                self._safe_remove(generations, entry, identity=self.fs.identity_of(entry))
            except (OSError, DeploymentError) as error:
                print(
                    f"Updater generation cleanup warning: {describe_exception(error)}",
                    file=sys.stderr,
                )
        print("Installed deployment utility generation activated.")

    def _legacy_refresh_entries(self, journal: dict) -> list[tuple[Path, Path, Path, bool, bool]]:
        allowed = {
            self.updater_module,
            *self.updater_commands,
            *(self.updater_support / f"{name}.py" for name in UPDATER_SUPPORT_MODULES),
        }
        entries = journal.get("entries")
        phase = journal.get("phase")
        if (
            journal.get("schema") != 1
            or phase not in ("preparing", "prepared", "replacing", "committed", "rolled-back")
            or not isinstance(entries, list)
            or not entries
        ):
            raise DeploymentError("Invalid legacy updater refresh journal")
        result = []
        for entry in entries:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) for key in ("destination", "previous", "temporary")
            ):
                raise DeploymentError("Invalid legacy updater refresh journal entry")
            destination, previous, temporary = (
                Path(entry[key]) for key in ("destination", "previous", "temporary")
            )
            prefix = re.escape(destination.name)
            match = re.fullmatch(rf"{prefix}\.previous-([a-f0-9]{{6,32}})", previous.name)
            if (
                destination not in allowed
                or match is None
                or previous.parent != destination.parent
                or temporary != destination.with_name(f"{destination.name}.new-{match[1]}")
                or not isinstance(entry.get("had_previous"), bool)
                or not isinstance(entry.get("replaced"), bool)
            ):
                raise DeploymentError("Unsafe paths in the legacy updater refresh journal")
            allowed.remove(destination)
            for path in (previous, temporary, destination):
                if os.path.lexists(path):
                    self._utility_file(path)
            result.append(
                (destination, previous, temporary, entry["had_previous"], entry["replaced"])
            )
        return result

    def recover_updater_refresh(self) -> None:
        """Reconcile publications left by the pre-generation updater."""
        path = self.updater_root / ".refresh.json"
        journal, valid, reason = self._load_metadata(path, "legacy updater refresh journal")
        if not valid:
            raise DeploymentError(f"Cannot recover the previous utility from {path}: {reason}")
        if journal is None:
            return
        entries = self._legacy_refresh_entries(journal)
        if journal["phase"] not in ("committed", "rolled-back"):
            if journal["phase"] != "preparing":
                # A consumed temporary identifies a replacement even when the
                # old updater was killed before it wrote `replaced` to disk.
                for destination, previous, temporary, had_previous, replaced in entries:
                    if not replaced and temporary.exists():
                        continue
                    if had_previous:
                        if not previous.is_file():
                            raise DeploymentError(
                                f"Previous utility is missing: {previous}. Recovery metadata remains at {path}."
                            )
                        atomic_bytes(
                            destination,
                            previous.read_bytes(),
                            stat.S_IMODE(previous.stat().st_mode),
                        )
                    else:
                        destination.unlink(missing_ok=True)
                        sync_directory(destination.parent)
            journal["phase"] = "rolled-back"
            atomic_json(path, journal)
        for destination, previous, temporary, _, _ in entries:
            previous.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            sync_directory(destination.parent)
        path.unlink()
        sync_directory(path.parent)
        print("Recovered an interrupted legacy updater refresh.")

    def _refresh_installed_updater(self, target: Path) -> None:
        """Publish the updater set staged from the digest-verified archive."""
        if not self.manage_updater:
            return
        metadata = self._require_complete_release(target)
        digest = metadata.get("sha256")
        if not isinstance(digest, str):
            raise DeploymentError(
                "Refusing to refresh the installed utility without the verified archive "
                f"digest recorded in {Path(target) / RELEASE_MARKER}"
            )
        staging = self._pending_updater_for(digest)
        if staging is None:
            if (
                metadata
                and metadata.get("refresh_utility") is False
                and self._use_installed_updater(str(metadata["version"]))
            ):
                print("Keeping the installed updater for this historical application release.")
                return
            raise DeploymentError(
                "No trusted updater staging set was prepared from the verified release "
                "archive; refusing to copy a utility from the application-writable "
                "release tree. Re-run the update so preparation can stage it."
            )
        plan = self._updater_publication_plan(staging)
        self._preflight_or_reclaim(
            lambda: self.updater_requirement(payload_size=sum(map(len, plan.values()))),
            phase="updater refresh",
            service_state="unchanged",
            protected_releases={target},
        )
        if self._require_complete_release(target) != metadata:
            raise DeploymentError(
                "Prepared release metadata changed during updater storage reclamation"
            )
        self._publish_updater_plan(
            plan, tag=str(metadata.get("version", "")) if isinstance(metadata, dict) else ""
        )
        self._remove_pending_updater(staging)

    def refresh_updater(self, github: GitHub, release: dict) -> None:
        """Install only the trusted updater set from a published release."""
        if not self.manage_updater:
            raise DeploymentError(
                "The installed deployment utility is not managed by this installation layout"
            )
        tag = release["tag_name"]
        version_key(tag)
        archive_size = self._archive_size(release)
        self._managed_root(
            self.updater_root,
            owner=self.privileged_uid,
            mode=0o755,
            description="installed utility directory",
            write=True,
        )
        self._preflight_or_reclaim(
            lambda: self.updater_requirement(archive_size),
            phase="updater refresh",
            service_state="unchanged",
            protected_releases=set(),
        )
        self._remove_orphan_staging()
        preparation_id = secrets.token_hex(8)
        workspace = self._temporary_workspace("refresh", preparation_id=preparation_id)
        staging = None
        try:
            archive = workspace / "release.tar.gz"
            digest = github.download(release, archive)
            metadata = tomllib.loads(
                self._read_archive_members(archive, {"pyproject": "pyproject.toml"})[
                    "pyproject.toml"
                ].decode("utf-8")
            )
            if metadata.get("project", {}).get("version") != tag:
                raise DeploymentError("Release tag and package version disagree")
            staging = self._stage_updater_payload(
                archive, tag, digest, preparation_id=preparation_id
            )
            plan = self._updater_publication_plan(staging)
            self._publish_updater_plan(plan, tag=tag)
        finally:
            self._discard_staging(workspace, mixed=True)
            if staging is not None:
                self._remove_pending_updater(staging)
        print(f"Deployment utility refreshed from release {tag}.")

    @staticmethod
    def _archive_size(release: dict) -> int:
        for asset in release.get("assets", []):
            if asset.get("name") == f"hnefatafl-{release.get('tag_name')}.tar.gz":
                size = asset.get("size")
                if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                    return size
        return 0

    # ------------------------------------------------------------------
    # Standalone restore support
    # ------------------------------------------------------------------
    def _load_online_support(self):
        """Load schema-aware restore helpers without a source checkout import.

        The installed utility is a standalone script executed by the system Python.
        Its root-owned ``updater_support`` package is published from the same
        digest-verified release archive as ``deployment.py``. Source checkouts (and
        the test suite) still use the normal package import.
        """
        if self._online_support is not None:
            return self._online_support
        support = self.updater_support
        if not support.is_dir():
            try:
                from . import online_migrations as support_module
            except (ImportError, ValueError):
                try:
                    from hnefatafl import online_migrations as support_module  # type: ignore
                except ImportError as error:
                    raise DeploymentError(
                        "Restore support is not installed. Run the verified installer or "
                        "hnefatafl-update --refresh-updater so /usr/local/lib/hnefatafl/"
                        "updater_support is available, then retry. The service was not "
                        "stopped and no data was replaced."
                    ) from error
            self._online_support = support_module
            return support_module
        try:
            info = os.lstat(support)
        except OSError as error:
            raise DeploymentError(
                f"Cannot inspect the installed restore support ({support}): "
                f"{describe_exception(error)}"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DeploymentError(
                f"The installed restore support {support} is not a real directory; "
                "refusing to import it"
            )
        if info.st_uid != self.privileged_uid or stat.S_IMODE(info.st_mode) & 0o022:
            raise DeploymentError(
                f"The installed restore support {support} is not root-owned and "
                "non-writable; refusing to import it"
            )
        for name in UPDATER_SUPPORT_MODULES:
            module_path = support / f"{name}.py"
            try:
                module_info = os.lstat(module_path)
            except OSError as error:
                raise DeploymentError(
                    f"The installed restore support is incomplete ({module_path}): "
                    f"{describe_exception(error)}. Refresh the deployment utility."
                ) from error
            if stat.S_ISLNK(module_info.st_mode) or not stat.S_ISREG(module_info.st_mode):
                raise DeploymentError(
                    f"The installed restore support module {module_path} is not a "
                    "regular file; refusing to import it"
                )
            if module_info.st_uid != self.privileged_uid or module_info.st_mode & 0o022:
                raise DeploymentError(
                    f"The installed restore support module {module_path} is writable by "
                    "another account; refusing to import it"
                )
        package_name = "hnefatafl_updater_support"
        try:
            package = types.ModuleType(package_name)
            package.__path__ = [str(support)]
            sys.modules[package_name] = package
            spec = importlib.util.spec_from_file_location(
                f"{package_name}.online_migrations",
                support / "online_migrations.py",
            )
            if spec is None or spec.loader is None:
                raise DeploymentError(
                    "The installed restore support cannot be loaded; refresh the deployment utility"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        except (ImportError, OSError, SyntaxError) as error:
            raise DeploymentError(
                "The installed restore support could not be imported safely: "
                f"{describe_exception(error)}. The service was not stopped and no data "
                "was replaced; refresh the deployment utility from a verified release."
            ) from error
        if not callable(getattr(module, "retain_online_on_restore", None)) or not callable(
            getattr(module, "pause_online_after_restore", None)
        ):
            raise DeploymentError(
                "The installed restore support lacks the required schema-aware restore "
                "functions; refresh the deployment utility from a verified release"
            )
        self._online_support = module
        return module

    # ------------------------------------------------------------------
    # Snapshots and verification
    # ------------------------------------------------------------------
    def _assert_snapshot_sources_safe(self) -> None:
        self._data_directory(self.data)
        self.fs.validate_root(
            self.etc, owner=self.privileged_uid, description="configuration directory"
        )
        if self.fs.nested_mounts(self.data):
            raise DeploymentError(
                f"Nested mounts beneath {self.data} are unsupported for automated snapshots."
            )

    @staticmethod
    def _snapshot_name() -> str:
        return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(8)

    def _snapshot_association(self, transaction: dict | None, database_schema: int | None) -> dict:
        purpose = "manual"
        release = None
        if transaction and transaction["operation"] != "manual-backup":
            release = Path(transaction["previous"] or transaction["target"])
            purpose = "pre-deployment" if transaction["previous"] else "initial-install"
        elif self.current.is_symlink():
            release = self.current.resolve(strict=True)
        identity, version, digest = None, None, None
        if release is not None:
            metadata = self._require_complete_release(release)
            identity = list(self.fs.identity_of(release))
            version, digest = metadata["version"], metadata.get("sha256")
        association = {
            "schema": 2,
            "snapshot": transaction["snapshot_id"] if transaction else secrets.token_hex(16),
            "purpose": purpose,
            "transaction": transaction["transaction"] if transaction else secrets.token_hex(16),
            "created_at": time.time(),
            "version": version,
            "release": str(release) if release is not None else None,
            "release_identity": identity,
            "release_sha256": digest,
            "database_schema": database_schema,
            "updater_abi": UPDATER_ABI,
            "hnefatafl_data_dir": str(self.data),
        }
        self._validate_snapshot_association(association)
        return association

    def _validate_snapshot_association(self, association: dict) -> None:
        fields = {
            "schema",
            "snapshot",
            "purpose",
            "transaction",
            "created_at",
            "version",
            "release",
            "release_identity",
            "release_sha256",
            "database_schema",
            "updater_abi",
            "hnefatafl_data_dir",
        }
        purpose = association.get("purpose")
        created = association.get("created_at")
        schema = association.get("database_schema")
        abi = association.get("updater_abi")
        if (
            set(association) != fields
            or association.get("schema") != 2
            or purpose not in ("manual", "pre-deployment", "initial-install")
            or not all(
                isinstance(association.get(key), str)
                and re.fullmatch(r"[a-f0-9]{32}", association[key])
                for key in ("snapshot", "transaction")
            )
            or not isinstance(created, (int, float))
            or isinstance(created, bool)
            or not 0 < created < 1e12
            or schema is not None
            and (type(schema) is not int or schema < 0)
            or type(abi) is not int
            or not 3 <= abi <= UPDATER_ABI
            or association.get("hnefatafl_data_dir") != str(self.data)
        ):
            raise DeploymentError("Invalid versioned snapshot association metadata")
        release, version, digest = (
            association[key] for key in ("release", "version", "release_sha256")
        )
        identity = association["release_identity"]
        if (
            purpose == "manual"
            and release is None
            and all(value is None for value in (identity, version, digest))
        ):
            return
        if (
            not self._release_reference(release)
            or not valid_identity(identity)
            or not isinstance(version, str)
            or version != Path(release).name.partition("-")[0]
            or not (
                purpose == "manual"
                and digest is None
                or isinstance(digest, str)
                and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            )
        ):
            raise DeploymentError(
                "Invalid release identity or archive digest in snapshot association metadata"
            )

    def snapshot(self, *, transaction: dict | None = None) -> Path:
        self.ensure_layout()
        self._backup_root(write=True)
        self._assert_snapshot_sources_safe()
        backup = self.backups / (".staging-backup-" + secrets.token_hex(8))
        final = self.backups / (
            transaction["snapshot_name"] if transaction else self._snapshot_name()
        )
        if os.path.lexists(final):
            raise DeploymentError(f"The snapshot destination already exists: {final}")
        self._begin_staging(backup, "backup")
        try:
            self.fs.write_staging_marker(backup, "backup")
            excluded = self._snapshot_excluded() - {
                "hnefatafl.sqlite3",
                "hnefatafl.sqlite3-wal",
                "hnefatafl.sqlite3-journal",
            }
            self._copy_snapshot_tree(self._data_directory(self.data), backup / "data", excluded)
            config = self.fs.validate_root(
                self.etc, owner=self.privileged_uid, description="configuration directory"
            )
            self._copy_snapshot_tree(config, backup / "config", {JOURNAL_RESERVE})
            database_schema = self._snapshot_database(backup)
            association = self._snapshot_association(transaction, database_schema)
            atomic_json(backup / SNAPSHOT_METADATA, association)
            self._remove_staging_marker(backup)
            self._write_snapshot_manifest(backup)
            self.verify_snapshot(backup)
            sync_tree(backup)
            os.rename(backup, final)
            sync_directory(self.backups)
            self._finish_staging(backup)
        except BaseException:
            try:
                self._discard_staging(backup)
            except (OSError, DeploymentError) as cleanup_error:
                print(
                    f"Snapshot staging cleanup warning: {describe_exception(cleanup_error)}",
                    file=sys.stderr,
                )
            raise
        self.verification.put(final, "complete")
        if transaction is not None:
            transaction["backup_association"] = association
        return final

    def _copy_snapshot_tree(self, root: RootIdentity, destination: Path, excluded) -> None:
        def failed(error):
            raise error

        with self.fs.open_directory(root) as start:
            owners = {os.fstat(start).st_uid, self.privileged_uid}
            destination.mkdir(mode=0o700)
            for relative, directories, files, directory in os.fwalk(
                ".", dir_fd=start, follow_symlinks=False, onerror=failed
            ):
                target = destination / relative
                if relative == ".":
                    directories[:] = [name for name in directories if name not in excluded]
                    files = [name for name in files if name not in excluded]
                for name in (*directories, *files):
                    pinned = os.open(
                        name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
                    )
                    try:
                        info = os.fstat(pinned)
                        if info.st_uid not in owners or info.st_dev != root.device:
                            raise DeploymentError(
                                f"Unsafe owner or filesystem in snapshot source: {root.path / relative / name}"
                            )
                        mode = stat.S_IMODE(info.st_mode) & 0o777
                        if name in directories and stat.S_ISDIR(info.st_mode):
                            (target / name).mkdir(mode=0o700)
                        elif name in files and stat.S_ISREG(info.st_mode):
                            fd = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_CLOEXEC)
                            with os.fdopen(fd, "rb") as source, (target / name).open("xb") as copy:
                                shutil.copyfileobj(source, copy, length=MIB)
                                os.fchmod(copy.fileno(), mode & 0o755)
                        else:
                            raise DeploymentError(
                                f"Non-regular entry in snapshot source: {root.path / relative / name}"
                            )
                    finally:
                        os.close(pinned)

    def _remove_staging_marker(self, directory: Path) -> None:
        (Path(directory) / STAGING_MARKER).unlink(missing_ok=True)

    def _snapshot_database(self, backup: Path) -> int | None:
        # The live DB and WAL were copied after writers stopped. SQLite opens
        # the private copies, where journal recovery cannot affect live paths.
        database = backup / "data/hnefatafl.sqlite3"
        if not database.exists():
            return None
        target = backup / ".sqlite-backup"
        database.with_name(database.name + "-shm").unlink(missing_ok=True)
        with (
            closing(sqlite3.connect(database)) as source,
            closing(sqlite3.connect(target)) as copy,
        ):
            source.backup(copy)
            copy.commit()
            copy.execute("PRAGMA journal_mode=DELETE")
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DeploymentError("Backup SQLite integrity check failed")
            schema = int(copy.execute("PRAGMA user_version").fetchone()[0])
        os.replace(target, database)
        for suffix in ("-wal", "-shm", "-journal"):
            database.with_name(database.name + suffix).unlink(missing_ok=True)
        self._snapshot_schema_cache[self._database_cache_key(database)] = schema
        return schema

    def _write_snapshot_manifest(self, backup: Path) -> None:
        manifest = {}
        for current, directories, names in os.walk(
            backup, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            for name in (*directories, *names):
                entry = Path(current) / name
                try:
                    info = os.lstat(entry)
                except OSError as error:
                    raise DeploymentError(
                        f"Cannot inspect snapshot entry {entry}: {describe_exception(error)}"
                    ) from error
                if stat.S_ISLNK(info.st_mode):
                    raise DeploymentError(
                        "Backup encountered a symlink; inspect storage before continuing"
                    )
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise DeploymentError("Backup verification failed: unexpected special file")
                relative = entry.relative_to(backup).as_posix()
                if relative == "manifest.json":
                    continue
                manifest[relative] = checksum(entry)
                self.verification.hashed(info.st_size)
        atomic_json(backup / "manifest.json", manifest)

    def _backup_location_error(self, backup: Path, *, parent_fd: int | None = None) -> str:
        backup = Path(backup)
        try:
            info = os.lstat(backup)
        except OSError as error:
            return f"Cannot inspect the snapshot ({describe_exception(error)})"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return "The snapshot path is not a real directory"
        if parent_fd is None:
            try:
                self.fs._validate_ancestors(backup, "snapshot")
            except DeploymentError as error:
                return str(error)
        if info.st_uid != self.privileged_uid:
            return f"The snapshot is not root-owned (uid {info.st_uid})"
        if stat.S_IMODE(info.st_mode) & 0o077:
            return (
                f"The snapshot permissions are {stat.S_IMODE(info.st_mode):04o}, not root-only 0700"
            )
        try:
            parent = os.lstat(backup.parent) if parent_fd is None else os.fstat(parent_fd)
        except OSError as error:
            return f"Cannot inspect the snapshot directory ({describe_exception(error)})"
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            return "The snapshot directory is not a real directory"
        if parent.st_uid != self.privileged_uid or stat.S_IMODE(parent.st_mode) & 0o022:
            return "The snapshot directory is writable by another account or not root-owned"
        return ""

    def verify_snapshot(self, backup, *, parent_fd: int | None = None):
        backup = Path(backup)
        location = self._backup_location_error(backup, parent_fd=parent_fd)
        if location:
            raise DeploymentError("Backup verification failed: " + location)
        if self.verification.get(backup) == "complete":
            return
        self.verification.verifications += 1
        if parent_fd is None and (self.fs.is_mount(backup) or self.fs.nested_mounts(backup)):
            raise DeploymentError("Backup verification failed: snapshot contains a mount")
        if any(
            not (backup / component).is_dir() or (backup / component).is_symlink()
            for component in ("data", "config")
        ):
            raise DeploymentError(
                "Backup verification failed: missing or unsafe data/configuration directory"
            )
        manifest_path = backup / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise DeploymentError("Backup verification failed: missing manifest")
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError, TypeError) as error:
            raise DeploymentError(
                "Backup verification failed: manifest is unreadable or invalid"
            ) from error
        if not isinstance(manifest, dict):
            raise DeploymentError("Backup verification failed: invalid manifest")
        expected = {}
        for name, digest in manifest.items():
            if (
                not isinstance(name, str)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise DeploymentError("Backup verification failed: manifest entry is invalid")
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise DeploymentError("Backup verification failed: unsafe manifest path")
            expected[name] = digest
        found = {}
        for current, directories, names in os.walk(
            backup, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            if _has_escape(backup, Path(current)):
                raise DeploymentError("Backup verification failed: unsafe directory path")
            for name in (*directories, *names):
                entry = Path(current) / name
                try:
                    info = os.lstat(entry)
                except OSError as error:
                    raise DeploymentError(
                        f"Backup verification failed: {describe_exception(error)}"
                    ) from error
                if stat.S_ISLNK(info.st_mode):
                    raise DeploymentError("Backup verification failed: unexpected symlink")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise DeploymentError("Backup verification failed: unexpected special file")
                relative = entry.relative_to(backup).as_posix()
                if relative == "manifest.json":
                    continue
                found[relative] = (entry, info.st_size)
        if set(found) != set(expected):
            missing = sorted(set(expected) - set(found))
            extra = sorted(set(found) - set(expected))
            detail = "; ".join(
                part
                for part in (
                    f"missing {missing[:3]}" if missing else "",
                    f"unmanifested {extra[:3]}" if extra else "",
                )
                if part
            )
            raise DeploymentError(
                "Backup verification failed: manifest coverage mismatch"
                + (f" ({detail})" if detail else "")
            )
        for name, digest in expected.items():
            entry, size = found[name]
            self.verification.hashed(size)
            if checksum(entry) != digest:
                raise DeploymentError(
                    f"Backup verification failed: content hash mismatch for {name}"
                )
        self.verification.put(backup, "complete")

    @staticmethod
    def _database_cache_key(database: Path) -> tuple[int, ...]:
        info = database.lstat()
        return (*file_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _verified_snapshot_schema(self, backup: Path) -> int | None:
        database = backup / "data/hnefatafl.sqlite3"
        if not os.path.lexists(database):
            return None
        key = self._database_cache_key(database)
        if key in self._snapshot_schema_cache:
            return self._snapshot_schema_cache[key]
        try:
            with closing(
                sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as saved:
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DeploymentError("Snapshot SQLite integrity check failed")
                schema = int(saved.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error as error:
            raise DeploymentError("Snapshot SQLite could not be read safely") from error
        self._snapshot_schema_cache[key] = schema
        return schema

    def _verify_snapshot_association(
        self,
        backup: Path,
        *,
        target: Path | None,
        expected: dict | None,
        allow_legacy: bool,
        schema: int | None,
        purpose: str = "pre-deployment",
    ) -> list[str]:
        association, valid, reason = self._load_metadata(
            backup / SNAPSHOT_METADATA, "snapshot metadata"
        )
        if not valid:
            raise DeploymentError(f"Invalid snapshot association metadata: {reason}")
        if association is not None and association.get("schema") == 2:
            self._validate_snapshot_association(association)
            if association["database_schema"] != schema:
                raise DeploymentError("Snapshot association records a different database schema")
            if target is not None:
                if association["release"] != str(target):
                    raise DeploymentError(
                        f"The snapshot was created for release {association['release']} and does not match the rollback directory"
                    )
                if association["purpose"] != purpose:
                    raise DeploymentError(
                        "The snapshot purpose is not suitable for the recorded deployment recovery"
                    )
                metadata = self._require_complete_release(target)
                if (
                    association["release_identity"] != list(self.fs.identity_of(target))
                    or association["version"] != metadata["version"]
                    or association["release_sha256"] != metadata.get("sha256")
                ):
                    raise DeploymentError(
                        "Snapshot release identity or verified archive digest disagrees with the rollback target"
                    )
                if expected is None:
                    raise DeploymentError(
                        "The recorded deployment is missing the expected snapshot identity"
                    )
            if expected is not None and association != expected:
                raise DeploymentError(
                    "Snapshot identity or deployment transaction does not match the recorded association"
                )
            return []
        if association is not None:
            # The interim ABI-2 updater wrote this exact shape, with a nullable
            # release/digest for pre-install snapshots and no transaction ID.
            fields = {
                "schema",
                "created_at",
                "version",
                "release",
                "release_sha256",
                "transaction",
                "database_schema",
                "updater_abi",
                "hnefatafl_data_dir",
            }
            if (
                set(association) - (fields | {"transaction_target"})
                or not fields <= set(association)
                or association.get("schema") != 1
                or association.get("updater_abi") != 2
                or association.get("hnefatafl_data_dir") != str(self.data)
                or type(association.get("created_at")) not in (float, int)
                or not 0 < association["created_at"] < 1e12
                or association.get("transaction") is not None
                or association.get("database_schema") != schema
                or association.get("release") is not None
                and not self._release_reference(association["release"])
                or association.get("version") is not None
                and (
                    not isinstance(association["version"], str)
                    or VERSION.fullmatch(association["version"]) is None
                )
                or association.get("release_sha256") is not None
                and (
                    not isinstance(association["release_sha256"], str)
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", association["release_sha256"])
                )
            ):
                raise DeploymentError("Malformed legacy snapshot association metadata")
            if target is not None:
                if association.get("release") not in (None, str(target)) or association.get(
                    "version"
                ) not in (None, self.release_version(target)):
                    raise DeploymentError("The legacy snapshot was created for a different release")
                if association.get("release_sha256") is not None and association[
                    "release_sha256"
                ] != self._require_complete_release(target).get("sha256"):
                    raise DeploymentError(
                        "The legacy snapshot archive digest disagrees with the rollback release"
                    )
        if expected is not None or not allow_legacy:
            raise DeploymentError(
                "Snapshot association metadata is missing for the recorded deployment"
            )
        return [
            "Snapshot uses legacy metadata. Using the verified artifact recorded by the root-owned deployment state."
        ]

    def _initial_install_association(self, backup: Path, journal: dict | None) -> dict | None:
        """Recognize a database-free initial snapshot bound to a durable installation."""
        if (
            journal is None
            or journal.get("operation") not in {"install", "automatic-recovery"}
            or journal.get("phase") not in {"migrating", "activating", "activated", "recovering"}
            or journal.get("migration_started") is not True
            or journal.get("previous") is not None
            or journal.get("prior_state") is not None
            or os.path.lexists(self.state)
            or journal.get("backup") != str(backup)
            or journal.get("snapshot_name") != backup.name
        ):
            return None
        recorded, valid, _ = self._load_metadata(self.journal, "transaction journal")
        if not valid or recorded != journal or not self.validate_transaction_metadata(journal)[0]:
            return None
        association = journal.get("backup_association")
        if (
            not isinstance(association, dict)
            or association.get("purpose") != "initial-install"
            or association.get("database_schema") is not None
            or association.get("release") != journal["target"]
            or association.get("snapshot") != journal.get("snapshot_id")
            or association.get("transaction") != journal["transaction"]
        ):
            return None
        active = self._require_active_state(None)
        if active is not None and str(active) != journal["target"]:
            return None
        return association

    @staticmethod
    def _has_sqlite_authority(directory: Path) -> bool:
        return any(
            os.path.lexists(directory / ("hnefatafl.sqlite3" + suffix))
            for suffix in ("", "-wal", "-shm", "-journal")
        )

    def verify_snapshot_usable(
        self,
        backup: Path,
        *,
        target: Path | None = None,
        expected: dict | None = None,
        allow_legacy: bool = True,
        check_compatibility: bool = True,
        service_state: str | None = "running",
        recovery_journal: dict | None = None,
    ) -> list[str]:
        """Verify integrity and the recorded snapshot association before downtime."""
        backup = Path(backup)
        if not self._backup_reference(str(backup)):
            raise DeploymentError(
                f"The recorded snapshot is outside the managed backup locations: {backup}"
            )
        location = self._backup_location_error(backup)
        if location:
            raise DeploymentError(f"The recorded snapshot cannot be trusted: {location}")
        try:
            self.verify_snapshot(backup)
            schema = self._verified_snapshot_schema(backup)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise DeploymentError(
                f"Refusing to interrupt the service: snapshot verification failed ({describe_exception(error)})"
            ) from error
        initial = self._initial_install_association(backup, recovery_journal)
        if initial is not None:
            if schema is not None or self._has_sqlite_authority(backup / "data"):
                raise DeploymentError(
                    "The initial snapshot is inconsistent with its recorded lack of a database"
                )
            warnings = self._verify_snapshot_association(
                backup,
                target=Path(initial["release"]),
                expected=initial,
                allow_legacy=False,
                schema=None,
                purpose="initial-install",
            )
        else:
            warnings = self._verify_snapshot_association(
                backup, target=target, expected=expected, allow_legacy=allow_legacy, schema=schema
            )
            if schema is None and self._has_sqlite_authority(self.data):
                raise DeploymentError(
                    "The live installation has a database but the snapshot has no regular SQLite database"
                )
        if check_compatibility and schema is not None:
            self._verify_restore_preflight(backup)
        self.check_storage(
            [self.restore_requirement(backup)],
            phase="rollback/restore preflight",
            service_state=service_state,
            persistent_data="unchanged until verification succeeds",
        )
        return warnings

    # ------------------------------------------------------------------
    # Manual backups
    # ------------------------------------------------------------------
    def backup(self) -> Path:
        """Create a verified manual snapshot with storage and recovery safeguards."""
        self.ensure_layout()
        self._assert_snapshot_sources_safe()
        self._require_idle_transaction("create a manual backup")
        if self._active_jobs():
            raise DeploymentError(
                "Active/queued training or tournament jobs exist. Cancel/finish jobs "
                "before creating a manual backup; the service was not stopped."
            )
        state, valid, reason = self._load_metadata(self.state, "installed state")
        if self.state.exists() and not valid:
            raise DeploymentError(
                f"Refusing a manual backup with invalid installed state: {reason}. "
                "The service was not stopped and no data was changed."
            )
        if state is not None:
            valid_state, reason = self.validate_state_metadata(state)
            if not valid_state:
                raise DeploymentError(
                    f"Refusing a manual backup with invalid installed state: {reason}. "
                    "The service was not stopped and no data was changed."
                )
        # Byte and inode checks run against the actual backup filesystem before
        # the service is interrupted; safe retention is attempted first.
        self._preflight_or_reclaim(
            self.backup_requirements,
            phase="manual backup",
            service_state="running",
            protected_releases=None,
        )
        created = None
        original: BaseException | None = None
        with self.write_gate():
            if self._active_jobs():
                raise DeploymentError(
                    "New training or tournament activity appeared while draining HTTP "
                    "mutations; the backup was refused and the service is still running."
                )
            # Size the snapshot with writers drained. After shutdown, recheck
            # free space on the same destinations without rescanning the tree.
            requirements = self.backup_requirements()
            self.check_storage(
                requirements,
                phase="manual backup",
                service_state="running",
            )
            transaction = {
                "operation": "manual-backup",
                "transaction": secrets.token_hex(16),
                "abi": UPDATER_ABI,
                "phase": "prepared",
                "service_was_running": self._service_is_active(),
                "snapshot_name": self._snapshot_name(),
                "snapshot_id": secrets.token_hex(16),
                "backup": None,
            }
            self.record(transaction)
            try:
                if transaction["service_was_running"]:
                    self.service("stop")
                self.check_storage(
                    requirements,
                    phase="manual backup after service shutdown",
                    service_state="stopped",
                )
                transaction["phase"] = "snapshot"
                self.record(transaction)
                created = self.snapshot(transaction=transaction)
                transaction["backup"] = str(created)
            except BaseException as error:
                original = error
                transaction["backup_error"] = describe_exception(error)
            transaction["phase"] = "recovering"
            # The original service state is already durable. Even if the disk is
            # full and this progress record fails, try to recover the service now.
            try:
                self.record(transaction)
            except BaseException as error:
                if original is None:
                    original = error
            recovery_error = self._restore_backup_service(transaction)
            if recovery_error is not None:
                outcome = (
                    "Manual backup failed: "
                    + describe_exception(original)
                    + "; automatic service recovery also failed: "
                    if original is not None
                    else f"Verified backup retained at {created}; automatic service recovery failed: "
                )
                raise DeploymentError(
                    outcome
                    + describe_exception(recovery_error)
                    + ". The previously verified backups were preserved. Run "
                    "hnefatafl-update --recover and inspect journalctl -u hnefatafl -n 200 --no-pager."
                ) from recovery_error
            if original is not None:
                outcome = (
                    "the service was restarted and verified"
                    if transaction["service_was_running"]
                    else "the service remained stopped"
                )
                raise DeploymentError(
                    "Manual backup failed: "
                    + describe_exception(original)
                    + f". The previously verified backups were preserved and {outcome}."
                ) from original
        assert created is not None
        self._post_operation_cleanup("manual backup", protected_backups={created})
        print(f"Verified backup: {created}")
        return created

    def _restore_backup_service(self, transaction: dict) -> BaseException | None:
        try:
            if transaction["service_was_running"]:
                self.service("start")
                self._recovery_probe()
            transaction.pop("service_recovery_error", None)
            transaction["phase"] = (
                "committed"
                if transaction.get("backup") and not transaction.get("backup_error")
                else "recovered"
            )
            self.record(transaction)
        except BaseException as error:
            transaction["phase"] = "recovering"
            transaction["service_recovery_error"] = describe_exception(error)
            try:
                self.record(transaction)
            except (OSError, JournalWriteError, KeyboardInterrupt):
                pass
            return error
        return None

    def _require_idle_transaction(self, action: str) -> None:
        transaction, readable, reason = self._load_metadata(self.journal, "transaction journal")
        valid = readable
        if transaction is not None:
            valid, reason = self.validate_transaction_metadata(transaction)
        if (
            not valid
            or transaction is not None
            and (
                transaction["phase"] not in {"committed", "recovered"} or transaction.get("restore")
            )
        ):
            raise DeploymentError(
                f"Cannot {action} before resolving the deployment journal. "
                f"Run hnefatafl-update --recover. {reason}"
            )

    # ------------------------------------------------------------------
    # Restore transaction
    # ------------------------------------------------------------------
    def _restore_marker(self, directory: Path) -> dict | None:
        value, valid, _reason = self._load_metadata(
            Path(directory) / RESTORE_MARKER, "restore staging marker"
        )
        if not valid or value is None:
            return None
        transaction = value.get("transaction")
        if not isinstance(transaction, str) or not re.fullmatch(r"[0-9a-f]{8,32}", transaction):
            return None
        return value

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

    @contextmanager
    def _private_live_database(self, live: Path, parent: Path):
        with tempfile.TemporaryDirectory(dir=parent, prefix=".live-authority-") as work:
            private = Path(work) / live.name
            root = self._data_directory(live.parent)
            with self.fs.open_directory(root) as directory:
                for suffix in ("", "-wal", "-journal"):
                    name = live.name + suffix
                    try:
                        pinned = os.open(
                            name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
                        )
                    except FileNotFoundError:
                        if not suffix:
                            raise DeploymentError(
                                "The live database disappeared during restore"
                            ) from None
                        continue
                    try:
                        info = os.fstat(pinned)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_dev != root.device
                            or info.st_uid not in {self._data_owner(), self.privileged_uid}
                        ):
                            raise DeploymentError(
                                f"Unsafe live database entry: {live.parent / name}"
                            )
                        fd = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_CLOEXEC)
                        with os.fdopen(fd, "rb") as source, (Path(work) / name).open("xb") as copy:
                            shutil.copyfileobj(source, copy, length=MIB)
                    finally:
                        os.close(pinned)
            yield private

    def _prepare_restore_database(self, live_db: Path, copy_db: Path) -> None:
        support = self._load_online_support()
        # Restoring game/training data must not resurrect logged-out sessions or
        # undo password, role, and status changes made since the snapshot.
        if live_db.exists() and not copy_db.exists():
            raise DeploymentError(
                "Snapshot has no database; manual recovery is required before replacing live authority"
            )
        if live_db.exists() and copy_db.exists():
            with (
                self._private_live_database(live_db, copy_db.parent) as private,
                closing(sqlite3.connect(private)) as live,
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
                try:
                    support.retain_online_on_restore(live, copy)
                except ValueError as error:
                    raise DeploymentError(str(error)) from error
                copy.commit()
        elif copy_db.exists():
            # Disaster recovery without a live DB cannot recover post-snapshot
            # progress. It must still revoke saved capabilities and pause clocks.
            with closing(sqlite3.connect(copy_db)) as copy:
                self.revoke_restore_capabilities(copy)
                try:
                    support.pause_online_after_restore(
                        copy,
                        "Snapshot-only recovery; timeout finalization paused for operator reconciliation.",
                    )
                except ValueError as error:
                    raise DeploymentError(str(error)) from error
                copy.commit()

    def _validate_restore_topology(self) -> None:
        if self.fs.is_mount(self.data) or self.fs.nested_mounts(self.data):
            raise DeploymentError(
                f"Automatic restore requires an unmounted persistent data directory with no nested mounts: {self.data}. "
                "Preserve the snapshot and use a reviewed manual restore for mounted data."
            )
        self.fs._validate_ancestors(self.data, "persistent data directory")
        if os.path.lexists(self.data):
            self._data_directory(self.data, staging=True)

    def restore(
        self, backup: Path, *, journal: dict | None = None, transaction_id: str | None = None
    ) -> Path:
        """Stage data privately and transfer the held gate before publication."""
        if self._held_gate is None:
            with self.write_gate():
                return self.restore(backup, journal=journal, transaction_id=transaction_id)
        self._validate_restore_topology()
        backup = Path(backup)
        self.ensure_layout()
        initial = self._initial_install_association(backup, journal)
        if initial is not None and (journal is None or not journal["service_stopped"]):
            raise DeploymentError(
                "Confirm the failed installation service has stopped before recovering its data"
            )
        self.verify_snapshot_usable(
            backup, service_state="stopped", check_compatibility=False, recovery_journal=journal
        )
        token = transaction_id or secrets.token_hex(8)
        staging = self.data.with_name(".restore-" + token)
        retained = self.data.with_name(".retained-" + token)
        if os.path.lexists(staging) or os.path.lexists(retained):
            raise DeploymentError(
                f"Restore paths already exist for {token}. Run hnefatafl-update --recover."
            )
        original = self._data_directory(self.data)
        restore = {
            "schema": 2,
            "phase": "prepared",
            "transaction": token,
            "backup": str(backup),
            "data": str(self.data),
            "staging": str(staging),
            "retained": str(retained),
            "original_identity": [original.device, original.inode],
            "staging_identity": None,
            "gate_identity": list(self._held_gate),
        }
        self._record_restore(journal, restore, "prepared")
        self._begin_staging(staging, "restore")
        atomic_json(
            staging / RESTORE_MARKER,
            {
                "schema": 2,
                "transaction": token,
                "backup": str(backup),
                "created_at": time.time(),
            },
        )
        shutil.copytree(
            backup / "data",
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(GATE_NAME, RESTORE_MARKER),
        )
        if initial is None:
            self._prepare_restore_database(
                self.data / "hnefatafl.sqlite3", staging / "hnefatafl.sqlite3"
            )
        elif self._has_sqlite_authority(staging):
            raise DeploymentError("SQLite files appeared in the database-free recovery candidate")
        self._prepare_staged_ownership(staging)
        sync_tree(staging)
        sync_directory(staging.parent)
        restore["staging_identity"] = list(self.fs.identity_of(staging))
        self._record_restore(journal, restore, "staged")
        self._publish_restored_data(journal, restore)
        if journal is None:
            (self.data / RESTORE_MARKER).unlink(missing_ok=True)
            sync_directory(self.data)
        print(f"Restored pre-deployment state. Newer data retained at {retained}.")
        if initial is not None:
            print(
                "Any accounts provisioned during the failed installation are in the retained data."
            )
        return retained

    def _record_restore(self, journal: dict | None, restore: dict, phase: str) -> None:
        restore["phase"] = phase
        if journal is not None:
            journal["restore"] = dict(restore)
            self.record(journal)

    def _prepare_staged_ownership(self, staging: Path) -> None:
        uid, gid = self._data_account()
        if self.privileged_uid == uid and os.getegid() == gid:
            return
        # The containing directory stays private until the root inode has been
        # published. Every child is copied from a verified snapshot.
        for directory, subdirs, files, fd in os.fwalk(
            staging, topdown=False, follow_symlinks=False
        ):
            for name in (*subdirs, *files):
                if Path(directory) == staging and name == RESTORE_MARKER:
                    continue
                os.chown(name, uid, gid, dir_fd=fd, follow_symlinks=False)

    def _move_gate(self, source: RootIdentity, destination: RootIdentity) -> None:
        if self._held_gate is None:
            raise DeploymentError(
                "Restore cannot move the deployment gate without holding its lock"
            )
        with self.fs.open_directory(source) as src, self.fs.open_directory(destination) as dst:
            if self._entry_identity(dst, GATE_NAME) is not None:
                raise DeploymentError("The restore candidate already has a deployment gate")
            if self._entry_identity(src, GATE_NAME) != list(self._held_gate):
                raise DeploymentError("The locked deployment gate changed during restore")
            os.rename(GATE_NAME, GATE_NAME, src_dir_fd=src, dst_dir_fd=dst)
            if self._entry_identity(dst, GATE_NAME) != list(self._held_gate):
                raise DeploymentError(
                    "The restore candidate did not receive the locked deployment gate"
                )
            os.fsync(src)
            os.fsync(dst)

    def _publish_restored_data(self, journal: dict | None, restore: dict) -> None:
        data, staging, retained = self.data, Path(restore["staging"]), Path(restore["retained"])
        physical = {
            path: self._data_directory(path, staging=True)
            for path in (data, staging, retained)
            if os.path.lexists(path)
        }

        def identity(path):
            root = physical.get(path)
            return [root.device, root.inode] if root else None

        original, candidate = restore["original_identity"], restore["staging_identity"]
        if (
            identity(data) == original
            and identity(staging) == candidate
            and retained not in physical
        ):
            self._record_restore(journal, restore, "stopping")
            os.rename(data, retained)
            sync_directory(data.parent)
            physical[retained] = self._data_directory(retained, staging=True)
            del physical[data]
            self._record_restore(journal, restore, "data-renamed")
        if (
            data not in physical
            and identity(staging) == candidate
            and identity(retained) == original
        ):
            with self.fs.open_directory(physical[staging]) as fd:
                staged_gate = self._entry_identity(fd, GATE_NAME)
            if staged_gate is None:
                self._move_gate(physical[retained], physical[staging])
            elif staged_gate != restore["gate_identity"]:
                raise DeploymentError(
                    "The restore candidate has an unexpected deployment gate inode"
                )
            self._record_restore(journal, restore, "gate-moved")
            os.rename(staging, data)
            sync_directory(data.parent)
            self._finish_staging(staging)
            physical[data] = self._data_directory(data, staging=True)
            del physical[staging]
            self._record_restore(journal, restore, "published")
        if identity(data) != candidate or identity(retained) != original or staging in physical:
            raise DeploymentError(
                "Cannot identify the authoritative restored data directory. Preserve the journal and retained data."
            )
        uid, gid = self._data_account()
        with self.fs.open_directory(physical[data]) as fd:
            info = os.fstat(fd)
            if self._entry_identity(fd, GATE_NAME) != restore["gate_identity"]:
                raise DeploymentError(
                    "The authoritative data directory is missing the locked deployment gate"
                )
            if (info.st_uid, info.st_gid) != (uid, gid):
                os.fchown(fd, uid, gid)
            os.fsync(fd)
        if restore["phase"] not in {"ownership-set", "activated", "started", "verified"}:
            self._record_restore(journal, restore, "ownership-set")

    def recover_restore(self, journal: dict) -> bool:
        """Reconcile the journal with directory and gate identities on disk."""
        restore = journal["restore"]
        if restore.get("schema") != 2:
            restore = self._upgrade_legacy_restore(restore)
            if restore is None:
                journal["restore"] = None
                self.record(journal)
                return False
            self._record_restore(journal, restore, restore["phase"])
        staging = Path(restore["staging"])
        retained = Path(restore["retained"])
        if restore["phase"] == "prepared":
            root = self._data_directory(self.data)
            if [root.device, root.inode] != restore["original_identity"] or os.path.lexists(
                retained
            ):
                raise DeploymentError(
                    "The original data directory changed before restore staging completed"
                )
            if os.path.lexists(staging):
                if os.path.lexists(staging / GATE_NAME):
                    raise DeploymentError(
                        "Unfinished restore staging holds a deployment gate and has been preserved"
                    )
                if os.path.lexists(self._staging_intent(staging)):
                    self._discard_staging(staging, mixed=True)
                else:
                    self._safe_remove(
                        staging.parent, staging, identity=self.fs.identity_of(staging)
                    )
            journal["restore"] = None
            self.record(journal)
            return False
        self._publish_restored_data(journal, restore)
        return True

    def _upgrade_legacy_restore(self, restore: dict) -> dict | None:
        """Read legacy restore markers once to establish durable identities."""
        if not restore.get("staging") or not restore.get("retained"):
            return None  # The old updater recorded preflight before allocating paths.
        staging, retained = Path(restore["staging"]), Path(restore["retained"])
        original = retained if os.path.lexists(retained) else self.data
        candidate = self.data if original == retained and os.path.lexists(self.data) else staging
        if restore["phase"] == "prepared" and not os.path.lexists(candidate):
            return None
        marker = self._restore_marker(candidate)
        if marker is None or marker.get("transaction") != restore["transaction"]:
            raise DeploymentError(
                "The legacy restore has no matching candidate marker. Preserve all data directories for reviewed recovery."
            )
        for path in (original, candidate):
            self._data_directory(path, staging=True)
        return {
            **restore,
            "schema": 2,
            "original_identity": list(self.fs.identity_of(original)),
            "staging_identity": list(self.fs.identity_of(candidate))
            if restore["phase"] != "prepared"
            else None,
            "gate_identity": list(self._held_gate or ()),
        }

    def finalize_restore(self, journal: dict) -> None:
        restore = journal.get("restore")
        if restore is None:
            return
        if journal.get("phase") not in {"committed", "recovered"}:
            raise DeploymentError("Cannot finalize a restore before the transaction is durable")
        root = self._data_directory(self.data)
        if restore.get("staging_identity") != [root.device, root.inode]:
            raise DeploymentError(
                "The committed restore data directory changed before finalization"
            )
        with self.fs.open_directory(root) as fd:
            if self._entry_identity(fd, GATE_NAME) != restore["gate_identity"]:
                raise DeploymentError("The committed restore is missing its deployment gate")
            # Unlink only the reserved entry in the validated directory. The
            # journal remains authoritative even if the marker is already gone.
            try:
                os.unlink(RESTORE_MARKER, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.fsync(fd)
        journal["restore"] = None
        self.record(journal)

    def activate(self, release: Path):
        pending = self.base / "current.new"
        pending.unlink(missing_ok=True)
        pending.symlink_to(release)
        os.replace(pending, self.current)
        sync_directory(self.base)

    def _unit_content(self, release: Path) -> bytes:
        authenticated = release / ".trusted-unit"
        unit = (
            authenticated
            if os.path.lexists(authenticated)
            else release / "deployment/hnefatafl.service"
        )
        self.fs._validate_ancestors(self.unit_file, "systemd service unit")
        if os.path.lexists(self.unit_file):
            self._utility_file(self.unit_file)
        return self._utility_file(unit).read_bytes()

    def _install_release_unit(self, release: Path) -> None:
        atomic_bytes(self.unit_file, self._unit_content(release), 0o644)

    def _enable_service_start_guard(self) -> None:
        if not self.manage_updater:
            return
        drop_in = self.unit_file.with_name(self.unit_file.name + ".d")
        self._managed_root(
            drop_in,
            owner=self.privileged_uid,
            mode=0o755,
            description="systemd service drop-in",
            write=True,
        )
        content = (
            "[Service]\n"
            "StateDirectory=\n"
            "ExecStartPre=+/usr/bin/python3 -I -B /usr/local/lib/hnefatafl/deployment.py --guard-data-root\n"
        ).encode()
        path = drop_in / "50-data-authority.conf"
        if not os.path.lexists(path) or self._utility_file(path).read_bytes() != content:
            atomic_bytes(path, content, 0o644)
        self.run(["systemctl", "daemon-reload"])

    def _unchanged_previous_release(self, journal: dict, root: RootIdentity) -> Path | None:
        if (
            "previous_data_identity" not in journal
            or journal["phase"] not in {"prepared", "snapshot", "recovering"}
            or journal["migration_started"]
            or journal["data_modified"]
            or journal.get("restore") is not None
        ):
            return None
        installed = self._installed_metadata()
        previous = self._require_active_state(installed)
        if (
            installed is None
            or installed != journal.get("prior_state")
            or previous is None
            or str(previous) != journal.get("previous")
            or (root.device, root.inode) != tuple(journal["previous_data_identity"])
            or self.fs.identity_of(previous) != tuple(journal["previous_release_identity"])
        ):
            raise DeploymentError(
                "Cannot prove the previous release and live data are unchanged. "
                f"Inspect {self.journal}, {self.state}, and {self.data} before restarting."
            )
        return previous

    @contextmanager
    def _open_unchanged_service_journal(self, journal: dict):
        root = self.fs.validate_root(
            self.etc, owner=self.privileged_uid, description="configuration directory"
        )
        with self.fs.open_directory(root) as directory:
            fd = os.open(
                self.journal.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory,
            )
            with os.fdopen(fd, encoding="utf-8") as handle:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.privileged_uid
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                    or json.load(handle, object_pairs_hook=unique_metadata_fields) != journal
                    or file_identity(info) != self.fs.identity_of(self.journal)
                ):
                    raise DeploymentError("The deployment journal changed during service recovery")
                yield fd

    def _authorize_service_start(self) -> None:
        root = self._data_directory(self.data)
        journal, valid, reason = self._load_metadata(self.journal, "transaction journal")
        if valid and journal is not None:
            valid, reason = self.validate_transaction_metadata(journal)
        if not valid:
            raise DeploymentError(f"Cannot verify data authority for startup: {reason}")
        if journal is None or journal["phase"] in {"committed", "recovered"}:
            if self._require_active_state(self._installed_metadata()) is None:
                raise DeploymentError("Service startup is deferred until a release is installed")
        elif journal.get("operation") == "manual-backup" and journal["service_was_running"]:
            self._require_active_state(self._installed_metadata())
        else:
            current = self._require_active_state(None)
            allowed = (
                journal.get("target")
                if journal.get("operation") in {"install", "update"}
                and journal["phase"] in {"activated", "committing"}
                else journal.get("service_target")
            )
            if current is not None and str(current) != allowed:
                if self._unchanged_previous_release(journal, root) == current:
                    with self._open_unchanged_service_journal(journal) as descriptor:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        except BlockingIOError:
                            # Only the updater's scoped restart of the proven
                            # previous service holds this lock.
                            allowed = str(current)
            if current is None or str(current) != allowed:
                raise DeploymentError(
                    "Service startup is deferred until the interrupted deployment is recovered"
                )
        if (
            journal
            and journal.get("restore")
            and journal["restore"].get("staging_identity") != [root.device, root.inode]
        ):
            raise DeploymentError(
                "Service startup is deferred because the authoritative restored data is unavailable"
            )

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

    def _verify_release_environment(
        self, target: Path, environment: dict[str, str] | None = None
    ) -> None:
        """Verify the frozen environment and entry points at their permanent path."""
        if environment is None:
            workspace = self._temporary_workspace("verify")
            try:
                self._verify_release_environment(target, self._preparation_environment(workspace))
            finally:
                self._clear_preparation_workspace(workspace)
            return
        target = Path(target)
        python = target / ".venv/bin/python"
        if python.is_symlink():
            resolved = python.resolve()
            if not resolved.is_file():
                raise DeploymentError(
                    f"Prepared release virtual environment python is a dangling symlink: {python}"
                )
        elif not python.is_file():
            raise DeploymentError(
                f"Prepared release is missing its virtual environment python: {python}"
            )
        venv_bin = target / ".venv/bin"
        if venv_bin.is_dir():
            for entry in sorted(venv_bin.iterdir()):
                if entry.is_symlink() or not entry.is_file():
                    continue
                try:
                    with entry.open("rb") as handle:
                        first = handle.readline(512).decode("utf-8", "replace").strip()
                except OSError:
                    continue
                if not first.startswith("#!"):
                    continue
                if "python" not in first:
                    continue
                expected = str(python)
                if expected not in first and str(target) not in first:
                    raise DeploymentError(
                        f"Entry point {entry.name} points outside the permanent release "
                        f"path ({first}); a relocated virtual environment would be broken"
                    )
        checks = [
            (
                [python, "-c", "import hnefatafl, sys; assert sys.prefix"],
                "Python import and virtual environment prefix",
            ),
            (
                [python, "-c", "import hnefatafl.accounts, hnefatafl.server"],
                "database migration and service imports",
            ),
            ([python, "-m", "uvicorn", "--version"], "uvicorn entry point"),
            (
                [python, "-m", "hnefatafl.accounts", "migrate", "--help"],
                "database migration command",
            ),
        ]
        console = target / ".venv/bin/hnefatafl"
        if console.is_file() and not console.is_symlink():
            checks.append(([console, "--help"], "generated Hnefatafl console entry point"))
        for command, description in checks:
            try:
                # Production runs the frozen environment as the application user;
                # disposable layouts already run as their own unprivileged account.
                self.run(command, cwd=target, user=True, extra_env=environment, preparation=True)
            except (OSError, subprocess.CalledProcessError) as error:
                raise DeploymentError(
                    f"Prepared release verification failed for {description}: "
                    f"{describe_exception(error)}. The release was not published."
                ) from error
        activation = target / ".venv/bin/activate"
        if activation.is_file():
            content = activation.read_text(errors="replace")
            if str(target) not in content:
                print(
                    "Prepared release note: .venv/bin/activate does not reference the "
                    "release path; generated entry points were verified directly.",
                    file=sys.stderr,
                )

    def _grant_build_access(self, target: Path) -> None:
        uid, gid = self._data_account()
        if uid == self.privileged_uid:
            return
        for _, _, files, fd in os.fwalk(target, topdown=False, follow_symlinks=False):
            for name in files:
                os.chown(name, uid, gid, dir_fd=fd, follow_symlinks=False)
            os.fchown(fd, uid, gid)

    def _seal_release(self, target: Path, identity: tuple[int, int]) -> None:
        uid, _ = self._data_account()
        if uid == self.privileged_uid:
            return
        if (
            self.fs.is_mount(target)
            or self.fs.nested_mounts(target)
            or self.fs.identity_of(target) != identity
        ):
            raise DeploymentError(
                "The prepared release changed identity or mount topology during build"
            )

        def failed(error):
            raise error

        def seal_file(pinned: int, info: os.stat_result, expected_links: int, name: str) -> None:
            file_fd = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fchown(file_fd, self.privileged_uid, os.getegid())
                if os.fstat(file_fd).st_nlink != expected_links:
                    raise DeploymentError(f"A prepared file gained or lost links: {name}")
                os.fchmod(file_fd, 0o555 if info.st_mode & 0o111 else 0o444)
            finally:
                os.close(file_fd)

        # Lock down every containing directory before validating the hardlinks
        # created by npm post-install scripts. Virtualenv symlink targets are
        # never accessed here.
        hardlinks: dict[tuple[int, int], list[Path]] = {}
        dependency_root = target / "frontend/node_modules"
        for directory, directories, files, fd in os.fwalk(
            target, topdown=True, follow_symlinks=False, onerror=failed
        ):
            info = os.fstat(fd)
            if info.st_dev != identity[0] or info.st_uid not in {uid, self.privileged_uid}:
                raise DeploymentError("Unsafe directory in the prepared release")
            os.fchown(fd, self.privileged_uid, os.getegid())
            os.fchmod(fd, 0o755)
            for name in (*directories, *files):
                pinned = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    entry = os.fstat(pinned)
                    if stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode):
                        continue
                    if (
                        not stat.S_ISREG(entry.st_mode)
                        or entry.st_dev != identity[0]
                        or entry.st_uid not in {uid, self.privileged_uid}
                    ):
                        raise DeploymentError(f"Unsafe file in the prepared release: {name}")
                    path = Path(directory) / name
                    if entry.st_nlink == 1:
                        seal_file(pinned, entry, 1, name)
                    elif path.is_relative_to(dependency_root):
                        hardlinks.setdefault(file_identity(entry), []).append(path)
                    else:
                        raise DeploymentError(f"Unsafe hardlink in the prepared release: {path}")
                finally:
                    os.close(pinned)
        for identity_key, paths in hardlinks.items():
            path = paths[0]
            pinned = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                entry = os.fstat(pinned)
                if (
                    entry.st_nlink != len(paths)
                    or file_identity(entry) != identity_key
                    or any(file_identity(item.lstat()) != identity_key for item in paths[1:])
                ):
                    raise DeploymentError(
                        f"Unsafe external hardlink in the prepared release: {path}"
                    )
                seal_file(pinned, entry, len(paths), str(path))
            finally:
                os.close(pinned)

    @staticmethod
    def _archive_has_launcher(archive: Path) -> bool:
        with tarfile.open(archive, "r:gz") as bundle:
            return any(
                member.name.removeprefix("./") == "scripts/linux/launcher.py" for member in bundle
            )

    def _use_installed_updater(self, tag: str) -> bool:
        return version_key(tag) <= (0, 42, 0) and self._active_updater() is not None

    def prepare(self, github: GitHub, release: dict) -> Path:
        self._base_root()
        self._release_root(write=True)
        self.ensure_layout()
        self._remove_orphan_staging()
        tag = release["tag_name"]
        version_key(tag)
        archive_size = self._archive_size(release)
        self.preflight_prepare(archive_size)
        token = secrets.token_hex(8)
        # The environment is prepared at its permanent path so every generated
        # shebang and pyvenv.cfg path stays valid. Publication is the atomic
        # creation of the completion marker, not a directory rename.
        final = self.releases / (tag + "-" + token[:8])
        own_identity = self._begin_staging(final, "release", 0o755, preparation_id=token)
        published = False
        units_settled = False
        refresh_utility = self.manage_updater
        workspace = None
        pending = None
        try:
            workspace = self._temporary_workspace("prepare", preparation_id=token)
            environment = self._preparation_environment(workspace)
            archive = workspace / "release.tar.gz"
            sha256 = github.download(release, archive)
            if self.manage_updater:
                if self._archive_has_launcher(archive):
                    pending = self._stage_updater_payload(
                        archive, tag, sha256, preparation_id=token
                    )
                elif self._use_installed_updater(tag):
                    refresh_utility = False
                else:
                    raise DeploymentError("The verified archive is missing its deployment launcher")
            unit = self._read_archive_members(archive, {"unit": "deployment/hnefatafl.service"})
            extract(archive, final)
            archive.unlink()
            for required in (
                "pyproject.toml",
                "uv.lock",
                "frontend/package-lock.json",
                "hnefatafl/auth.py",
                "tests/test_security.py",
                "deployment/hnefatafl.service",
            ):
                if not (final / required).is_file():
                    raise ValueError(f"Unsupported release: missing {required}")
            metadata = tomllib.loads((final / "pyproject.toml").read_text())
            if metadata["project"]["version"] != tag:
                raise ValueError("Release tag and package version disagree")
            self._grant_build_access(final)
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
                cwd=final,
                user=True,
                extra_env=environment,
                preparation=True,
            )
            self.run(
                ["npm", "--prefix", "frontend", "ci"],
                cwd=final,
                user=True,
                extra_env=environment,
                preparation=True,
            )
            self.run(
                ["npm", "--prefix", "frontend", "run", "build"],
                cwd=final,
                user=True,
                extra_env=environment,
                preparation=True,
            )
            self.run(
                [
                    final / ".venv/bin/python",
                    "-m",
                    "pytest",
                    "tests/test_security.py",
                    "-q",
                ],
                cwd=final,
                user=True,
                extra_env=environment,
                preparation=True,
            )
            temporary_build = scan_usage(workspace)
            self._seal_release(final, own_identity)
            peak = scan_usage(final)
            self._prune_build_outputs(final)
            size = scan_usage(final)
            self._release_size_cache = {}
            atomic_bytes(final / ".trusted-unit", unit["deployment/hnefatafl.service"], 0o444)
            self._verify_release_environment(final, environment)
            self._stop_staging_units(workspace)
            units_settled = True
            temporary_verification = scan_usage(workspace)
            release_metadata = {
                "version": tag,
                "sha256": sha256,
                "prepared_at": time.time(),
                "peak_bytes": peak.bytes,
                "peak_inodes": peak.inodes,
                "temporary_peak_bytes": max(temporary_build.bytes, temporary_verification.bytes),
                "temporary_peak_inodes": max(temporary_build.inodes, temporary_verification.inodes),
                "size_bytes": size.bytes,
                "size_inodes": size.inodes,
                "file_count": size.files,
                "updater_abi": UPDATER_ABI,
                "refresh_utility": refresh_utility,
            }
            # Persist the sealed tree before making its completion marker visible.
            sync_tree(final, allow_symlinks=True)
            atomic_bytes(
                final / RELEASE_MARKER,
                (json.dumps(release_metadata, indent=2) + "\n").encode(),
                0o644,
            )
            published = True
            self._finish_staging(final)
        except BaseException:
            try:
                if workspace is not None and not units_settled:
                    self._stop_staging_units(workspace)
                    units_settled = True
                if not published:
                    self._discard_staging(final, mixed=True)
                    if pending is not None:
                        self._remove_pending_updater(pending)
            except (OSError, DeploymentError, subprocess.CalledProcessError) as cleanup_error:
                print(
                    "Failed release cleanup warning: "
                    f"{describe_exception(cleanup_error)}. The original preparation "
                    "failure follows.",
                    file=sys.stderr,
                )
            raise
        finally:
            if workspace is not None and units_settled:
                try:
                    self._discard_staging(workspace, mixed=True)
                except (OSError, DeploymentError, subprocess.CalledProcessError) as error:
                    print(
                        f"Preparation directory cleanup warning: {describe_exception(error)}",
                        file=sys.stderr,
                    )
        try:
            descriptor = os.open(self.releases, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        print(
            f"Prepared release {tag}: {format_bytes(size.bytes)} on disk; "
            f"peak preparation usage was {format_bytes(peak.bytes)} "
            f"({peak.inodes} inodes)."
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
        token = secrets.token_urlsafe(32)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        rows = self._application_queries(
            [
                (
                    "INSERT INTO sessions SELECT ?,id,?,? FROM accounts WHERE active=1 AND role='ADMIN' LIMIT 1 RETURNING token_hash",
                    (hashed, time.time() + 60, hashlib.sha256(b"local-probe").hexdigest()),
                ),
            ],
            write=True,
        )
        if not rows[0]:
            raise self._readiness_error(
                "administrator", "no_active_administrator", attempts=1, elapsed=0.0, status=200
            )
        try:
            self._probe_authentication(url, host, token)
            self._probe_anonymous_rejection(url, host)
        finally:
            self._application_queries(
                [("DELETE FROM sessions WHERE token_hash=?", (hashed,))], write=True
            )

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
        tables = ("accounts", "auth_meta", "account_limits")
        checks = ["PRAGMA user_version", *(f"PRAGMA table_info({table})" for table in tables)]
        try:
            live_results = self._application_queries([(sql, ()) for sql in checks])
            with closing(
                sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as saved:
                snapshot_results = [saved.execute(sql).fetchall() for sql in checks]
        except (sqlite3.Error, DeploymentError) as error:
            raise DeploymentError(
                "Snapshot compatibility could not be read safely; manual recovery is required."
            ) from error
        live_version = live_results[0][0][0]
        snapshot_version = snapshot_results[0][0][0]
        if max(live_version, snapshot_version) >= 10 and live_version != snapshot_version:
            raise DeploymentError(
                f"Snapshot schema {snapshot_version} differs from live schema {live_version}; "
                "automatic rollback would be refused by the online schema-authority checks. "
                "Preserve the snapshot and inspect both databases before manual recovery."
            )
        for current, saved_columns in zip(live_results[1:], snapshot_results[1:], strict=True):
            if saved_columns and [row[1] for row in current] != [row[1] for row in saved_columns]:
                raise DeploymentError(
                    "Account schema changed; manual recovery is required before restoring security state."
                )

    @staticmethod
    def _safe_failure_detail(error: BaseException) -> str:
        return describe_exception(error)

    def deploy(self, target: Path, *, initialize=False):
        self._deployment_transaction_started = False
        self._deployment_recovery_completed = False
        try:
            self._require_idle_transaction("start deployment")
            self.ensure_layout()
            self.recover_updater_refresh()
            self._remove_orphan_staging()
            self.preflight_deployment(Path(target), service_state="running")
            if self.manage_updater:
                self._refresh_installed_updater(target)
                self._enable_service_start_guard()
            with self.write_gate():
                self._deploy_locked(target, initialize=initialize)
        except BaseException:
            if not getattr(self, "_deployment_transaction_started", False):
                self._cleanup_refused_deployment(target)
            elif self._deployment_recovery_completed:
                self._post_operation_cleanup("deployment recovery")
            raise
        self._post_operation_cleanup("deployment")

    def _cleanup_refused_deployment(self, target: Path) -> None:
        """Trim a successfully prepared release that was never deployed.

        The target is not protected here: a refused deployment leaves an unused
        release that must not accumulate indefinitely. The active and
        rollback-required artifacts remain protected by the normal rules.
        """
        try:
            report = self.cleanup(dry_run=False, automatic=True)
        except BaseException as error:
            print(
                "Cleanup warning after refused deployment: "
                f"{describe_exception(error)}. The prepared release remains on disk; "
                "run hnefatafl-update --cleanup --dry-run to inspect it.",
                file=sys.stderr,
            )
            return
        if report.removed:
            print(
                f"Removed {len(report.removed)} unused artifact(s) after the refused "
                f"deployment; actually reclaimed {format_bytes(report.reclaimed_bytes)}."
            )
        if report.exception_reason:
            print(f"Retention note: {report.exception_reason}")
        if (
            not any(entry.path == Path(target) for entry in report.removed)
            and Path(target).exists()
        ):
            print(
                f"Prepared release retained for inspection: {target}. "
                "It is not the active release and may be removed after review."
            )

    def _require_complete_release(self, target: Path) -> dict:
        target = Path(target)
        try:
            relative = target.relative_to(self.releases)
        except ValueError as error:
            raise DeploymentError(
                f"Refusing to deploy {target}: it is not inside the managed release "
                f"directory {self.releases}"
            ) from error
        if len(relative.parts) != 1:
            raise DeploymentError(
                f"Refusing to deploy {target}: only a direct child of the release "
                "directory can be activated"
            )
        self.fs.validate_root(target, owner=self.privileged_uid, description="completed release")
        metadata, valid, reason = self._load_metadata(target / RELEASE_MARKER, "release metadata")
        if not valid or metadata is None:
            raise DeploymentError(
                f"Refusing to deploy {target}: {reason or 'release is not complete'}"
            )
        if str(metadata.get("version")) != relative.name.split("-")[0]:
            raise DeploymentError(
                f"Refusing to deploy {target}: release metadata version disagrees "
                "with the directory name"
            )
        return metadata

    def _deploy_locked(self, target: Path, *, initialize=False):
        self._diagnostic_failure_phase = None
        self._diagnostic_recovery_target = None
        if self._active_jobs():
            raise ValueError(
                "Active/queued training or tournament jobs exist. Cancel/finish jobs and pause remote workers first."
            )
        self.validate_configuration(target)
        old_state, state_valid, state_reason = self._load_metadata(self.state, "installed state")
        if self.state.exists() and not state_valid:
            raise DeploymentError(
                f"Refusing to deploy with invalid installed state: {state_reason}. "
                "The service was not stopped and persistent data is unchanged. "
                "Inspect /etc/hnefatafl before continuing."
            )
        if old_state is not None:
            valid_state, state_reason = self.validate_state_metadata(old_state)
            if not valid_state:
                raise DeploymentError(
                    f"Refusing to deploy with ambiguous installed state: {state_reason}. "
                    "The service was not stopped and persistent data is unchanged. "
                    "Inspect /etc/hnefatafl before continuing."
                )
        old = self._require_active_state(old_state)
        self._require_complete_release(target)
        self._unit_content(target)
        self._assert_snapshot_sources_safe()
        self._validate_restore_topology()
        # Refuse predictable storage failures while the application is still
        # running. The target is explicitly protected from storage cleanup.
        requirements = self._deployment_requirements()
        self.check_storage(
            requirements,
            phase="pre-deployment snapshot",
            service_state="running",
            protected_releases={Path(target)},
        )
        transaction: dict = {
            "phase": "prepared",
            "abi": UPDATER_ABI,
            "operation": "update" if old else "install",
            "transaction": secrets.token_hex(16),
            "snapshot_name": self._snapshot_name(),
            "snapshot_id": secrets.token_hex(16),
            "previous": str(old) if old else None,
            "target": str(target),
            "backup": None,
            "prior_state": old_state,
            "service_stopped": False,
            "migration_started": False,
            "data_modified": False,
            "restore": None,
        }
        if old is not None and old_state is not None:
            root = self._data_directory(self.data)
            transaction["previous_data_identity"] = [root.device, root.inode]
            transaction["previous_release_identity"] = list(self.fs.identity_of(old))
        self._snapshot_association(transaction, None)
        self._reserve_journal_space()
        self.record(transaction)
        self._deployment_transaction_started = True
        migration_entered = False
        deployment_step = "stopping current service"
        try:
            if old or self.unit_file.exists():
                self.service("stop")
                transaction["service_stopped"] = True
                deployment_step = "recording service shutdown"
                self.record(transaction)
            deployment_step = "post-stop activity check"
            if self._active_jobs():
                raise DeploymentError(
                    "New job activity detected during shutdown; update refused, retained state preserved"
                )
            deployment_step = "post-stop storage preflight"
            self.check_storage(
                self._deployment_requirements(),
                phase="pre-deployment snapshot",
                service_state="stopped",
                protected_releases={Path(target)},
            )
            deployment_step = "pre-deployment snapshot"
            transaction["phase"] = "snapshot"
            self.record(transaction)
            backup = self.snapshot(transaction=transaction)
            transaction["backup"] = str(backup)
            transaction["phase"] = "prepared"
            self.record(transaction)
            if old:
                deployment_step = "snapshot rollback association verification"
                self.verify_snapshot_usable(
                    backup,
                    target=old,
                    expected=transaction["backup_association"],
                    service_state="stopped",
                )
            deployment_step = "database migration"
            transaction["phase"] = "migrating"
            transaction["migration_started"] = True
            self.record(transaction)
            migration_entered = True
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
                    user=True,
                )
            deployment_step = "release activation"
            transaction["phase"] = "activating"
            self.record(transaction)
            self.activate(target)
            self._install_release_unit(target)
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
            release_metadata = json.loads((target / RELEASE_MARKER).read_text())
            committed_state = {
                **release_metadata,
                "version": release_metadata.get("version"),
                "previous": transaction["previous"],
                "backup": transaction["backup"],
                "backup_association": transaction["backup_association"],
                "target": transaction["target"],
                "phase": "committed",
            }
            self._finish_transaction(transaction, committed_state)
            print("Release ready: " + str(target))
        except BaseException as error:
            if deployment_step == "commit":
                raise DeploymentStepError(
                    f"Could not finish publishing the installed state: {describe_exception(error)}. Run hnefatafl-update --recover."
                ) from error
            recovery_step = "previous service recovery before migration"
            self._diagnostic_recovery_target = self.release_version(old) if old else "none"
            self._diagnostic_failure_phase = f"{deployment_step}; automatic recovery"
            needs_restore = migration_entered
            transaction["migration_started"] = migration_entered
            try:
                if needs_restore or not self._restore_unchanged_service(
                    transaction, action="automatic recovery", start=True
                ):
                    # A failed target can be stopped even when no new durable
                    # journal entry is possible. Restore waits for publication.
                    recovery_step = "stopping failed target service"
                    if old or self.unit_file.exists():
                        self.service("stop")
                    recovery_step = "recording automatic recovery intent"
                    transaction["operation"] = "automatic-recovery"
                    transaction["phase"] = "recovering"
                    transaction["service_stopped"] = True
                    self.record(transaction)
                    recovery_step = "unsupported rollback policy check"
                    if old:
                        self._ensure_rollback_target_supported(old, action="automatic recovery")
                    if needs_restore:
                        recovery_step = "snapshot compatibility preflight"
                        self.verify_snapshot_usable(
                            Path(transaction["backup"]),
                            target=old,
                            expected=transaction.get("backup_association"),
                            service_state="stopped",
                            recovery_journal=transaction,
                        )
                        recovery_step = "snapshot restoration"
                        self.restore(Path(transaction["backup"]), journal=transaction)
                    if old:
                        recovery_step = "previous release activation"
                        self.activate(old)
                        self._install_release_unit(old)
                        self.run(["systemctl", "daemon-reload"])
                        recovery_step = "previous release service startup"
                        transaction["service_target"] = str(old)
                        self.record(transaction)
                        self.service("start")
                        recovery_step = "previous release readiness verification"
                        self._diagnostic_failure_phase = (
                            "readiness verification during automatic recovery"
                        )
                        self._recovery_probe()
                    elif self.current.is_symlink():
                        recovery_step = "release symlink cleanup"
                        self.current.unlink()
                        sync_directory(self.base)
                    recovery_step = "installed state restoration"
                    self._finish_transaction(
                        transaction,
                        old_state
                        if old_state is not None or old is None
                        else self._recovered_release_state(old),
                    )
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
            self._deployment_recovery_completed = True
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
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
                    f"{error} Automatic recovery: succeeded. Initial installation remains stopped. "
                    + context
                ) from error
            if old:
                outcome = "automatic recovery succeeded; previous release restored and verified"
            else:
                outcome = "automatic recovery succeeded; initial installation remains stopped"
            raise DeploymentStepError(
                f"Deployment failed during {deployment_step}: "
                f"{self._safe_failure_detail(error)}; {outcome}. {context} "
                f"Inspect {self.journal} and journalctl -u hnefatafl -n 200 --no-pager."
            ) from error

    def recover(self, *, rollback=False):
        self.ensure_layout()
        self.recover_updater_refresh()
        self._remove_orphan_staging()
        rollback_plan = self._prepare_explicit_rollback() if rollback else None
        journal, valid, _ = self._load_metadata(self.journal, "transaction journal")
        if (
            rollback
            or valid
            and journal
            and journal.get("operation") != "manual-backup"
            and journal.get("phase") not in {"committed", "recovered"}
        ):
            self._enable_service_start_guard()
        with self.write_gate():
            completed = self._recover_locked(rollback_plan=rollback_plan)
        if isinstance(completed, Path):
            self._post_operation_cleanup("manual backup recovery", protected_backups={completed})
        elif completed:
            self._post_operation_cleanup("recovery")

    def _service_is_active(self) -> bool:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "hnefatafl.service"],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            if self.manage_updater:
                raise DeploymentError(
                    "Cannot determine the current application service state"
                ) from error
            return False
        if result.returncode == 0:
            return True
        if self.manage_updater and (
            result.returncode != 3 or result.stdout.strip() not in {"inactive", "failed"}
        ):
            raise DeploymentError(
                "The application service is in an unknown or transitional state. Inspect systemctl status hnefatafl.service."
            )
        return False

    def _installed_metadata(self) -> dict | None:
        state, valid, reason = self._load_metadata(self.state, "installed state")
        if valid and state is not None:
            valid, reason = self.validate_state_metadata(state)
        if not valid:
            raise DeploymentError(f"Invalid installed state at {self.state}: {reason}")
        return state

    def _require_active_state(self, state: dict | None) -> Path | None:
        try:
            current = self.current.resolve(strict=True) if self.current.is_symlink() else None
        except (OSError, RuntimeError) as error:
            raise DeploymentError(f"The active release link is invalid: {self.current}") from error
        if os.path.lexists(self.current) and current is None:
            raise DeploymentError(f"Expected an active release symlink at {self.current}")
        if current is not None and not self._release_reference(str(current)):
            raise DeploymentError(
                f"The active release is outside the managed release directory: {current}"
            )
        if state is not None and (
            current is None
            or state.get("target") not in (None, str(current))
            or state["version"] != self.release_version(current)
        ):
            raise DeploymentError(
                "Installed state disagrees with the active release. Inspect the deployment journal before continuing."
            )
        return current

    def _rollback_installed_state(self, previous: Path, snapshot: Path) -> dict:
        saved, valid, _ = self._load_metadata(
            snapshot / "config/installed.json", "snapshot installed state"
        )
        if (
            valid
            and saved is not None
            and self.validate_state_metadata(saved)[0]
            and self._state_describes(saved, previous)
        ):
            prior_backup, prior_release = saved.get("backup"), saved.get("previous")
            if prior_backup and Path(prior_backup).parent == self.legacy_backups:
                # An archived state can predate migration. Only a verifiable
                # association can distinguish a migrated artifact from a name
                # conflict at the new location.
                if saved.get("backup_association") is None:
                    prior_backup = None
                else:
                    saved["backup"] = prior_backup = str(self.backups / Path(prior_backup).name)
            if prior_backup and prior_release and Path(prior_release).is_dir():
                try:
                    self._require_complete_release(Path(prior_release))
                    self.verify_snapshot(Path(prior_backup))
                    schema = self._verified_snapshot_schema(Path(prior_backup))
                    self._verify_snapshot_association(
                        Path(prior_backup),
                        target=Path(prior_release),
                        expected=saved.get("backup_association"),
                        allow_legacy=True,
                        schema=schema,
                    )
                except (OSError, DeploymentError):
                    pass
                else:
                    return saved
        return self._recovered_release_state(previous)

    def _state_describes(self, state: dict, release: Path) -> bool:
        return state.get("target") in (None, str(release)) and state.get(
            "version"
        ) == self.release_version(release)

    def _recovered_release_state(self, release: Path) -> dict:
        return {
            **self._require_complete_release(release),
            "target": str(release),
            "previous": None,
            "backup": None,
            "phase": "committed",
        }

    def _finish_transaction(self, transaction: dict, state: dict | None) -> None:
        transaction["phase"] = "committing"
        transaction["commit_state"] = state
        self.record(transaction)
        self._publish_transaction_state(transaction)

    def _publish_transaction_state(self, transaction: dict) -> None:
        state = transaction["commit_state"]
        self._publish_transaction_metadata(
            self.state, state, phase=transaction["phase"], use_reserve=True
        )
        transaction["phase"] = (
            "committed" if transaction["operation"] in {"install", "update"} else "recovered"
        )
        transaction["prior_state"] = None
        transaction.pop("rollback_state", None)
        transaction.pop("commit_state")
        self.record(transaction)
        self.finalize_restore(transaction)

    def _resume_transaction_commit(self, transaction: dict) -> None:
        state = transaction["commit_state"]
        intended = (
            transaction["target"]
            if transaction["operation"] in {"install", "update"}
            else transaction.get("previous")
        )
        current = self._require_active_state(state)
        if intended != (str(current) if current is not None else None):
            raise DeploymentError("The active release disagrees with the durable commit intent")
        if transaction.get("restore"):
            restore = transaction["restore"]
            root = self._data_directory(self.data)
            if restore.get("staging_identity") != [root.device, root.inode]:
                raise DeploymentError(
                    "The active data directory disagrees with the durable commit intent"
                )
        if current is not None:
            if not self._service_is_active():
                self.service("start")
            self._recovery_probe()
        self._publish_transaction_state(transaction)

    def _restore_unchanged_service(self, transaction: dict, *, action: str, start=False) -> bool:
        if "previous_data_identity" not in transaction:
            return False
        durable, valid, reason = self._load_metadata(self.journal, "transaction journal")
        if valid and durable is not None:
            valid, reason = self.validate_transaction_metadata(durable)
        if not valid or durable is None or durable.get("transaction") != transaction["transaction"]:
            raise DeploymentError(
                f"Cannot verify the durable transaction at {self.journal} for service recovery. "
                f"{reason} Inspect the journal before restarting."
            )
        previous = self._unchanged_previous_release(durable, self._data_directory(self.data))
        if previous is None:
            return False
        self._ensure_rollback_target_supported(previous, action=action)
        self._require_complete_release(previous)
        # File locking needs no free blocks. The startup guard checks the
        # protected journal and both identities before honoring this lock.
        with self._open_unchanged_service_journal(durable) as descriptor:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if start or not self._service_is_active():
                self.service("start")
            self._recovery_probe()
        try:
            self.record(
                {
                    **transaction,
                    "operation": "automatic-recovery",
                    "phase": "recovered",
                    "service_target": str(previous),
                    "migration_started": False,
                    "prior_state": None,
                }
            )
        except (OSError, JournalWriteError) as error:
            raise DeploymentStepError(
                "The previous service is running and verified on unchanged data. "
                "Recovery metadata remains incomplete. " + describe_exception(error)
            ) from error
        return True

    def _prepare_explicit_rollback(self) -> dict:
        self._require_idle_transaction("start a new rollback")
        installed = self._installed_metadata()
        if installed is None or not installed.get("previous"):
            raise DeploymentError("No previous release to roll back to")
        previous = Path(installed["previous"])
        self._ensure_rollback_target_supported(previous, action="rollback")
        active = self._require_active_state(installed)
        if not installed.get("backup"):
            raise DeploymentError(
                "No verified pre-deployment snapshot is recorded for this rollback."
            )
        backup = Path(installed["backup"])
        self._require_complete_release(previous)
        self._unit_content(previous)
        self._validate_restore_topology()
        for warning in self.verify_snapshot_usable(
            backup, target=previous, expected=installed.get("backup_association")
        ):
            print(f"Recovery note: {warning}")
        self.check_storage(
            [self.restore_requirement(backup), self._journal_storage_requirement()],
            phase="rollback metadata and restore",
            service_state="unchanged",
        )
        return {
            "operation": "rollback",
            "transaction": secrets.token_hex(16),
            "abi": UPDATER_ABI,
            "phase": "recovering",
            "previous": str(previous),
            "target": str(active),
            "backup": str(backup),
            "backup_association": installed.get("backup_association"),
            "prior_state": installed,
            "rollback_state": self._rollback_installed_state(previous, backup),
            "service_stopped": False,
            "migration_started": True,
            "data_modified": False,
            "restore": None,
        }

    def _recover_locked(self, *, rollback_plan: dict | None = None) -> bool | Path:
        rollback = rollback_plan is not None
        self._diagnostic_failure_phase = None
        self._diagnostic_recovery_target = None
        journal, valid, reason = self._load_metadata(self.journal, "transaction journal")
        if valid and journal is not None:
            valid, reason = self.validate_transaction_metadata(journal)
        if not valid:
            raise DeploymentError(f"Cannot recover from invalid {self.journal}: {reason}")
        previous: Path | None
        backup: Path | None
        transaction: dict
        if rollback:
            assert rollback_plan is not None
            self._require_idle_transaction("start a new rollback")
            transaction = rollback_plan
            previous = Path(transaction["previous"])
            backup = Path(transaction["backup"])
        else:
            if journal is None:
                print("No interrupted deployment to recover.")
                return False
            transaction = journal
            phase = transaction["phase"]
            if phase in {"committed", "recovered"}:
                # A prior attempt may have reached rename but failed to sync
                # the journal directory. Confirm durability before cleanup.
                sync_directory(self.etc)
                if transaction.get("restore"):
                    self._require_active_state(self._installed_metadata())
                    self.finalize_restore(transaction)
                    return True
                print("No interrupted deployment to recover.")
                return False
            if phase == "committing":
                self._resume_transaction_commit(transaction)
                return True
            if transaction.get("operation") == "manual-backup":
                recovery_error = self._restore_backup_service(transaction)
                if recovery_error is not None:
                    raise DeploymentError(
                        f"Manual backup service recovery failed: {describe_exception(recovery_error)}. Inspect {self.journal} and rerun hnefatafl-update --recover."
                    ) from recovery_error
                print("Recovered the service state recorded before the manual backup.")
                snapshot = self.backups / transaction["snapshot_name"]
                return snapshot if os.path.lexists(snapshot) else False
            previous = Path(transaction["previous"]) if transaction.get("previous") else None
            backup = Path(transaction["backup"]) if transaction.get("backup") else None
            if (
                not transaction.get("operation")
                and phase == "recovering"
                and transaction.get("abi") == 2
                and (transaction.get("prior_state") or {}).get("target")
                == transaction.get("target")
            ):
                transaction["operation"] = "rollback"
        if previous is not None:
            self._diagnostic_recovery_target = self.release_version(previous)
            self._ensure_rollback_target_supported(
                previous, action="rollback" if rollback else "interrupted recovery"
            )
            self._require_complete_release(previous)
            self._unit_content(previous)
        restore = transaction.get("restore")
        needs_restore = bool(
            rollback
            or restore
            or transaction.get(
                "migration_started",
                transaction["phase"] in {"migrating", "activating", "activated"},
            )
        )
        initial = False
        if needs_restore:
            if backup is None:
                raise DeploymentError(
                    "Interrupted deployment may have modified the database, but no snapshot is recorded. Perform reviewed manual recovery."
                )
            self._validate_restore_topology()
            initial = self._initial_install_association(backup, transaction) is not None
            if rollback_plan is not None:
                self._verify_restore_preflight(backup)
            elif restore is None or restore.get("phase") == "prepared":
                warnings = self.verify_snapshot_usable(
                    backup,
                    target=previous,
                    expected=transaction.get("backup_association"),
                    recovery_journal=transaction,
                )
                for warning in warnings:
                    print(f"Recovery note: {warning}")
        if not initial and self._active_jobs():
            raise DeploymentError("Finish/cancel training and tournaments before recovery")
        if not needs_restore and self._restore_unchanged_service(
            transaction, action="interrupted recovery"
        ):
            return True
        if transaction.get("operation") == "rollback":
            assert previous is not None and backup is not None
            if "rollback_state" not in transaction:
                transaction["rollback_state"] = self._rollback_installed_state(previous, backup)
            recovered_state = transaction["rollback_state"]
        elif previous is not None:
            prior = transaction.get("prior_state") or self._installed_metadata()
            recovered_state = (
                prior
                if prior is not None and self._state_describes(prior, previous)
                else self._recovered_release_state(previous)
            )
        else:
            recovered_state = None
        migrated = needs_restore
        transaction.update(
            {
                "phase": "recovering",
                "abi": UPDATER_ABI,
                "operation": "rollback"
                if transaction.get("operation") == "rollback"
                else "automatic-recovery",
                "transaction": transaction.get("transaction") or secrets.token_hex(16),
                "previous": str(previous) if previous is not None else None,
                "service_stopped": bool(transaction.get("service_stopped")),
                "migration_started": migrated,
                "data_modified": bool(transaction.get("data_modified")),
                "service_target": None,
            }
        )
        step = "recording recovery intent"
        try:
            if rollback:
                self._reserve_journal_space()
            self.record(transaction)
            step = "checking current service"
            current = self.current.resolve() if self.current.is_symlink() else None
            already_healthy = (
                current is not None
                and current == previous
                and not needs_restore
                and self._service_is_active()
            )
            if already_healthy:
                try:
                    self._recovery_probe()
                except ReadinessError:
                    already_healthy = False
            if not already_healthy:
                step = "stopping current service"
                # An interrupted first install can predate creation of its service unit.
                if not initial or self.unit_file.exists():
                    self.service("stop")
                transaction["service_stopped"] = True
                self.record(transaction)
                if needs_restore:
                    assert backup is not None
                    step = "snapshot restoration"
                    if restore is None or not self.recover_restore(transaction):
                        self.restore(backup, journal=transaction)
                    else:
                        print(
                            "Restored pre-deployment state from the interrupted restore candidate."
                        )
                if previous is not None:
                    step = "previous release activation"
                    self.activate(previous)
                    self._install_release_unit(previous)
                    self.run(["systemctl", "daemon-reload"])
                    step = "previous release service startup"
                    transaction["service_target"] = str(previous)
                    self.record(transaction)
                    self.service("start")
                    step = "previous release readiness verification"
                    self._diagnostic_failure_phase = "readiness verification during " + (
                        "rollback"
                        if transaction["operation"] == "rollback"
                        else "interrupted recovery"
                    )
                    self._recovery_probe()
                elif self.current.is_symlink():
                    self.current.unlink()
                    sync_directory(self.base)
            step = "installed state publication"
            self._finish_transaction(transaction, recovered_state)
        except (KeyboardInterrupt, SystemExit, ReadinessError):
            raise
        except BaseException as error:
            raise DeploymentStepError(
                f"Recovery failed during {step}: {describe_exception(error)}. Inspect {self.journal} and rerun hnefatafl-update --recover."
            ) from error
        return True


def protected_json(path: Path) -> dict:
    fs = PrivilegedFilesystem()
    fs._validate_ancestors(path, "deployment configuration")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise DeploymentError(
                f"Configuration must be an owner-controlled 0600 regular file: {path}"
            )
        try:
            value = json.load(handle, object_pairs_hook=unique_metadata_fields)
        except (ValueError, RecursionError) as error:
            raise DeploymentError(f"Invalid JSON in deployment configuration: {path}") from error
    if not isinstance(value, dict):
        raise DeploymentError(f"Configuration must be a JSON object: {path}")
    return value


def _open_deployment_lock(path: Path | None = None, *, read_only: bool = False) -> int:
    path = Path(path or "/run/lock/hnefatafl-update.lock")
    fs = PrivilegedFilesystem()
    fs._validate_ancestors(path, "deployment lock")
    if fs.is_mount(path):
        raise DeploymentError(f"The deployment lock is a mount point: {path}")
    flags = os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT
    descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(descriptor)
        raise DeploymentError(f"The deployment lock must be an owner-controlled 0600 file: {path}")
    return descriptor


def _lock_deployment(descriptor: int, *, read_only: bool = False) -> None:
    try:
        fcntl.flock(descriptor, (fcntl.LOCK_SH if read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except BlockingIOError:
        raise DeploymentError("Another installation/update/backup is running") from None


def install_reviewed_updater(
    source: Path, etc: Path, utilities: Path, commands: Path, lock: Path
) -> None:
    """Bootstrap from the same administrator-reviewed distribution as install.sh."""
    with closing(os.fdopen(_open_deployment_lock(lock), "r+")) as handle:
        _lock_deployment(handle.fileno())
        config_path = etc / "deployment.json"
        config = protected_json(config_path) if os.path.lexists(config_path) else {}
        deployment = Deployment(config, etc=etc, updater_root=utilities)
        deployment.updater_commands = (commands / "hnefatafl-update", commands / "hnefatafl-backup")
        deployment.recover_updater_refresh()
        payload = {}
        for member in deployment.UPDATER_MEMBERS.values():
            path = source / member
            if not stat.S_ISREG(path.lstat().st_mode):
                raise DeploymentError(f"Missing or unsafe reviewed installer member: {path}")
            payload[member] = path.read_bytes()
        deployment._publish_updater_plan(payload, tag="installer", repair=True)


def _production_layout() -> dict:
    return {
        "config_dir": Path("/etc/hnefatafl"),
        "base": Path("/opt/hnefatafl"),
        "data": Path("/var/lib/hnefatafl"),
        "etc": Path("/etc/hnefatafl"),
        "unit_file": Path("/etc/systemd/system/hnefatafl.service"),
        "backups": None,
        "lock": Path("/run/lock/hnefatafl-update.lock"),
        "updater_root": Path("/usr/local/lib/hnefatafl"),
    }


def guard_service_start() -> int:
    paths = _production_layout()
    config = protected_json(Path(paths["config_dir"]) / "deployment.json")
    Deployment(config)._authorize_service_start()
    return 0


def main(argv=None, *, layout=None, installed_generation=None, deployment_lock=None):
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
        "verify-service",
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
    read_only = args.check or args.list or args.cleanup and args.dry_run
    if os.geteuid() != 0 and layout is None:
        parser.error("Run this deployment command as root")
    os.umask(0o077)

    def interrupted(*_):
        raise InterruptedError("Deployment interrupted; inspect the transaction journal")

    signal.signal(signal.SIGTERM, interrupted)
    paths = layout or _production_layout()
    config_dir = Path(paths["config_dir"])
    lock_descriptor = None
    try:
        if installed_generation is None:
            try:
                lock_descriptor = _open_deployment_lock(Path(paths["lock"]), read_only=read_only)
            except FileNotFoundError:
                if not read_only:
                    raise
            if lock_descriptor is not None:
                _lock_deployment(lock_descriptor, read_only=read_only)
        elif deployment_lock is None and not read_only:
            raise DeploymentError("The installed launcher did not acquire the deployment lock")
        config = protected_json(config_dir / "deployment.json")
        deployment = Deployment(
            config,
            base=Path(paths["base"]),
            data=Path(paths["data"]),
            etc=Path(paths["etc"]),
            unit_file=Path(paths["unit_file"]),
            backups=Path(paths["backups"]) if paths["backups"] is not None else None,
            updater_root=Path(paths["updater_root"]),
            custom_layout=layout is not None,
        )
        if installed_generation is not None:
            deployment.updater_support = Path(installed_generation) / "updater_support"
        legacy_publication = os.path.lexists(deployment.updater_root / ".refresh.json")
        if legacy_publication and read_only:
            raise DeploymentError(
                "Interrupted updater publication. Run hnefatafl-update --recover."
            )
        if not read_only:
            deployment.recover_updater_refresh()
        if read_only and os.path.lexists(deployment.migration_journal):
            raise DeploymentError("Interrupted backup migration. Run hnefatafl-update --recover.")
        if args.recover:
            deployment.recover()
            return 0
        deployment._require_idle_transaction("continue")
        if args.cleanup:
            if not read_only:
                deployment.ensure_layout()
            report = deployment.cleanup(dry_run=args.dry_run, include_caches=True)
            deployment.print_cleanup_report(report)
            if report.failed or report.errors or report.refused and not read_only:
                return 1
            return 0
        if args.rollback:
            deployment.recover(rollback=True)
            return 0
        if args.verify_service:
            deployment._require_active_state(deployment._installed_metadata())
            with deployment.write_gate():
                deployment.probe()
            return 0
        if args.backup:
            deployment.backup()
            return 0
        token_file = config_dir / "github.json"
        token = protected_json(token_file).get("token", "") if token_file.exists() else ""
        if not isinstance(token, str) or not isinstance(config.get("repository"), str):
            raise DeploymentError(
                "Invalid deployment repository or GitHub authentication configuration"
            )
        github = GitHub(config["repository"], token)
        releases = github.releases()
        if not releases:
            raise ValueError("No published stable GitHub release is available")
        if args.list:
            print("\n".join(item["tag_name"] for item in releases))
            return 0
        state = deployment._installed_metadata()
        deployment._require_active_state(state)
        current = state["version"] if state is not None else None
        selected, newer = select_release(releases, current, args.version)
        if args.refresh_updater:
            print(
                f"Installed: {current or 'none'}; refreshing deployment utility from "
                f"release {selected['tag_name']}"
            )
            deployment.refresh_updater(github, selected)
            return 0
        print(f"Installed: {current or 'none'}; selected stable release: {selected['tag_name']}")
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
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
