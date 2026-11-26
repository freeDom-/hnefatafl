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
# Transaction phases this utility understands. A utility must never install a
# replacement with a lower protocol number: it could not resume the journal phases
# or interpret the retention/restore metadata written by this one.
UPDATER_ABI = 2
JOURNAL_PHASES = {
    "prepared",
    "snapshot",
    "migrating",
    "activating",
    "activated",
    "committed",
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
RESTORE_MARKER = ".restore.json"
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


@dataclass(frozen=True)
class Usage:
    """Conservative tree usage for storage preflight.

    ``bytes`` is the larger of apparent and allocated size per entry, plus an
    allowance for directory and inode metadata, so filesystem allocation overhead
    for many small files is included. ``inodes`` counts directories, regular files
    and symlinks, because a copied tree consumes an inode for each. Hardlinked files
    are counted per entry (a copy does not share them) while ``hardlink_bytes``
    records how much of the apparent size is shared today for reclamation reports.
    """

    bytes: int = 0
    inodes: int = 0
    files: int = 0
    directories: int = 0
    apparent_bytes: int = 0
    allocated_bytes: int = 0
    hardlink_bytes: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            bytes=self.bytes + other.bytes,
            inodes=self.inodes + other.inodes,
            files=self.files + other.files,
            directories=self.directories + other.directories,
            apparent_bytes=self.apparent_bytes + other.apparent_bytes,
            allocated_bytes=self.allocated_bytes + other.allocated_bytes,
            hardlink_bytes=self.hardlink_bytes + other.hardlink_bytes,
        )


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
                try:
                    resolved = ancestor.resolve(strict=True)
                    target = os.stat(resolved)
                except (OSError, RuntimeError) as error:
                    raise DeploymentError(
                        f"The ancestor {ancestor} of {description} is an unresolved "
                        f"symlink ({describe_exception(error)}); refusing privileged "
                        "filesystem operations"
                    ) from error
                if not stat.S_ISDIR(target.st_mode):
                    raise DeploymentError(
                        f"The ancestor {ancestor} of {description} does not resolve to a "
                        "directory; refusing privileged filesystem operations"
                    )
                if target.st_uid not in {0, self.euid}:
                    raise DeploymentError(
                        f"The ancestor {ancestor} of {description} resolves into a "
                        "directory owned by an untrusted account; refusing privileged "
                        "filesystem operations"
                    )
                if not self._acceptable_ancestor_mode(target.st_mode):
                    raise DeploymentError(
                        f"The ancestor {ancestor} of {description} resolves into a "
                        "writable directory controlled by another account; refusing "
                        "privileged filesystem operations"
                    )
            elif not stat.S_ISDIR(info.st_mode):
                raise DeploymentError(
                    f"The ancestor {ancestor} of {description} is not a directory; "
                    "refusing privileged filesystem operations"
                )
            elif info.st_uid not in {0, self.euid}:
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
        if nested:
            shown = ", ".join(str(item) for item in nested[:3])
            raise DeploymentError(
                f"The {description} ({path}) contains nested mount points ({shown}); "
                "refusing privileged filesystem operations that could cross or hide "
                "another filesystem"
            )
        return RootIdentity(path, int(info.st_dev), int(info.st_ino), description)

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
        expected_owner: int,
        identity: tuple[int, int] | None = None,
        allow_symlinks_inside: bool = True,
        allow_nested: bool = False,
    ) -> None:
        """Remove one validated artifact below ``root`` without following links."""
        target = Path(target)
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
                    if info.st_uid != expected_owner:
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
        expected_owner: int,
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
        if info.st_uid != expected_owner:
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
        os.chmod(marker, 0o600)

    def read_staging_marker(self, directory: Path) -> dict | None:
        marker = Path(directory) / STAGING_MARKER
        try:
            info = os.lstat(marker)
        except OSError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
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
    # The top directory itself is created by a copy operation and consumes an
    # inode and metadata blocks.
    usage = Usage(bytes=TREE_OVERHEAD_PER_DIRECTORY, inodes=1, directories=1)
    seen: set[tuple[int, int]] = set()

    def add(info, *, directory: bool) -> None:
        nonlocal usage
        key = (int(info.st_dev), int(info.st_ino))
        allocated = int(getattr(info, "st_blocks", 0)) * 512
        size = int(info.st_size)
        if directory:
            usage = usage + Usage(
                bytes=TREE_OVERHEAD_PER_DIRECTORY,
                inodes=1,
                directories=1,
            )
            return
        entry = Usage(
            bytes=max(size, allocated) + TREE_OVERHEAD_PER_FILE,
            inodes=1,
            files=1,
            apparent_bytes=size,
            allocated_bytes=allocated,
        )
        if info.st_nlink > 1:
            if key in seen:
                entry = Usage(
                    bytes=TREE_OVERHEAD_PER_FILE,
                    inodes=1,
                    files=1,
                    apparent_bytes=size,
                    allocated_bytes=allocated,
                    hardlink_bytes=size,
                )
            else:
                seen.add(key)
        usage = usage + entry

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
    return usage


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
        self._online_support: types.ModuleType | None = None
        self._layout_ready = False
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

    def run(self, args, *, cwd=None, user=False, extra_env=None):
        command = [str(value) for value in args]
        if user:
            command = ["runuser", "-u", "hnefatafl", "--", *command]
        subprocess.run(command, cwd=cwd, env={**self.env, **(extra_env or {})}, check=True)

    def service(self, action):
        self.run(["systemctl", action, "hnefatafl.service"])

    def record(self, value):
        atomic_json(self.journal, value)

    # ------------------------------------------------------------------
    # Managed directories and metadata validation
    # ------------------------------------------------------------------
    def _release_root(self, *, write: bool = False) -> RootIdentity:
        return self._managed_root(
            self.releases, owner=0, mode=0o755, description="release directory", write=write
        )

    def _backup_root(self, *, write: bool = False) -> RootIdentity:
        return self._managed_root(
            self.backups, owner=0, mode=0o700, description="backup directory", write=write
        )

    def _base_root(self) -> RootIdentity:
        return self._managed_root(
            self.base, owner=0, mode=0o755, description="deployment base directory"
        )

    def _managed_root(
        self, path: Path, *, owner: int, mode: int, description: str, write: bool = False
    ) -> RootIdentity:
        path = Path(path)
        if write and not path.exists():
            if not path.parent.is_dir():
                raise DeploymentError(
                    f"Cannot create the {description}: parent {path.parent} is missing"
                )
            os.mkdir(path, 0o700 if mode & 0o077 == 0 else 0o755)
            if os.geteuid() == 0:
                os.chown(path, owner, owner)
        if not path.exists():
            raise DeploymentError(f"The {description} does not exist: {path}")
        return self.fs.validate_root(path, owner=owner, description=description, mode=mode)

    def ensure_layout(self) -> None:
        """Validate and, when needed, migrate the root-owned deployment layout.

        A legacy installation kept verified snapshots directly inside the
        application-user-owned data directory. They are relocated once into the
        root-owned sibling backup root before any privileged snapshot operation, and
        recorded metadata references are rewritten atomically.
        """
        if self._layout_ready:
            return
        self._migrate_legacy_backups()
        self._layout_ready = True

    def _migrate_legacy_backups(self) -> None:
        legacy, current = self.legacy_backups, self.backups
        try:
            info = os.lstat(legacy)
        except FileNotFoundError:
            return
        except OSError as error:
            raise DeploymentError(
                "Cannot inspect the legacy backup directory "
                f"({legacy}): {describe_exception(error)}. Refusing privileged "
                "operations until it is resolved."
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DeploymentError(
                f"The legacy backup location {legacy} is not a real directory. The "
                "application account could redirect privileged backup writes; stop "
                "and inspect the installation before continuing."
            )
        if info.st_uid != self.privileged_uid or stat.S_IMODE(info.st_mode) & 0o077:
            raise DeploymentError(
                f"The legacy backup location {legacy} is not root-owned 0700. Refusing "
                "to trust or move it; inspect it manually and set the expected "
                "ownership."
            )
        nested = self.fs.nested_mounts(legacy)
        if nested:
            raise DeploymentError(
                f"The legacy backup location {legacy} contains nested mounts "
                "and cannot be migrated automatically."
            )
        moved = []
        if not current.exists():
            try:
                os.rename(legacy, current)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise DeploymentError(
                        f"Cannot relocate the legacy backup directory to {current}: "
                        f"{describe_exception(error)}"
                    ) from error
        current_exists = current.exists()
        if current_exists:
            current_info = os.lstat(current)
            if stat.S_ISLNK(current_info.st_mode) or not stat.S_ISDIR(current_info.st_mode):
                raise DeploymentError(
                    f"The backup directory {current} is not a real directory; refusing "
                    "privileged snapshot operations"
                )
            if (
                current_info.st_uid != self.privileged_uid
                or stat.S_IMODE(current_info.st_mode) & 0o077
            ):
                raise DeploymentError(
                    f"The backup directory {current} is not root-owned 0700; refusing "
                    "privileged snapshot operations"
                )
            if legacy.exists() and legacy != current:
                try:
                    children = sorted(legacy.iterdir(), key=lambda item: item.name)
                except OSError as error:
                    raise DeploymentError(
                        f"Cannot inspect the legacy backup directory: {describe_exception(error)}"
                    ) from error
                for child in children:
                    destination = current / child.name
                    if destination.exists():
                        print(
                            f"Legacy backup migration warning: {destination} already exists; "
                            f"leaving {child} in {legacy} for manual review.",
                            file=sys.stderr,
                        )
                        continue
                    try:
                        os.rename(child, destination)
                    except OSError as error:
                        if error.errno != errno.EXDEV:
                            raise DeploymentError(
                                f"Cannot relocate the legacy snapshot {child}: "
                                f"{describe_exception(error)}"
                            ) from error
                        self._copy_verified_snapshot(child, destination)
                    moved.append(child.name)
                try:
                    legacy.rmdir()
                except OSError:
                    print(
                        f"Legacy backup migration: {legacy} still contains unrecognized "
                        "entries and was retained for manual review.",
                        file=sys.stderr,
                    )
        if legacy.exists() and legacy != current:
            if not moved:
                return
        self._rewrite_backup_references(legacy, current)
        print(f"Migrated verified backups from {legacy} to the root-owned {current}.")

    def _copy_verified_snapshot(self, source: Path, destination: Path) -> None:
        staging = Path(destination).with_name(".staging-backup-" + secrets.token_hex(8))
        try:
            shutil.copytree(source, staging, symlinks=True)
            self.verify_snapshot(staging)
            os.chmod(staging, 0o700)
            if os.geteuid() == 0:
                self.run(["chown", "-R", "root:root", staging])
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _rewrite_backup_references(self, legacy: Path, current: Path) -> None:
        def rewritten(value):
            if not isinstance(value, str) or not value:
                return value
            candidate = Path(value)
            try:
                relative = candidate.relative_to(legacy)
            except ValueError:
                return value
            return str(current / relative)

        for metadata_path in (self.state, self.journal):
            value, valid, _reason = self._load_metadata(metadata_path, metadata_path.name)
            if not valid or value is None:
                continue
            changed = False
            for key in ("backup",):
                if key in value:
                    replacement = rewritten(value.get(key))
                    if replacement != value.get(key):
                        value[key] = replacement
                        changed = True
            for nested_key in ("prior_state", "restore"):
                nested = value.get(nested_key)
                if isinstance(nested, dict) and "backup" in nested:
                    replacement = rewritten(nested.get("backup"))
                    if replacement != nested.get("backup"):
                        nested["backup"] = replacement
                        changed = True
            if changed:
                try:
                    atomic_json(metadata_path, value)
                except OSError as error:
                    raise DeploymentError(
                        f"Relocated snapshots but cannot update {metadata_path}: "
                        f"{describe_exception(error)}. Update the recorded backup paths "
                        "manually before continuing."
                    ) from error

    def _path_within(self, value, root: Path) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            candidate = Path(value)
            if not candidate.is_absolute() or ".." in candidate.parts:
                return False
            candidate.relative_to(root)
            return True
        except (ValueError, OSError):
            return False

    def validate_state_metadata(self, state, *, depth: int = 0) -> tuple[bool, str]:
        """Semantically validate installed state before destructive cleanup."""
        if depth > 4:
            return False, "nested prior-state metadata is too deep"
        if not isinstance(state, dict):
            return False, "installed state is not a JSON object"
        legacy = not any(key in state for key in ("target", "previous", "backup", "phase"))
        if "version" in state and (
            not isinstance(state["version"], str) or VERSION.fullmatch(state["version"]) is None
        ):
            return False, "installed state version is invalid"
        if "phase" in state and state.get("phase") != "committed":
            return False, "installed state has an unsupported phase"
        for key, root in (
            ("target", self.releases),
            ("previous", self.releases),
        ):
            value = state.get(key)
            if value is None:
                continue
            if not self._path_within(value, root):
                return False, f"installed state {key} is outside {root}"
        backup = state.get("backup")
        if backup is not None and not (
            self._path_within(backup, self.backups)
            or self._path_within(backup, self.legacy_backups)
        ):
            return False, "installed state backup is outside the managed backup root"
        prior = state.get("prior_state")
        if prior is not None:
            valid, reason = self.validate_state_metadata(prior, depth=depth + 1)
            if not valid:
                return False, "nested prior state is invalid: " + reason
        if not legacy and not isinstance(state.get("version"), str):
            return False, "installed state has no valid version"
        return True, ""

    def validate_transaction_metadata(self, journal) -> tuple[bool, str]:
        """Semantically validate a transaction journal before destructive cleanup."""
        if not isinstance(journal, dict):
            return False, "transaction journal is not a JSON object"
        phase = journal.get("phase")
        if not isinstance(phase, str) or phase not in JOURNAL_PHASES:
            return False, "transaction journal phase is unsupported"
        for key, root in (("target", self.releases), ("previous", self.releases)):
            value = journal.get(key)
            if value is None:
                continue
            if not self._path_within(value, root):
                return False, f"transaction {key} is outside {root}"
        backup = journal.get("backup")
        if backup is not None and not (
            self._path_within(backup, self.backups)
            or self._path_within(backup, self.legacy_backups)
        ):
            return False, "transaction backup is outside the managed backup root"
        for flag in ("service_stopped", "migration_started", "data_modified"):
            if flag in journal and not isinstance(journal[flag], bool):
                return False, f"transaction {flag} is not a boolean"
        prior = journal.get("prior_state")
        if prior is not None:
            valid, reason = self.validate_state_metadata(prior)
            if not valid:
                return False, "transaction prior_state is invalid: " + reason
        restore = journal.get("restore")
        if restore is not None:
            valid, reason = self._validate_restore_metadata(restore)
            if not valid:
                return False, "transaction restore metadata is invalid: " + reason
        return True, ""

    def _validate_restore_metadata(self, restore) -> tuple[bool, str]:
        if not isinstance(restore, dict):
            return False, "not an object"
        phase = restore.get("phase")
        if not isinstance(phase, str) or phase not in RESTORE_PHASES:
            return False, "unsupported restore phase"
        if restore.get("data") not in {None, str(self.data)}:
            return False, "restore data path does not match the configured data root"
        for key in ("staging", "retained"):
            value = restore.get(key)
            if value is None:
                continue
            candidate = Path(str(value))
            if candidate.parent != self.data.parent:
                return False, f"restore {key} is not beside the data root"
            pattern = RESTORE_STAGING_DIRECTORY if key == "staging" else RETAINED_DIRECTORY
            if pattern.fullmatch(candidate.name) is None:
                return False, f"restore {key} has an unexpected name"
        for key, root in (("target", self.releases), ("previous", self.releases)):
            value = restore.get(key)
            if value is not None and not self._path_within(value, root):
                return False, f"restore {key} is outside {root}"
        backup = restore.get("backup")
        if backup is not None and not (
            self._path_within(backup, self.backups)
            or self._path_within(backup, self.legacy_backups)
        ):
            return False, "restore snapshot is outside the managed backup root"
        transaction_id = restore.get("transaction")
        if transaction_id is not None and (
            not isinstance(transaction_id, str)
            or re.fullmatch(r"[0-9a-f]{8,32}", transaction_id) is None
        ):
            return False, "restore transaction identifier is invalid"
        return True, ""

    @contextmanager
    def write_gate(self):
        # Drain admitted HTTP mutations before checking jobs/stopping services.
        # New mutations fail with 503 until activation/recovery finishes. GET
        # readiness probes remain available and cannot launch training.
        gate = self.data / ".deployment-gate"
        if not self.data.is_dir():
            # An interrupted restore may have renamed the data directory before
            # publishing its replacement. The locked gate inode then lives in the
            # retained or staging directory; adopt it so recovery is still
            # serialized against any surviving writer.
            gate = self.data.parent / (self.data.name + ".deployment-gate")
            journal, journal_valid, _reason = self._load_metadata(
                self.journal, "transaction journal"
            )
            if journal_valid and isinstance(journal, dict):
                restore = journal.get("restore")
                if isinstance(restore, dict):
                    for key in ("staging", "retained"):
                        candidate = restore.get(key)
                        if not isinstance(candidate, str):
                            continue
                        candidate_gate = Path(candidate) / ".deployment-gate"
                        if candidate_gate.exists():
                            gate = candidate_gate
                            break
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
            "hnefatafl.sqlite3",
            "hnefatafl.sqlite3-wal",
            "hnefatafl.sqlite3-shm",
        }

    def _database_size(self) -> tuple[int, int]:
        """Actual SQLite page allocation plus sidecars and inode count."""
        database = self.data / "hnefatafl.sqlite3"
        if not database.is_file() or database.is_symlink():
            return 0, 0
        size = 0
        inodes = 1
        try:
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as c:
                page_count = c.execute("PRAGMA page_count").fetchone()[0]
                page_size = c.execute("PRAGMA page_size").fetchone()[0]
                size = int(page_count) * int(page_size)
        except sqlite3.Error:
            size = int(database.stat().st_size)
        for suffix in ("-wal", "-shm"):
            sidecar = database.with_name(database.name + suffix)
            if sidecar.is_file() and not sidecar.is_symlink():
                info = sidecar.stat()
                size += max(int(info.st_size), int(getattr(info, "st_blocks", 0)) * 512)
                inodes += 1
        # A standalone SQLite copy omits the WAL but adds page overhead.
        return int(size * 1.05) + TREE_OVERHEAD_PER_FILE, inodes

    def snapshot_requirement(self) -> StorageRequirement:
        """Estimate a snapshot using exactly the snapshot inclusion rules."""
        excluded = self._snapshot_excluded()
        data_usage = scan_usage(self.data, excluded)
        config_usage = self._usage(self.etc)
        database_bytes, database_inodes = self._database_size()
        manifest_bytes = max(MIB, (data_usage.inodes + config_usage.inodes + 32) * 512)
        usage = data_usage + config_usage
        total = usage.bytes + database_bytes + manifest_bytes
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
        archive_bytes = archive_size if archive_size > 0 else 64 * MIB
        # Extraction, the frozen virtual environment, npm ci and the frontend
        # build coexist temporarily. Historical prepared peaks are the primary
        # measurement; the configured floor prevents an empty history from
        # pretending a first release is tiny.
        build_bytes = int(release_bytes * 1.15) + archive_bytes
        cache_bytes = int(self.storage_settings["cache_headroom_mib"]) * MIB
        return [
            StorageRequirement(
                self.releases,
                build_bytes,
                "release extraction, virtual environment and frontend build",
                release_inodes,
            ),
            StorageRequirement(
                self.base,
                archive_bytes,
                "temporary archive download and extraction",
                max(64, archive_bytes // (32 * 1024)),
            ),
            StorageRequirement(
                self.data / "cache",
                cache_bytes,
                "uv/npm dependency cache growth and temporary build files",
                8000,
            ),
            self.snapshot_requirement(),
            StorageRequirement(self.etc, 16 * MIB, "deployment metadata", 64),
        ]

    def _deployment_requirements(self) -> list[StorageRequirement]:
        margin = int(self.storage_settings["snapshot_margin_percent"]) / 100
        return [
            self.snapshot_requirement(),
            StorageRequirement(
                self.data,
                int(64 * MIB * (1 + margin)),
                "migration and rollback headroom beside the data directory",
                256,
            ),
            StorageRequirement(self.base, 16 * MIB, "release activation metadata", 32),
            StorageRequirement(self.etc, 16 * MIB, "transaction journal", 64),
        ]

    def restore_requirement(self, backup: Path) -> StorageRequirement:
        """Restore-specific estimate against the actual snapshot destination."""
        backup = Path(backup)
        usage = scan_usage(backup / "data")
        usage = usage + self._usage(backup / "config")
        margin = int(self.storage_settings["snapshot_margin_percent"]) / 100
        total = usage.bytes + int(usage.bytes * margin)
        # The restore candidate is created beside the live data directory, so the
        # requirement belongs to that filesystem, not the snapshot filesystem.
        return StorageRequirement(
            self.data,
            total,
            "restore staging, retained live data and migration headroom",
            usage.inodes + 1024,
        )

    def updater_requirement(self, archive_size: int) -> list[StorageRequirement]:
        archive_bytes = archive_size if archive_size > 0 else 64 * MIB
        return [
            StorageRequirement(
                self.updater_root.parent,
                archive_bytes * 2 + 64 * MIB,
                "updater archive staging and coherent utility replacement",
                10000,
            )
        ]

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
                    "bytes": 0,
                    "files": 0,
                    "labels": [],
                    "paths": set(),
                },
            )
            group["bytes"] += max(0, int(requirement.bytes))
            group["files"] += max(0, int(requirement.inodes))
            group["labels"].append(requirement.label)
            group["paths"].add(str(requirement.path))
        headroom = int(self.storage_settings["headroom_mib"]) * MIB
        inode_reserve = int(self.storage_settings["minimum_free_inodes"])
        failures = []
        for group in groups.values():
            required_bytes = group["bytes"] + headroom
            required_inodes = group["files"] + inode_reserve
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
        path = Path(path)
        cached = self.verification.get(path)
        if cached == "complete":
            return "complete", "verified during this operation"
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
        if not (path / "data").is_dir() or not (path / "config").is_dir():
            return "incomplete", "missing data or configuration directory"
        manifest = path / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            return "incomplete", "missing manifest"
        try:
            self.verify_snapshot(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return "invalid", "manifest verification failed: " + describe_exception(error)
        database = path / "data/hnefatafl.sqlite3"
        if database.is_file() and not database.is_symlink():
            try:
                with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        return "invalid", "SQLite integrity check failed"
            except sqlite3.Error as error:
                return "invalid", "SQLite integrity check failed: " + describe_exception(error)
        self.verification.put(path, "complete")
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

    def _backup_roots(self) -> list[RootIdentity]:
        roots = []
        if self.backups.exists() and self.backups != self.legacy_backups:
            roots.append(
                self.fs.validate_root(
                    self.backups,
                    owner=self.privileged_uid,
                    description="backup directory",
                    mode=0o700,
                )
            )
        return roots

    def _data_owner(self) -> int:
        try:
            return os.lstat(self.data).st_uid
        except OSError:
            return self.privileged_uid

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
            self.ensure_layout()
        state, state_ok, state_reason = self._load_metadata(self.state, "installed state")
        if state_ok and state is not None:
            state_ok, state_reason = self.validate_state_metadata(state)
        journal, journal_ok, journal_reason = self._load_metadata(
            self.journal, "transaction journal"
        )
        if journal_ok and journal is not None:
            journal_ok, journal_reason = self.validate_transaction_metadata(journal)
        unfinished = bool(journal and journal.get("phase") not in {"committed", "recovered"})
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

        release_entries = self._release_entries()
        protected_release_count = 0
        protected_versions = set()
        complete_candidates = []
        for entry in release_entries:
            path = entry["path"]
            key = self._path_key(str(path))
            kind = entry["kind"]
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
                if age < INCOMPLETE_GRACE_SECONDS:
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
        if not dry_run:
            # Validate the backup root before planning any destructive cleanup.
            self._backup_roots()
        search_roots = [self.backups]
        if self.legacy_backups.exists() and self.legacy_backups != self.backups:
            search_roots.append(self.legacy_backups)
        for root in search_roots:
            try:
                backup_children = sorted(root.iterdir(), key=lambda item: item.name)
            except (FileNotFoundError, OSError):
                backup_children = []
            for path in backup_children:
                try:
                    info = path.lstat()
                except OSError as error:
                    add(
                        "backup",
                        "skip",
                        path,
                        "unreadable entry: " + describe_exception(error),
                        status="unrecognized",
                    )
                    continue
                identity = (int(info.st_dev), int(info.st_ino))
                key = self._path_key(str(path))
                if STAGING_DIRECTORY.fullmatch(path.name):
                    marker = self.fs.read_staging_marker(path)
                    age = max(0.0, now - info.st_mtime)
                    if marker is None:
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
                status, detail = self._backup_status(path)
                entry_status = {
                    "complete": "verified",
                    "incomplete": "incomplete",
                    "invalid": "invalid",
                    "unrecognized": "unrecognized",
                }.get(status, "unrecognized")
                if status == "complete":
                    backup_entries.append((path, info.st_mtime, identity))
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
            for path, _mtime, _identity in backup_entries
            if self._path_key(str(path)) in protected_backup_map
        )
        backup_slots = max(0, int(self.retention["backups"]) - protected_backup_count)
        backup_entries.sort(key=lambda item: (item[1], item[0].name), reverse=True)
        unprotected_backups = []
        for path, mtime, identity in backup_entries:
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
                unprotected_backups.append((path, mtime, identity))
        for index, (path, _mtime, identity) in enumerate(unprotected_backups):
            if index < backup_slots:
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
                    "beyond configured backup retention",
                    status="verified",
                    identity=identity,
                )
        if protected_backup_count > int(self.retention["backups"]):
            note(
                f"retention exception: {protected_backup_count} verified backups are "
                f"protected but the configured limit is {self.retention['backups']}; "
                "no protected backup was removed"
            )

        self._plan_interrupted_restores(report, add, journal, journal_ok, now)

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
                usage = scan_usage(candidate.path)
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
            report.reclaimable_bytes += usage.bytes
            if dry_run:
                continue
            root = location_root(candidate)
            try:
                self._safe_remove(
                    root,
                    candidate.path,
                    identity=candidate.identity,
                    owners=owners_for(candidate),
                )
            except (OSError, DeploymentError) as error:
                candidate.action = "failed"
                candidate.reason = describe_exception(error)
                report.failed.append(candidate)
                report.errors.append(f"Cannot remove {candidate.path}: {describe_exception(error)}")
                report.cleanup_complete = False
            else:
                candidate.action = "removed"
                report.removed.append(candidate)

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
    ) -> None:
        """Remove one validated artifact below a validated managed root."""
        expected = owners or {self.privileged_uid}
        description = f"managed directory {Path(root)}"
        root_identity = self.fs.validate_root(
            Path(root),
            owner=self.privileged_uid,
            description=description,
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
            expected_owner=int(info.st_uid),
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
                if report.dry_run and entry.action == "remove":
                    verb = "WOULD REMOVE"
                label = f"{entry.version} " if entry.version else ""
                status = f" [{entry.status}]" if entry.status else ""
                size = f" ({format_bytes(entry.bytes)})" if entry.bytes else ""
                print(f"  {verb} {label}{entry.path}: {entry.reason}{status}{size}")
        if report.hardlink_bytes:
            print(
                "Hardlinked content in selected artifacts: "
                f"{format_bytes(report.hardlink_bytes)} (counted per copy)"
            )
        print(f"Estimated apparent reclamation: {format_bytes(report.reclaimable_bytes)}")
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

    def _remove_orphan_staging(self) -> None:
        """Remove only marked staging artifacts from an interrupted operation.

        The global deployment lock is held, so no other updater operation owns these
        paths. A directory name alone is never sufficient: the marker written when the
        staging directory was created must be present and valid.
        """
        journal, journal_valid, _reason = self._load_metadata(self.journal, "transaction journal")
        protected_restore = set()
        if journal_valid and isinstance(journal, dict):
            restore = journal.get("restore")
            if isinstance(restore, dict):
                for key in ("staging", "retained"):
                    value = restore.get(key)
                    if isinstance(value, str) and value:
                        protected_restore.add(str(Path(value)))
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
                if pattern.fullmatch(path.name) is None:
                    continue
                if str(path) in protected_restore:
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
        "update": "scripts/linux/update.sh",
        "backup": "scripts/linux/backup.sh",
        **{name: f"hnefatafl/{name}.py" for name in UPDATER_SUPPORT_MODULES},
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
                if not member.isfile() or member.issym() or member.islnk():
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
    def _updater_abi(source: str) -> int | None:
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "UPDATER_ABI":
                        value = node.value
                        if isinstance(value, ast.Constant) and isinstance(value.value, int):
                            return int(value.value)
        return None

    def _installed_updater_abi(self) -> int:
        value, valid, _reason = self._load_metadata(self.updater_state, "installed updater state")
        if valid and value is not None:
            abi = value.get("abi")
            if isinstance(abi, int) and not isinstance(abi, bool) and abi >= 1:
                return abi
        return 1

    def _validate_updater_payload(self, payload: dict[str, bytes]) -> int:
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
        abi = self._updater_abi(module_source)
        installed = self._installed_updater_abi()
        if abi is None or abi < 1:
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
        for name in UPDATER_SUPPORT_MODULES:
            member = f"hnefatafl/{name}.py"
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
        for wrapper in ("scripts/linux/update.sh", "scripts/linux/backup.sh"):
            content = payload.get(wrapper)
            if not content:
                raise DeploymentError(f"The candidate updater payload is missing {wrapper}")
            try:
                text = content.decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                raise DeploymentError(
                    f"Refusing to install invalid updater wrapper {wrapper}"
                ) from error
            if not text.strip() or "\x00" in text:
                raise DeploymentError(f"Refusing to install invalid updater wrapper {wrapper}")
        return abi

    def _stage_updater_payload(self, archive: Path, tag: str, digest: str) -> Path:
        """Persist authenticated updater sources in root-controlled storage."""
        self.updater_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        if os.geteuid() == 0:
            os.chown(self.updater_root, 0, 0)
        payload = self._read_archive_members(archive, self.UPDATER_MEMBERS)
        abi = self._validate_updater_payload(payload)
        token = re.sub(r"[^a-f0-9]", "", digest.lower())[-16:] or secrets.token_hex(8)
        staging = self.updater_root / (".pending-" + token)
        if staging.exists():
            self._remove_pending_updater(staging)
        os.mkdir(staging, 0o700)
        files = {}
        for member, content in payload.items():
            destination = staging / member
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if os.geteuid() == 0:
                os.chown(destination, 0, 0)
            files[member] = hashlib.sha256(content).hexdigest()
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
        os.chmod(staging / "manifest.json", 0o600)
        print(f"Staged trusted deployment utility from verified release archive {tag}.")
        return staging

    def _remove_pending_updater(self, staging: Path) -> None:
        staging = Path(staging)
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

    def _updater_publication_plan(self, staging: Path) -> list[tuple[Path, Path, int]]:
        manifest, valid, reason = self._load_metadata(
            Path(staging) / "manifest.json", "updater staging manifest"
        )
        if not valid or manifest is None:
            raise DeploymentError(
                f"Refusing to publish an unverified updater set: {reason or 'missing manifest'}"
            )
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise DeploymentError("Updater staging manifest has no file digest map")
        payload = self._read_archive_members_from_staging(staging, files)
        self._validate_updater_payload(payload)
        plan = [
            (staging / self.UPDATER_MEMBERS["deployment"], self.updater_module, 0o644),
            (
                staging / self.UPDATER_MEMBERS["update"],
                self.updater_commands[0],
                0o755,
            ),
            (
                staging / self.UPDATER_MEMBERS["backup"],
                self.updater_commands[1],
                0o755,
            ),
        ]
        for name in UPDATER_SUPPORT_MODULES:
            plan.append(
                (staging / f"hnefatafl/{name}.py", self.updater_support / f"{name}.py", 0o644)
            )
        return plan

    def _read_archive_members_from_staging(self, staging: Path, expected: dict) -> dict[str, bytes]:
        payload = {}
        for member, digest in expected.items():
            if not isinstance(member, str) or not isinstance(digest, str):
                raise DeploymentError("Updater staging manifest contains an invalid entry")
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise DeploymentError("Updater staging manifest contains an invalid digest")
            if (
                member != "hnefatafl/deployment.py"
                and member not in {f"hnefatafl/{name}.py" for name in UPDATER_SUPPORT_MODULES}
                and member not in self.UPDATER_MEMBERS.values()
            ):
                raise DeploymentError(f"Updater staging contains an unexpected member {member}")
            path = Path(staging) / member
            try:
                info = os.lstat(path)
            except OSError as error:
                raise DeploymentError(
                    f"Updater staging member is missing ({member}): {describe_exception(error)}"
                ) from error
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise DeploymentError(f"Updater staging member {member} is not a regular file")
            if info.st_uid != self.privileged_uid or info.st_mode & 0o022:
                raise DeploymentError(
                    f"Updater staging member {member} is writable by another account"
                )
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise DeploymentError(f"Updater staging member {member} changed after staging")
            payload[member] = content
        for name in UPDATER_SUPPORT_MODULES:
            member = f"hnefatafl/{name}.py"
            if member not in payload:
                raise DeploymentError(f"Updater staging is missing restore support {member}")
        return payload

    def _publish_updater_plan(self, plan: list[tuple[Path, Path, int]], *, tag: str) -> None:
        self.fs.validate_root(
            self.updater_root,
            owner=self.privileged_uid,
            description="installed utility directory",
        )
        module_source = plan[0][0].read_text()
        abi = self._updater_abi(module_source)
        if abi is None:
            raise DeploymentError("The candidate deployment utility has no protocol version")
        token = secrets.token_hex(6)
        journal_path = self.updater_root / ".refresh.json"
        entries = []
        allowed_parents = {
            self.updater_root,
            self.updater_support,
            self.updater_commands[0].parent,
            self.updater_commands[1].parent,
        }
        for source, destination, mode in plan:
            if destination.parent not in allowed_parents:
                raise DeploymentError(
                    f"Refusing to publish a utility outside the managed root: {destination}"
                )
            if not destination.parent.exists():
                os.mkdir(destination.parent, 0o755)
                if os.geteuid() == 0:
                    os.chown(destination.parent, 0, 0)
            parent = self.fs.validate_root(
                destination.parent,
                owner=self.privileged_uid,
                description="installed utility directory",
            )
            if destination.is_symlink():
                raise DeploymentError(
                    f"Refusing to replace the symlinked utility destination {destination}"
                )
            entries.append(
                {
                    "source": str(source),
                    "destination": str(destination),
                    "mode": mode,
                    "temporary": str(destination.with_name(destination.name + ".new-" + token)),
                    "previous": str(destination.with_name(destination.name + ".previous-" + token)),
                    "parent_device": parent.device,
                    "parent_inode": parent.inode,
                    "replaced": False,
                    "had_previous": False,
                }
            )
        journal = {
            "schema": 1,
            "phase": "preparing",
            "tag": tag,
            "transaction": token,
            "entries": entries,
        }
        atomic_json(journal_path, journal)
        try:
            for entry in entries:
                destination = Path(str(entry["destination"]))
                source = Path(str(entry["source"]))
                if destination.exists():
                    shutil.copyfile(destination, str(entry["previous"]))
                    os.chmod(str(entry["previous"]), stat.S_IMODE(destination.stat().st_mode))
                    if os.geteuid() == 0:
                        os.chown(str(entry["previous"]), 0, 0)
                    entry["had_previous"] = True
                descriptor = os.open(
                    str(entry["temporary"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(source.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(str(entry["temporary"]), int(str(entry["mode"])))
                if os.geteuid() == 0:
                    os.chown(str(entry["temporary"]), 0, 0)
            journal["phase"] = "prepared"
            atomic_json(journal_path, journal)
            for index, entry in enumerate(entries):
                parent = self.fs.validate_root(
                    Path(str(entry["destination"])).parent,
                    owner=self.privileged_uid,
                    description="installed utility directory",
                )
                if (parent.device, parent.inode) != (
                    entry["parent_device"],
                    entry["parent_inode"],
                ):
                    raise DeploymentError(
                        "The installed utility directory changed identity during "
                        "publication; restoring the previous utility"
                    )
                os.replace(str(entry["temporary"]), str(entry["destination"]))
                entry["replaced"] = True
                journal["phase"] = "replacing"
                journal["completed"] = index + 1
                atomic_json(journal_path, journal)
            for directory in {Path(str(entry["destination"])).parent for entry in entries}:
                descriptor = os.open(directory, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            journal["phase"] = "committed"
            atomic_json(journal_path, journal)
        except BaseException:
            try:
                self._rollback_updater_journal(journal)
            except (OSError, DeploymentError) as rollback_error:
                raise DeploymentError(
                    "Utility replacement failed and the previous utility could not be "
                    f"fully restored: {describe_exception(rollback_error)}. The previous "
                    f"files are retained in {self.updater_root}; inspect and restore "
                    "them manually before running another update."
                ) from rollback_error
            raise
        finally:
            try:
                if journal_path.exists():
                    journal_path.unlink()
            except OSError:
                pass
        for entry in entries:
            previous = Path(str(entry["previous"]))
            if previous.exists() and not previous.is_symlink():
                previous.unlink(missing_ok=True)
        self._record_updater_state(tag, abi)
        print("Installed deployment utility refreshed from the verified release archive.")

    def _rollback_updater_journal(self, journal: dict) -> None:
        for entry in journal.get("entries", []):
            destination = Path(str(entry["destination"]))
            previous = Path(str(entry["previous"]))
            temporary = Path(str(entry["temporary"]))
            if entry.get("replaced") and entry.get("had_previous") and previous.exists():
                os.replace(previous, destination)
            elif entry.get("replaced") and not entry.get("had_previous"):
                destination.unlink(missing_ok=True)
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink(missing_ok=True)
            if previous.exists() and not previous.is_symlink():
                previous.unlink(missing_ok=True)
        journal_path = self.updater_root / ".refresh.json"
        try:
            journal_path.unlink(missing_ok=True)
        except OSError:
            pass

    def recover_updater_refresh(self) -> None:
        """Roll back an interrupted utility publication before anything else."""
        journal_path = self.updater_root / ".refresh.json"
        if not journal_path.exists():
            return
        journal, valid, reason = self._load_metadata(journal_path, "updater refresh journal")
        if not valid or journal is None:
            raise DeploymentError(
                f"An interrupted deployment utility refresh has unreadable metadata "
                f"({reason}). Refusing to continue until {journal_path} is inspected; "
                "the previous utility copies are retained beside the installed files."
            )
        for entry in journal.get("entries", []):
            if not isinstance(entry, dict):
                raise DeploymentError(
                    "The interrupted utility refresh journal is malformed; inspect it "
                    "manually before continuing"
                )
        response = journal.get("phase")
        if response == "committed":
            journal_path.unlink(missing_ok=True)
            return
        self._rollback_updater_journal(journal)
        print(
            "Recovered an interrupted deployment utility refresh by restoring the "
            "previous working utility."
        )

    def _record_updater_state(self, tag: str, abi: int) -> None:
        atomic_json(
            self.updater_state,
            {
                "schema": 1,
                "abi": abi,
                "version": tag,
                "refreshed_at": time.time(),
            },
        )

    def _refresh_installed_updater(self, target: Path) -> None:
        """Publish the updater set staged from the digest-verified archive."""
        if not self.manage_updater:
            return
        metadata, valid, reason = self._load_metadata(
            Path(target) / RELEASE_MARKER, "release metadata"
        )
        digest = metadata.get("sha256") if valid and metadata else None
        if not isinstance(digest, str):
            raise DeploymentError(
                "Refusing to refresh the installed utility without the verified archive "
                f"digest recorded in {Path(target) / RELEASE_MARKER}"
            )
        staging = self._pending_updater_for(digest)
        if staging is None:
            raise DeploymentError(
                "No trusted updater staging set was prepared from the verified release "
                "archive; refusing to copy a utility from the application-writable "
                "release tree. Re-run the update so preparation can stage it."
            )
        plan = self._updater_publication_plan(staging)
        self._preflight_or_reclaim(
            lambda: self.updater_requirement(0),
            phase="updater refresh",
            service_state="unchanged",
            protected_releases=set(),
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
        self.updater_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        if os.geteuid() == 0:
            os.chown(self.updater_root, 0, 0)
        self._preflight_or_reclaim(
            lambda: self.updater_requirement(archive_size),
            phase="updater refresh",
            service_state="unchanged",
            protected_releases=set(),
        )
        with tempfile.TemporaryDirectory(
            dir=self.updater_root.parent, prefix=".updater-download-"
        ) as temporary:
            archive = Path(temporary) / "release.tar.gz"
            digest = github.download(release, archive)
            metadata = tomllib.loads(
                self._read_archive_members(archive, {"pyproject": "pyproject.toml"})[
                    "pyproject.toml"
                ].decode("utf-8")
            )
            if metadata.get("project", {}).get("version") != tag:
                raise DeploymentError("Release tag and package version disagree")
            staging = self._stage_updater_payload(archive, tag, digest)
        plan = self._updater_publication_plan(staging)
        self._publish_updater_plan(plan, tag=tag)
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
        for path, description in (
            (self.data, "persistent data directory"),
            (self.etc, "configuration directory"),
        ):
            try:
                info = os.lstat(path)
            except OSError as error:
                raise DeploymentError(
                    f"Cannot inspect the {description} ({path}): {describe_exception(error)}"
                ) from error
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise DeploymentError(
                    f"The {description} ({path}) is not a real directory; refusing to "
                    "create a snapshot"
                )
        nested = self.fs.nested_mounts(self.data)
        if nested:
            print(
                "Snapshot notice: the data directory contains nested mount points ("
                + ", ".join(str(item) for item in nested[:3])
                + "); their content is included in the snapshot and counted in the "
                "storage estimate.",
                file=sys.stderr,
            )

    def _snapshot_association(self) -> dict:
        association = {
            "schema": 1,
            "created_at": time.time(),
            "version": None,
            "release": None,
            "release_sha256": None,
            "transaction": None,
            "database_schema": None,
            "updater_abi": UPDATER_ABI,
            "hnefatafl_data_dir": str(self.data),
        }
        try:
            current = self.current.resolve() if self.current.is_symlink() else None
        except (OSError, RuntimeError):
            current = None
        if current is not None:
            association["release"] = str(current)
            metadata, valid, _reason = self._load_metadata(
                Path(current) / RELEASE_MARKER, "release metadata"
            )
            if valid and metadata:
                version = metadata.get("version")
                if isinstance(version, str) and VERSION.fullmatch(version):
                    association["version"] = version
                digest = metadata.get("sha256")
                if isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    association["release_sha256"] = digest
        transaction, valid, _reason = self._load_metadata(self.journal, "transaction journal")
        if valid and transaction:
            target = transaction.get("target")
            if isinstance(target, str):
                association["transaction_target"] = target
        database = self.data / "hnefatafl.sqlite3"
        if database.is_file() and not database.is_symlink():
            try:
                with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                    association["database_schema"] = int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    )
            except sqlite3.Error:
                association["database_schema"] = None
        return association

    def snapshot(self) -> Path:
        self.ensure_layout()
        self._backup_root(write=True)
        self._assert_snapshot_sources_safe()
        backup = self.backups / (".staging-backup-" + secrets.token_hex(8))
        os.mkdir(backup, 0o700)
        self.fs.write_staging_marker(backup, "backup")
        try:
            shutil.copytree(
                self.data,
                backup / "data",
                ignore=self._snapshot_ignore,
                symlinks=True,
            )
            shutil.copytree(self.etc, backup / "config", symlinks=True)
            self._snapshot_database(backup)
            atomic_json(backup / SNAPSHOT_METADATA, self._snapshot_association())
            self._remove_staging_marker(backup)
            self._write_snapshot_manifest(backup)
            self.verify_snapshot(backup)
            final = self.backups / (time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4))
            os.replace(backup, final)
            os.chmod(final, 0o700)
        except BaseException:
            try:
                if backup.exists() and not backup.is_symlink():
                    self._safe_remove(
                        self.backups,
                        backup,
                        owners={self.privileged_uid, self._data_owner()},
                    )
            except (OSError, DeploymentError) as cleanup_error:
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
        self.verification.put(final, "complete")
        return final

    def _snapshot_ignore(self, directory, names):
        excluded = self._snapshot_excluded()
        return excluded & set(names) if Path(directory) == self.data else set()

    def _remove_staging_marker(self, directory: Path) -> None:
        marker = Path(directory) / STAGING_MARKER
        if marker.exists() and not marker.is_symlink():
            marker.unlink()

    def _snapshot_database(self, backup: Path) -> None:
        database = self.data / "hnefatafl.sqlite3"
        if not database.exists():
            return
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

    def _write_snapshot_manifest(self, backup: Path) -> None:
        manifest = {}
        for current, directories, names in os.walk(backup, topdown=True, followlinks=False):
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
                if not stat.S_ISREG(info.st_mode):
                    continue
                relative = entry.relative_to(backup).as_posix()
                if relative == "manifest.json":
                    continue
                manifest[relative] = checksum(entry)
                self.verification.hashed(info.st_size)
        atomic_json(backup / "manifest.json", manifest)

    def _backup_location_error(self, backup: Path) -> str:
        backup = Path(backup)
        try:
            info = os.lstat(backup)
        except OSError as error:
            return f"Cannot inspect the snapshot ({describe_exception(error)})"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return "The snapshot path is not a real directory"
        if info.st_uid != self.privileged_uid:
            return f"The snapshot is not root-owned (uid {info.st_uid})"
        if stat.S_IMODE(info.st_mode) & 0o077:
            return (
                f"The snapshot permissions are {stat.S_IMODE(info.st_mode):04o}, not root-only 0700"
            )
        try:
            parent = os.lstat(backup.parent)
        except OSError as error:
            return f"Cannot inspect the snapshot directory ({describe_exception(error)})"
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            return "The snapshot directory is not a real directory"
        if parent.st_uid != self.privileged_uid or stat.S_IMODE(parent.st_mode) & 0o022:
            return "The snapshot directory is writable by another account or not root-owned"
        return ""

    def verify_snapshot(self, backup):
        backup = Path(backup)
        location = self._backup_location_error(backup)
        if location:
            raise DeploymentError("Backup verification failed: " + location)
        if self.verification.get(backup) == "complete":
            return
        self.verification.verifications += 1
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
        for current, directories, names in os.walk(backup, topdown=True, followlinks=False):
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
                if not stat.S_ISREG(info.st_mode):
                    continue
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

    def verify_snapshot_usable(
        self,
        backup: Path,
        *,
        target: Path | None = None,
        previous: Path | None = None,
        transaction: str | None = None,
        allow_legacy: bool = True,
        service_state: str | None = "running",
    ) -> list[str]:
        """Full rollback/recovery preflight before any service interruption."""
        backup = Path(backup)
        warnings: list[str] = []
        location = self._backup_location_error(backup)
        if location:
            raise DeploymentError(
                f"Refusing to interrupt the service: the recorded snapshot cannot be "
                f"trusted. {location}. The service was not stopped and no release or "
                "data was changed."
            )
        if not backup.exists():
            raise DeploymentError(
                "Refusing to interrupt the service: the recorded rollback snapshot "
                f"does not exist ({backup}). The service was not stopped and no "
                "release or data was changed."
            )
        if self.fs.is_mount(backup):
            raise DeploymentError(
                "Refusing to interrupt the service: the recorded snapshot is a mount "
                "point, not a managed artifact"
            )
        try:
            self.verify_snapshot(backup)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise DeploymentError(
                "Refusing to interrupt the service: snapshot verification failed "
                f"({describe_exception(error)}). The service was not stopped and no "
                "release or data was changed."
            ) from error
        schema = None
        association, valid, reason = self._load_metadata(
            backup / SNAPSHOT_METADATA, "snapshot metadata"
        )
        if valid and association is not None:
            created_schema = association.get("database_schema")
            if (
                schema is not None
                and isinstance(created_schema, int)
                and not isinstance(created_schema, bool)
                and created_schema != schema
            ):
                raise DeploymentError(
                    "Refusing to interrupt the service: snapshot metadata records "
                    f"database schema {created_schema}, but the snapshot contains "
                    f"schema {schema}. The snapshot is inconsistent."
                )
            recorded_transaction = association.get("transaction")
            if transaction and recorded_transaction and recorded_transaction != transaction:
                raise DeploymentError(
                    "Refusing to interrupt the service: the snapshot belongs to a "
                    "different deployment transaction"
                )
            if target is not None:
                recorded_release = association.get("release")
                if isinstance(recorded_release, str) and recorded_release:
                    try:
                        recorded = Path(recorded_release).resolve()
                    except (OSError, RuntimeError):
                        recorded = Path(recorded_release)
                    if recorded != Path(target).resolve():
                        raise DeploymentError(
                            "Refusing to interrupt the service: the snapshot was "
                            f"created for release {recorded_release}, not {target}"
                        )
            recorded_version = association.get("version")
            expected_version = self.release_version(target) if target is not None else None
            if (
                expected_version
                and isinstance(recorded_version, str)
                and recorded_version != expected_version
            ):
                raise DeploymentError(
                    "Refusing to interrupt the service: the snapshot records release "
                    f"version {recorded_version}, but the rollback release is "
                    f"{expected_version}"
                )
        else:
            if not allow_legacy:
                raise DeploymentError(
                    "Refusing to interrupt the service: snapshot association metadata "
                    f"is unavailable ({reason or 'missing'}), so the snapshot cannot be "
                    "proven to belong to the recorded release. Perform a reviewed "
                    "manual recovery."
                )
            warnings.append(
                "Snapshot has legacy metadata without an explicit release/transaction "
                "association; it is accepted only because it is the exact snapshot "
                "recorded by the root-owned installed state."
            )
        database = backup / "data/hnefatafl.sqlite3"
        live = self.data / "hnefatafl.sqlite3"
        live_database_exists = live.is_file() and not live.is_symlink()
        if live_database_exists and (database.is_symlink() or not database.is_file()):
            raise DeploymentError(
                "Refusing to interrupt the service: the live installation has a "
                "database but the snapshot has no regular SQLite database"
            )
        if not live_database_exists and (database.is_symlink() or not database.is_file()):
            # A pre-database installation can still be recovered from its data and
            # configuration; there is no authoritative database to replace.
            self.check_storage(
                [self.restore_requirement(backup)],
                phase="rollback/restore preflight",
                service_state=service_state,
                persistent_data="unchanged until verification succeeds",
            )
            return warnings
        try:
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as saved:
                integrity = saved.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise DeploymentError(
                        "Refusing to interrupt the service: snapshot SQLite integrity "
                        f"check failed ({integrity})"
                    )
                schema = int(saved.execute("PRAGMA user_version").fetchone()[0])
            created_schema = (
                association.get("database_schema") if isinstance(association, dict) else None
            )
            if (
                isinstance(created_schema, int)
                and not isinstance(created_schema, bool)
                and created_schema != schema
            ):
                raise DeploymentError(
                    "Refusing to interrupt the service: snapshot metadata records "
                    f"database schema {created_schema}, but the snapshot contains "
                    f"schema {schema}. The snapshot is inconsistent."
                )
        except sqlite3.Error as error:
            raise DeploymentError(
                "Refusing to interrupt the service: snapshot SQLite could not be read "
                f"({describe_exception(error)})"
            ) from error
        live = self.data / "hnefatafl.sqlite3"
        if live.is_file() and not live.is_symlink():
            try:
                with closing(sqlite3.connect(f"file:{live}?mode=ro", uri=True)) as current:
                    live_schema = int(current.execute("PRAGMA user_version").fetchone()[0])
            except sqlite3.Error as error:
                raise DeploymentError(
                    "Refusing to interrupt the service: the live database cannot be "
                    f"read safely ({describe_exception(error)})"
                ) from error
            self._verify_restore_preflight(backup)
            if live_schema != schema:
                warnings.append(
                    f"Snapshot schema {schema} differs from live schema {live_schema}; "
                    "automatic restoration will be refused before replacing data."
                )
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
        if active_jobs(self.data):
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
        recovery_error = None
        original: BaseException | None = None
        with self.write_gate():
            if active_jobs(self.data):
                raise DeploymentError(
                    "New training or tournament activity appeared while draining HTTP "
                    "mutations; the backup was refused and the service is still running."
                )
            # Repeat the critical checks under the deployment gate before stopping.
            self._preflight_or_reclaim(
                self.backup_requirements,
                phase="manual backup",
                service_state="running",
                protected_releases=None,
            )
            if self._service_is_active():
                self.service("stop")
            try:
                self._preflight_or_reclaim(
                    self.backup_requirements,
                    phase="manual backup after service shutdown",
                    service_state="stopped",
                    protected_releases=None,
                )
                created = self.snapshot()
            except BaseException as error:
                original = error
                try:
                    self.service("start")
                    self._recovery_probe()
                except BaseException as restart_error:
                    recovery_error = restart_error
            if recovery_error is not None:
                assert original is not None
                raise DeploymentError(
                    "Manual backup failed: "
                    + describe_exception(original)
                    + "; automatic service recovery also failed: "
                    + describe_exception(recovery_error)
                    + ". The previously verified backups were preserved. Inspect "
                    "journalctl -u hnefatafl -n 200 --no-pager; the application may be "
                    "stopped."
                ) from recovery_error
            if original is not None:
                raise DeploymentError(
                    "Manual backup failed: "
                    + describe_exception(original)
                    + ". Only the incomplete staging artifact was removed; previously "
                    "verified backups were preserved and the service was restarted and "
                    "verified."
                ) from original
        assert created is not None
        report = self.cleanup(
            dry_run=False,
            automatic=True,
            protected_backups={created},
        )
        if report.removed:
            print(
                f"Retention removed {len(report.removed)} old artifact(s); actually "
                f"reclaimed {format_bytes(report.reclaimed_bytes)}."
            )
        if report.exception_reason:
            print(f"Retention note: {report.exception_reason}")
        print(f"Verified backup: {created}")
        return created

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

    def restore(
        self,
        backup: Path,
        *,
        journal: dict | None = None,
        transaction_id: str | None = None,
    ) -> Path:
        """Publish a verified snapshot with durable, resumable transaction phases."""
        backup = Path(backup)
        self.ensure_layout()
        self.verify_snapshot_usable(backup, service_state="stopped")
        token = transaction_id or secrets.token_hex(8)
        staging = self.data.with_name(".restore-" + token)
        retained = self.data.with_name(".retained-" + token)
        if staging.exists() or retained.exists():
            raise DeploymentError(
                f"Restore staging or retained directory already exists for transaction "
                f"{token}; run hnefatafl-update --recover to inspect the interrupted "
                "restore instead of starting a second one."
            )
        restore_meta = {
            "phase": "prepared",
            "transaction": token,
            "backup": str(backup),
            "data": str(self.data),
            "staging": str(staging),
            "retained": str(retained),
            "target": str(Path(self.current).resolve()) if self.current.is_symlink() else None,
            "previous": None,
        }

        def step(phase: str) -> None:
            restore_meta["phase"] = phase
            if journal is not None:
                journal["restore"] = dict(restore_meta)
                self.record(journal)

        if journal is not None:
            restore_meta["previous"] = journal.get("previous")
            journal["restore"] = dict(restore_meta)
            self.record(journal)
        try:
            os.mkdir(staging, 0o700)
            atomic_json(
                staging / RESTORE_MARKER,
                {
                    "schema": 1,
                    "transaction": token,
                    "backup": str(backup),
                    "created_at": time.time(),
                },
            )
            shutil.copytree(backup / "data", staging, dirs_exist_ok=True)
            self._prepare_restore_database(
                self.data / "hnefatafl.sqlite3", staging / "hnefatafl.sqlite3"
            )
            step("staged")
            gate = self.data / ".deployment-gate"
            if gate.exists():
                # Preserve the locked inode while replacing the surrounding data root.
                (staging / ".deployment-gate").unlink(missing_ok=True)
                gate.rename(staging / ".deployment-gate")
            step("stopping")
            self.data.rename(retained)
            step("data-renamed")
            staging.rename(self.data)
            step("published")
            self._chown_restored_data()
            step("ownership-set")
            print(f"Restored pre-deployment state. Newer data retained at {retained}.")
        except BaseException:
            if journal is None:
                raise
            # Leave the durable journal untouched: recovery decides using the
            # recorded phases and the on-disk marker identities, never by guessing.
            raise
        return retained

    def _chown_restored_data(self) -> None:
        if self.manage_updater:
            self.run(["chown", "-R", "hnefatafl:hnefatafl", self.data])
        elif os.geteuid() == 0 and self.data.exists():
            try:
                uid = self._data_owner()
                self.run(["chown", "-R", f"{uid}:{uid}", self.data])
            except (OSError, subprocess.CalledProcessError) as error:
                print(
                    "Restore ownership warning: "
                    f"{describe_exception(error)}. Verify ownership before starting the "
                    "service.",
                    file=sys.stderr,
                )

    def recover_restore(self, journal: dict) -> bool:
        """Resume or safely revert an interrupted restore transaction.

        Returns True when the recorded restore result is now the authoritative
        data directory, and False when only a disposable candidate was discarded.
        """
        restore_meta = journal.get("restore")
        if not isinstance(restore_meta, dict):
            return False
        transaction = restore_meta.get("transaction")
        data = Path(str(restore_meta.get("data") or self.data))
        staging = Path(str(restore_meta.get("staging") or ""))
        retained = Path(str(restore_meta.get("retained") or ""))
        if data != self.data:
            raise DeploymentError(
                "The transaction journal records a different data directory than the "
                "current configuration; refusing an ambiguous automatic restore. "
                "Perform reviewed manual recovery."
            )
        staged_marker = self._restore_marker(staging) if staging.exists() else None
        published_marker = self._restore_marker(self.data)
        if (
            restore_meta.get("phase") == "prepared"
            and not staging.exists()
            and not retained.exists()
            and published_marker is None
        ):
            # The transaction was recorded before any filesystem transition.
            journal["restore"] = None
            self.record(journal)
            return False
        if staged_marker and staged_marker.get("transaction") != transaction:
            raise DeploymentError(
                "The restore staging directory belongs to a different transaction; "
                "refusing ambiguous automatic restore"
            )
        if published_marker and published_marker.get("transaction") != transaction:
            raise DeploymentError(
                "The data directory contains a restore marker for a different "
                "transaction; refusing ambiguous automatic restore"
            )
        if self.data.exists() and retained.exists() and not published_marker:
            raise DeploymentError(
                "Both the data directory and a retained data directory exist, but the "
                "data directory is not the recorded restore result. Refusing to guess "
                "which data directory is authoritative; perform reviewed manual "
                "recovery."
            )
        if not self.data.exists() and staged_marker is not None:
            staging.rename(self.data)
            published_marker = staged_marker
            restore_meta["phase"] = "published"
            journal["restore"] = dict(restore_meta)
            self.record(journal)
        elif self.data.exists() and staged_marker is not None and not retained.exists():
            # Nothing was published; only the disposable candidate remains.
            self._safe_remove(
                self.data.parent,
                staging,
                identity=self.fs.identity_of(staging),
                owners={self.privileged_uid, self._data_owner()},
            )
            journal["restore"] = None
            self.record(journal)
            return False
        if published_marker is None:
            raise DeploymentError(
                "The interrupted restore has no verifiable published data directory; "
                "perform reviewed manual recovery. No automatic data replacement was "
                "attempted."
            )
        if not self.data.exists():
            raise DeploymentError(
                "The interrupted restore lost its data directory; perform reviewed manual recovery"
            )
        self._chown_restored_data()
        return True

    def finalize_restore(self, journal: dict) -> None:
        marker = self.data / RESTORE_MARKER
        try:
            if marker.is_file() and not marker.is_symlink():
                marker.unlink()
        except OSError as error:
            print(
                f"Restore marker cleanup warning: {describe_exception(error)}",
                file=sys.stderr,
            )
        journal["restore"] = None

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

    def _verify_release_environment(self, target: Path) -> None:
        """Verify the published environment still works at its permanent path.

        Virtual environments and generated entry points embed absolute paths. This
        release is prepared directly at its final path, so these checks are both a
        relocation regression test and the completion gate before publication.
        """
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
                self.run(command, cwd=target, user=self.manage_updater)
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

    def prepare(self, github: GitHub, release: dict) -> Path:
        self.releases.mkdir(parents=True, exist_ok=True)
        self.base.mkdir(parents=True, exist_ok=True)
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
        os.mkdir(final, 0o755)
        own_identity = self.fs.identity_of(final)
        self.fs.write_staging_marker(final, "release")
        published = False
        try:
            with tempfile.TemporaryDirectory(dir=self.base, prefix="download-") as temporary:
                archive = Path(temporary) / "release.tar.gz"
                sha256 = github.download(release, archive)
                if self.manage_updater:
                    self.updater_root.mkdir(parents=True, exist_ok=True, mode=0o755)
                    self._stage_updater_payload(archive, tag, sha256)
                extract(archive, final)
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
            self.run(["chown", "-R", "hnefatafl:hnefatafl", final])
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
            )
            self.run(["npm", "--prefix", "frontend", "ci"], cwd=final, user=True)
            self.run(["npm", "--prefix", "frontend", "run", "build"], cwd=final, user=True)
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
            )
            peak = scan_usage(final)
            self._prune_build_outputs(final)
            size = scan_usage(final)
            self._remove_staging_marker(final)
            self._release_size_cache = {}
            self.run(["chown", "-R", "root:root", final])
            self.run(["chmod", "-R", "a+rX,a-w", final])
            self._verify_release_environment(final)
            release_metadata = {
                "version": tag,
                "sha256": sha256,
                "prepared_at": time.time(),
                "peak_bytes": peak.bytes,
                "peak_inodes": peak.inodes,
                "size_bytes": size.bytes,
                "size_inodes": size.inodes,
                "file_count": size.files,
                "updater_abi": UPDATER_ABI,
            }
            # Publication: the completion marker is the single atomic transition
            # from "incomplete preparation" to "usable release".
            atomic_json(final / RELEASE_MARKER, release_metadata)
            os.chmod(final / RELEASE_MARKER, 0o644)
            published = True
        except BaseException:
            try:
                if not published and final.exists() and not final.is_symlink():
                    # This operation created the directory and holds the deployment
                    # lock. The captured identity makes a concurrent replacement
                    # detectable instead of deleting a different directory.
                    self._safe_remove(
                        self.releases,
                        final,
                        identity=own_identity,
                        owners={self.privileged_uid, self._data_owner()},
                    )
            except (OSError, DeploymentError) as cleanup_error:
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
        self._deployment_transaction_started = False
        try:
            with self.write_gate():
                return self._deploy_locked(target, initialize=initialize)
        except BaseException:
            if not getattr(self, "_deployment_transaction_started", False):
                self._cleanup_refused_deployment(target)
            raise

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
        self.ensure_layout()
        self.recover_updater_refresh()
        self._remove_orphan_staging()
        if active_jobs(self.data):
            raise ValueError(
                "Active/queued training or tournament jobs exist. Cancel/finish jobs and pause remote workers first."
            )
        self.validate_configuration(target)
        old = self.current.resolve() if self.current.is_symlink() else None
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
        self._require_complete_release(target)
        # Refuse predictable storage failures while the application is still
        # running. The target is explicitly protected from storage cleanup.
        self.preflight_deployment(Path(target), service_state="running")
        # Utility refresh is a hard step now: the new journal phases and restore
        # metadata require a utility that understands them. It runs from the
        # root-controlled staging set extracted from the digest-verified archive,
        # never from the application-writable release tree. A failure aborts while
        # the service is still healthy.
        if self.manage_updater:
            self._refresh_installed_updater(target)
        transaction = {
            "phase": "prepared",
            "abi": UPDATER_ABI,
            "previous": str(old) if old else None,
            "target": str(target),
            "backup": None,
            "prior_state": old_state,
            "service_stopped": False,
            "migration_started": False,
            "data_modified": False,
            "restore": None,
        }
        self.record(transaction)
        self._deployment_transaction_started = True
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
            backup = self.snapshot()
            transaction["backup"] = str(backup)
            transaction["phase"] = "prepared"
            self.record(transaction)
            if old:
                deployment_step = "snapshot rollback association verification"
                self.verify_snapshot_usable(backup, target=old, service_state="stopped")
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
            transaction["phase"] = "activating"
            self.record(transaction)
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
            release_metadata = json.loads((target / RELEASE_MARKER).read_text())
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
            self.finalize_restore(transaction)
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
                    self.verify_snapshot_usable(
                        Path(str(transaction["backup"])),
                        target=old,
                        service_state="stopped",
                    )
                recovery_step = "snapshot restoration"
                if needs_restore:
                    self.restore(Path(str(transaction["backup"])), journal=transaction)
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
                self.finalize_restore(transaction)
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
        self.ensure_layout()
        self.recover_updater_refresh()
        source = self.state if rollback else self.journal
        transaction, valid, reason = self._load_metadata(source, "recovery metadata")
        if not valid or transaction is None:
            raise DeploymentError(
                f"Cannot recover from invalid {source.name}: {reason}. "
                "No service stop, data restore or release switch was attempted."
            )
        if rollback:
            valid_metadata, reason = self.validate_state_metadata(transaction)
        else:
            valid_metadata, reason = self.validate_transaction_metadata(transaction)
        if not valid_metadata:
            raise DeploymentError(
                f"Refusing recovery from ambiguous {source.name}: {reason}. "
                "No service stop, data restore or release switch was attempted. "
                "Inspect the root-owned metadata and perform reviewed manual recovery."
            )
        self._remove_orphan_staging()
        phase = transaction.get("phase")
        if not rollback and phase in {"committed", "recovered"} and not transaction.get("restore"):
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
                migrated = phase in {"migrating", "activated", "activating"}
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
        rollback_journal = None
        if rollback:
            # Record a durable journal for rollback too, so an interruption between
            # renames can be resumed deterministically instead of guessing which
            # data directory is authoritative.
            existing_journal, journal_valid, _reason = self._load_metadata(
                self.journal, "transaction journal"
            )
            if (
                journal_valid
                and isinstance(existing_journal, dict)
                and existing_journal.get("phase") not in {"committed", "recovered"}
                and existing_journal.get("restore")
            ):
                rollback_journal = existing_journal
            else:
                rollback_journal = {
                    "phase": "recovering",
                    "abi": UPDATER_ABI,
                    "previous": previous,
                    "target": transaction.get("target") or str(previous),
                    "backup": backup,
                    "prior_state": transaction,
                    "service_stopped": False,
                    "migration_started": True,
                    "data_modified": False,
                    "restore": None,
                }
                self.record(rollback_journal)
        warnings: list[str] = []
        already_restored = False
        if rollback and rollback_journal and rollback_journal.get("restore"):
            try:
                already_restored = self.recover_restore(rollback_journal)
            except (OSError, DeploymentError) as restore_error:
                raise DeploymentStepError(
                    "Interrupted rollback restore recovery failed: "
                    f"{describe_exception(restore_error)}. No further data was changed. "
                    f"Inspect {self.journal} and the {self.data.parent} retained "
                    "directories before manual recovery."
                ) from restore_error
        if not rollback and transaction.get("restore"):
            # Resume or complete an interrupted restore before anything else; the
            # journal records every expected filesystem transition.
            try:
                already_restored = self.recover_restore(transaction)
            except (OSError, DeploymentError) as restore_error:
                raise DeploymentStepError(
                    "Interrupted restore recovery failed: "
                    f"{describe_exception(restore_error)}. No release was activated and "
                    f"no further data was changed. Inspect {self.journal} and the "
                    f"{self.data.parent} retained directories before manual recovery."
                ) from restore_error
        service_running = self._service_is_active()
        if needs_restore and not already_restored:
            self._diagnostic_failure_phase = "rollback compatibility preflight"
            warnings = self.verify_snapshot_usable(
                Path(str(backup)),
                target=Path(str(previous)) if previous else None,
                allow_legacy=True,
                service_state="running" if service_running else "stopped",
            )
            restore_meta = transaction.get("restore")
            if not isinstance(restore_meta, dict):
                transaction["restore"] = {
                    "phase": "prepared",
                    "transaction": secrets.token_hex(8),
                    "backup": str(backup),
                    "data": str(self.data),
                    "staging": None,
                    "retained": None,
                    "target": str(previous) if previous else None,
                    "previous": str(previous) if previous else None,
                }
                if not rollback:
                    self.record(transaction)
        for warning in warnings:
            print(f"Recovery note: {warning}")
        recovery_step = "checking current service"
        try:
            current = self.current.resolve() if self.current.is_symlink() else None
            already_healthy = False
            if (
                not needs_restore
                and previous
                and current == Path(str(previous))
                and transaction.get("phase") in {"committed", "recovered"}
            ):
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
                if needs_restore and not already_restored:
                    recovery_step = "snapshot restoration"
                    self.restore(
                        Path(str(backup)),
                        journal=rollback_journal if rollback else transaction,
                    )
                    already_restored = True
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
                            **json.loads((Path(previous) / RELEASE_MARKER).read_text()),
                            "version": self.release_version(Path(previous)),
                            "target": str(previous),
                            "previous": None,
                            "backup": None,
                            "phase": "committed",
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
                            **json.loads((Path(previous) / RELEASE_MARKER).read_text()),
                            "version": previous_version,
                            "target": str(previous),
                            "previous": None,
                            "backup": None,
                            "phase": "committed",
                        },
                    )
                elif not previous:
                    self.state.unlink(missing_ok=True)
            if rollback:
                if rollback_journal is not None:
                    self.finalize_restore(rollback_journal)
                    rollback_journal["phase"] = "recovered"
                    self.record(rollback_journal)
                else:
                    self.record({"phase": "recovered"})
            else:
                self.finalize_restore(transaction)
                transaction["phase"] = "recovered"
                self.record(transaction)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, ReadinessError):
                raise
            detail = self._safe_failure_detail(error)
            recovery_hint = (
                " The restore transaction journal is preserved; rerun --recover after "
                "inspecting it."
                if transaction.get("restore")
                else ""
            )
            raise DeploymentStepError(
                f"Recovery failed during {recovery_step}: {detail}."
                f"{recovery_hint} Inspect {self.journal} and "
                "journalctl -u hnefatafl -n 200 --no-pager."
            ) from error
        self._post_operation_cleanup("recovery")


def protected_json(path: Path, *, owner: int | None = None):
    info = path.stat()
    expected = os.geteuid() if owner is None else owner
    if info.st_uid != expected or info.st_mode & 0o077 or path.is_symlink():
        raise ValueError(f"Configuration must be an owner-controlled 0600 regular file: {path}")
    return json.loads(path.read_text())


def _open_deployment_lock(path: Path | None = None) -> int:
    """Open the shared deployment lock without truncating dry-run evidence."""
    path = Path(path or "/run/lock/hnefatafl-update.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)


LAYOUT_MARKER = ".deployment-test-layout"


def _disposable_layout(root: Path) -> dict:
    """Resolve an explicit disposable integration-test layout.

    The marker file is mandatory so a mistyped or hostile path cannot silently
    redirect the CLI away from the fixed production installation.
    """
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Layout root is not a directory: {root}")
    marker = root / LAYOUT_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(
            f"Refusing --layout-root {root}: the {LAYOUT_MARKER} marker is missing. "
            "This option exists only for disposable integration tests."
        )
    return {
        "config_dir": root / "etc",
        "base": root / "opt",
        "data": root / "data",
        "etc": root / "etc",
        "unit_file": root / "unit.service",
        "backups": root / "backups",
        "lock": root / "update.lock",
        "updater_root": root / "usr/lib/hnefatafl",
    }


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


def main(argv=None, *, layout=None):
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
    parser.add_argument(
        "--layout-root",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.cleanup:
        parser.error("--dry-run is only supported with --cleanup")
    if layout is not None and args.layout_root is not None:
        parser.error("Provide either a layout mapping or --layout-root, not both")
    if layout is None and args.layout_root is not None:
        layout = _disposable_layout(args.layout_root)
    if os.geteuid() != 0 and layout is None:
        parser.error("Run this deployment command as root")
    os.umask(0o077)

    def interrupted(*_):
        raise InterruptedError("Deployment interrupted; inspect the transaction journal")

    signal.signal(signal.SIGTERM, interrupted)
    paths = layout or _production_layout()
    config_dir = Path(paths["config_dir"])
    lock_descriptor = _open_deployment_lock(Path(paths["lock"]))
    lock = os.fdopen(lock_descriptor, "r+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another installation/update/backup is running") from None
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
        # An interrupted utility refresh is rolled back before any other privileged
        # work, so the installed utility set is never a mixed generation.
        deployment.recover_updater_refresh()
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
        transaction, transaction_valid, transaction_reason = deployment._load_metadata(
            deployment.journal, "transaction journal"
        )
        if deployment.journal.exists() and (
            not transaction_valid
            or (
                isinstance(transaction, dict)
                and transaction.get("phase") not in {"committed", "recovered"}
            )
        ):
            raise ValueError(
                "Interrupted transaction found. Run hnefatafl-update --recover before continuing"
                + (f" ({transaction_reason})" if not transaction_valid else "")
            )
        if args.backup:
            deployment.backup()
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
        try:
            lock.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

if __name__ == "__main__":
    raise SystemExit(main())
