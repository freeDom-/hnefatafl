"""Pinned browser-analysis runtime distribution.

The browser runtime (Pyodide, ONNX Runtime Web, the unchanged Python engine and
the cooperative search bridges) is a large optional artifact. It is staged
outside the application release directory, bound by a deterministic manifest and
served from the application's own origin. Nothing here is loaded at application
startup unless the browser-analysis feature flag is enabled and a verified
runtime is active.

Design boundaries:

* :mod:`hnefatafl.browser_runtime` owns distribution, manifests and verification.
* :mod:`hnefatafl.browser_models` owns explicit model publication.
* The frontend owns browser storage, downloads, capability detection and the
  runtime lifecycle (see ``frontend/src/browserAnalysis``).

The staged runtime is a production subset of the validated browser spike. The
worker and client JavaScript files are distributed verbatim from the spike (one
validated implementation), while the spike's benchmark, parity and fixture
control actions are not staged: the production bridge modules expose only the
bounded analysis and search protocol.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable

from .browser_bridge import BRIDGE_PROTOCOL
from .browser_pins import (
    ENGINE_MODULES,
    JS_ASSETS,
    LICENSE_ASSETS,
    LIMITS,
    MAX_RUNTIME_ASSET_BYTES,
    MAX_RUNTIME_FILES,
    REQUIRED_RUNTIME_FILES,
    MAX_MANIFEST_BYTES,
    MAX_RUNTIME_BYTES,
    ORT_ASSETS,
    ORT_CDN,
    ORT_VERSION,
    PYODIDE_ASSETS,
    PYODIDE_CDN,
    PYODIDE_VERSION,
    ROOT_MODULES,
    SPIKE_RUNTIME_JS,
)
from .encoding import ENCODER_VERSION
from .mcts_driver import DRIVER_VERSION, SEARCH_ALGORITHM, SEARCH_PROTOCOL
from .models import ARCHITECTURE
from .versioning import application_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGED_RUNTIME_ENV = "HNEFATAFL_BROWSER_RUNTIME_DIR"

RUNTIME_MANIFEST_SCHEMA = 1
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"
RUNTIME_DIRECTORY = "browser_runtime"
RUNTIME_ID = re.compile(r"[0-9a-f]{16}\Z")


class BrowserRuntimeError(Exception):
    """Base class for browser-runtime distribution failures."""


class RuntimeUnavailable(BrowserRuntimeError):
    """No verified runtime is active (missing, disabled or stale)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class RuntimeAssetError(BrowserRuntimeError):
    """A staged runtime manifest or asset is missing, corrupt or incompatible."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _gzip_bytes(data: bytes) -> int:
    return len(gzip.compress(data, mtime=0))


def _ensure_within(root: Path, relative: str) -> Path:
    """Resolve a manifest path, rejecting traversal and absolute paths."""
    if not isinstance(relative, str) or not relative or len(relative) > 512:
        raise RuntimeAssetError("invalid-path", "Runtime asset path is invalid")
    if relative.startswith("/") or "\\" in relative or "\x00" in relative:
        raise RuntimeAssetError("invalid-path", "Runtime asset path must be relative")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise RuntimeAssetError("invalid-path", "Runtime asset path is invalid")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise RuntimeAssetError("invalid-path", "Runtime asset path escapes the runtime directory")
    return candidate


def _copy_verified(source: Path, target: Path, expected: str, label: str) -> dict[str, Any]:
    if not source.is_file():
        raise RuntimeAssetError("missing-asset", f"Missing pinned runtime asset: {label}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    actual = _digest(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeAssetError("digest-mismatch", f"Pinned runtime digest mismatch: {label}")
    return {"path": label, "bytes": target.stat().st_size, "sha256": actual}


def _fetch_verified(url: str, target: Path, expected: str, label: str) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - pinned URL
            data = response.read(MAX_RUNTIME_BYTES + 1)
    except OSError as error:
        raise RuntimeAssetError("download-failed", f"Cannot fetch {label}: {error}") from error
    if len(data) > MAX_RUNTIME_BYTES:
        raise RuntimeAssetError("oversized-asset", f"Runtime asset is too large: {label}")
    actual = digest_bytes(data)
    if actual != expected:
        raise RuntimeAssetError("digest-mismatch", f"Pinned runtime digest mismatch: {label}")
    target.write_bytes(data)
    return {"path": label, "bytes": len(data), "sha256": actual}


def _asset_source(
    name: str,
    *,
    cache: Path | None,
    subdirectory: str,
    fetch: bool,
    url: str,
    expected: str,
    target: Path,
) -> dict[str, Any]:
    label = f"{subdirectory}/{name}"
    if cache is not None:
        return _copy_verified(cache / name, target, expected, label)
    if not fetch:
        raise RuntimeAssetError(
            "missing-asset",
            f"No cached {label}; pass --asset-cache or --fetch to stage pinned assets",
        )
    return _fetch_verified(url, target, expected, label)


def _engine_source(name: str, root: Path) -> Path:
    path = root / "hnefatafl" / name
    if not path.is_file():
        raise RuntimeAssetError("missing-source", f"Missing engine module: {name}")
    return path


def _js_source(name: str, root: Path) -> Path:
    path = root / SPIKE_RUNTIME_JS / name
    if not path.is_file():
        raise RuntimeAssetError("missing-source", f"Missing browser runtime asset: {name}")
    return path


def _write_json(path: Path, payload: dict[str, Any], *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        text = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def _inventory(root: Path, files: Iterable[str]) -> list[dict[str, Any]]:
    inventory = []
    for relative in sorted(files):
        path = _ensure_within(root, relative)
        data = path.read_bytes()
        inventory.append(
            {
                "path": relative,
                "bytes": len(data),
                "sha256": digest_bytes(data),
                "gzip_bytes": _gzip_bytes(data),
            }
        )
    return inventory


def _runtime_id(inventory: list[dict[str, Any]], pins: dict[str, Any]) -> str:
    identity = sorted(
        ([entry["path"], entry["sha256"]] for entry in inventory), key=lambda item: item[0]
    )
    return digest_bytes(_canonical({"files": identity, "pins": pins}))[:16]


def stage_runtime(
    output: Path,
    *,
    asset_cache: Path | None = None,
    fetch: bool = False,
    source_root: Path | None = None,
    application_version: str | None = None,
    development: bool = False,
) -> dict[str, Any]:
    """Stage a complete runtime under ``output`` and return its manifest.

    All pinned assets are digest-verified while copying. The function never
    leaves a partial manifest behind: the caller stages into a temporary
    directory and activates only after :func:`verify_runtime` succeeds.
    """
    root = (source_root or PROJECT_ROOT).resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeAssetError("output-not-empty", "Runtime staging directory must be empty")

    staged: list[str] = []
    runtime_inventory: list[dict[str, Any]] = []
    for name, expected in PYODIDE_ASSETS.items():
        entry = _asset_source(
            name,
            cache=asset_cache / "pyodide" if asset_cache else None,
            subdirectory="runtime",
            fetch=fetch,
            url=PYODIDE_CDN.format(version=PYODIDE_VERSION, name=name),
            expected=expected,
            target=output / "runtime" / name,
        )
        runtime_inventory.append(entry)
        staged.append(entry["path"])
    ort_inventory: list[dict[str, Any]] = []
    for name, expected in ORT_ASSETS.items():
        entry = _asset_source(
            name,
            cache=asset_cache / "ort" if asset_cache else None,
            subdirectory="ort",
            fetch=fetch,
            url=ORT_CDN.format(version=ORT_VERSION, name=name),
            expected=expected,
            target=output / "ort" / name,
        )
        ort_inventory.append(entry)
        staged.append(entry["path"])

    module_entries: list[dict[str, Any]] = []
    package = output / "python" / "hnefatafl"
    package.mkdir(parents=True, exist_ok=True)
    for name in ENGINE_MODULES:
        source = _engine_source(name, root)
        target = package / name
        shutil.copyfile(source, target)
        entry = {
            "path": f"python/hnefatafl/{name}",
            "bytes": target.stat().st_size,
            "sha256": _digest(target),
            "gzip_bytes": _gzip_bytes(target.read_bytes()),
        }
        module_entries.append(entry)
        staged.append(entry["path"])

    root_entries: list[dict[str, Any]] = []
    for staged_name, source_name in ROOT_MODULES.items():
        source = root / "hnefatafl" / source_name
        if not source.is_file():
            raise RuntimeAssetError("missing-source", f"Missing bridge module: {source_name}")
        target = output / staged_name
        shutil.copyfile(source, target)
        entry = {
            "path": staged_name,
            "bytes": target.stat().st_size,
            "sha256": _digest(target),
            "gzip_bytes": _gzip_bytes(target.read_bytes()),
        }
        root_entries.append(entry)
        staged.append(entry["path"])

    license_entries: list[dict[str, Any]] = []
    for staged_name, source_name in LICENSE_ASSETS.items():
        source = root / source_name
        if not source.is_file():
            raise RuntimeAssetError("missing-source", f"Missing license text: {source_name}")
        target = output / staged_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entry = {
            "path": staged_name,
            "bytes": target.stat().st_size,
            "sha256": _digest(target),
            "gzip_bytes": _gzip_bytes(target.read_bytes()),
        }
        license_entries.append(entry)
        staged.append(entry["path"])

    js_entries: list[dict[str, Any]] = []
    for name in JS_ASSETS:
        source = _js_source(name, root)
        target = output / name
        shutil.copyfile(source, target)
        entry = {
            "path": name,
            "bytes": target.stat().st_size,
            "sha256": _digest(target),
            "gzip_bytes": _gzip_bytes(target.read_bytes()),
        }
        js_entries.append(entry)
        staged.append(entry["path"])

    bundle_id = digest_bytes(_canonical([[e["path"], e["sha256"]] for e in module_entries]))[:16]
    search_manifest = {
        "schema": 1,
        "pyodide": PYODIDE_VERSION,
        "python_modules": list(ENGINE_MODULES),
        "root_modules": ["search_bridge.py"],
        "runtime_included": True,
        "bundle_id": bundle_id,
        "runtime_inventory": runtime_inventory,
        "inventory": module_entries + [e for e in root_entries if e["path"] == "search_bridge.py"],
    }
    _write_json(output / "search-assets.json", search_manifest)
    classical_manifest = {
        "schema": 1,
        "pyodide": PYODIDE_VERSION,
        "python_modules": list(ENGINE_MODULES),
        "root_modules": ["bridge.py"],
        "inventory": module_entries
        + [e for e in root_entries if e["path"] == "bridge.py"]
        + js_entries,
    }
    _write_json(output / "assets.json", classical_manifest)
    staged.extend(["search-assets.json", "assets.json"])

    package_version = application_version or application_metadata()["package_version"]
    pins = {
        "pyodide": PYODIDE_VERSION,
        "onnxruntime_web": ORT_VERSION,
        "encoder_version": ENCODER_VERSION,
        "driver_version": DRIVER_VERSION,
        "search_protocol": SEARCH_PROTOCOL,
        "search_algorithm": SEARCH_ALGORITHM,
        "bridge_protocol": BRIDGE_PROTOCOL,
        "architecture": ARCHITECTURE,
        "application_version": package_version,
    }
    inventory = _inventory(output, staged)
    runtime_id = _runtime_id(inventory, pins)
    total_bytes = sum(entry["bytes"] for entry in inventory)
    if total_bytes > MAX_RUNTIME_BYTES:
        raise RuntimeAssetError("oversized-runtime", "Staged runtime exceeds the size budget")
    manifest = {
        "schema": RUNTIME_MANIFEST_SCHEMA,
        "runtime_id": runtime_id,
        "pins": pins,
        "python_assets": runtime_inventory,
        "onnxruntime_assets": ort_inventory,
        "files": inventory,
        "total_bytes": total_bytes,
        "limits": LIMITS,
    }
    _write_json(output / RUNTIME_MANIFEST_NAME, manifest)
    verify_runtime(output, application_version=package_version, development=development)
    return manifest


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = directory / RUNTIME_MANIFEST_NAME
    if not path.is_file():
        raise RuntimeUnavailable("runtime-missing", "No staged runtime manifest is present")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise RuntimeUnavailable("runtime-missing", str(error)) from error
    if not 0 < size <= MAX_MANIFEST_BYTES:
        raise RuntimeAssetError("manifest-invalid", "Runtime manifest size is invalid")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeAssetError("manifest-invalid", "Runtime manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeAssetError("manifest-incompatible", "Unsupported runtime manifest schema")
    return manifest


def _require(manifest: dict[str, Any], key: str, kind: type) -> Any:
    value = manifest.get(key)
    if not isinstance(value, kind):
        raise RuntimeAssetError("manifest-invalid", f"Manifest field {key!r} is invalid")
    return value


_SAFE_RUNTIME_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _validated_pins(manifest: dict[str, Any]) -> dict[str, Any]:
    pins = _require(manifest, "pins", dict)
    expected = {
        "pyodide": PYODIDE_VERSION,
        "onnxruntime_web": ORT_VERSION,
        "encoder_version": ENCODER_VERSION,
        "driver_version": DRIVER_VERSION,
        "search_protocol": SEARCH_PROTOCOL,
        "search_algorithm": SEARCH_ALGORITHM,
        "bridge_protocol": BRIDGE_PROTOCOL,
        "architecture": ARCHITECTURE,
    }
    for key, value in expected.items():
        actual = pins.get(key)
        if type(actual) is not type(value) or actual != value:
            raise RuntimeAssetError(
                "runtime-incompatible", f"Runtime pin {key!r} is not the expected identity"
            )
    application_version = pins.get("application_version")
    if not isinstance(application_version, str) or not application_version:
        raise RuntimeAssetError("manifest-invalid", "Runtime application version is missing")
    return pins


def _validate_inventory(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = _require(manifest, "files", list)
    if not files or len(files) > MAX_RUNTIME_FILES:
        raise RuntimeAssetError("manifest-invalid", "Runtime file inventory is invalid")
    expected: dict[str, dict[str, Any]] = {}
    total = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeAssetError("manifest-invalid", "Runtime inventory entry is invalid")
        relative = entry.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative) > 512
            or not _SAFE_RUNTIME_PATH.match(relative)
            or ".." in relative.split("/")
            or relative in expected
        ):
            raise RuntimeAssetError("manifest-invalid", "Runtime inventory path is invalid")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise RuntimeAssetError("manifest-invalid", "Runtime inventory digest is invalid")
        size = entry.get("bytes")
        if type(size) is not int or size < 0 or size > MAX_RUNTIME_ASSET_BYTES:
            raise RuntimeAssetError("manifest-invalid", "Runtime inventory size is invalid")
        total += size
        expected[relative] = entry
    if RUNTIME_MANIFEST_NAME in expected:
        raise RuntimeAssetError("manifest-invalid", "Runtime manifest lists itself")
    declared_total = manifest.get("total_bytes")
    if type(declared_total) is not int or declared_total != total:
        raise RuntimeAssetError("manifest-invalid", "Runtime declared total size does not match")
    if total > MAX_RUNTIME_BYTES:
        raise RuntimeAssetError("oversized-runtime", "Runtime exceeds the aggregate size budget")
    missing = [path for path in REQUIRED_RUNTIME_FILES if path not in expected]
    if missing:
        raise RuntimeAssetError(
            "missing-asset", f"Runtime inventory is missing required file: {missing[0]}"
        )
    return expected


def _validate_upstream_assets(
    manifest: dict[str, Any], expected: dict[str, dict[str, Any]]
) -> None:
    """Bind the actual pinned upstream binaries independently of the manifest.

    A self-consistent manifest cannot prove that the expected Pyodide/ORT assets
    were included; every pinned name must be present in both the manifest tree and
    the declared upstream inventory with the recorded digest and size.
    """
    declarations = {
        "runtime": (_require(manifest, "python_assets", list), PYODIDE_ASSETS),
        "ort": (_require(manifest, "onnxruntime_assets", list), ORT_ASSETS),
    }
    for prefix, (declared_list, pins) in declarations.items():
        declared: dict[str, dict[str, Any]] = {}
        for entry in declared_list:
            if not isinstance(entry, dict):
                raise RuntimeAssetError("manifest-invalid", "Upstream inventory entry is invalid")
            path = entry.get("path")
            if not isinstance(path, str) or path in declared:
                raise RuntimeAssetError("manifest-invalid", "Upstream inventory path is invalid")
            declared[path] = entry
        for name, digest in pins.items():
            relative = f"{prefix}/{name}"
            entry = expected.get(relative)
            upstream = declared.get(relative)
            if entry is None or upstream is None:
                raise RuntimeAssetError(
                    "missing-asset", f"Runtime is missing pinned upstream asset: {relative}"
                )
            if (
                entry["sha256"] != digest
                or upstream.get("sha256") != digest
                or upstream.get("bytes") != entry["bytes"]
            ):
                raise RuntimeAssetError(
                    "digest-mismatch", f"Pinned upstream asset identity mismatch: {relative}"
                )


def verify_runtime(
    directory: str | Path,
    *,
    runtime_id: str | None = None,
    application_version: str | None = None,
    development: bool = False,
    file_cache: "VerifiedFileCache | None" = None,
) -> dict[str, Any]:
    """Verify a staged runtime against its manifest and the running application.

    Missing, modified, unexpected or incompatible files fail closed. The check
    covers the complete staged tree, including indirectly loaded WASM, Python
    modules and worker assets, and independently re-binds every pinned upstream
    binary digest. ``development`` explicitly permits the ``0+local`` source
    checkout identity; production releases never do.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeUnavailable("runtime-missing", "Runtime directory is missing")
    manifest = _load_manifest(directory)
    pins = _validated_pins(manifest)
    expected = _validate_inventory(manifest)

    expected_app = (
        application_version
        if application_version is not None
        else application_metadata()["package_version"]
    )
    if expected_app == "0+local":
        if not development:
            raise RuntimeAssetError(
                "runtime-incompatible",
                "A source checkout identity is development-only, not a production runtime",
            )
    if pins["application_version"] != expected_app:
        raise RuntimeAssetError(
            "runtime-stale", "Runtime was staged for a different application release"
        )

    _validate_upstream_assets(manifest, expected)

    recomputed = _runtime_id(list(expected.values()), pins)
    if manifest.get("runtime_id") != recomputed:
        raise RuntimeAssetError("manifest-invalid", "Runtime manifest identity does not match")
    if runtime_id is not None and runtime_id != recomputed:
        raise RuntimeAssetError("runtime-mismatch", "Requested runtime identity is not active")

    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise RuntimeAssetError("unexpected-asset", "Runtime directory contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    actual.discard(RUNTIME_MANIFEST_NAME)
    unknown = actual - set(expected)
    if unknown:
        raise RuntimeAssetError(
            "unexpected-asset", f"Unexpected runtime asset: {sorted(unknown)[0]}"
        )
    cache = file_cache or VerifiedFileCache()
    for relative, entry in expected.items():
        cache.verify(directory, relative, entry)
    return manifest


def _file_signature(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        getattr(st, "st_ctime_ns", 0),
    )


def _hash_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


class VerifiedFileCache:
    """Bounded stat-signature cache for per-request asset integrity checks.

    The complete tree is hashed once when a snapshot is loaded. Individual HTTP
    requests re-check size and a stat signature and reuse the verified digest;
    any changed inode/size/mtime forces a re-hash and a mismatch fails closed.
    This keeps same-size replacement detectable without hashing the distribution
    on every request.
    """

    def __init__(self, max_entries: int = 128) -> None:
        self._entries: dict[str, tuple[tuple[int, int, int, int, int], str]] = {}
        self._max = max_entries
        self._lock = threading.Lock()

    def verify(self, directory: Path, relative: str, entry: dict[str, Any]) -> Path:
        path = self._lstat_components(directory, relative)
        try:
            before = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise RuntimeAssetError(
                "missing-asset", f"Runtime asset is missing: {relative}"
            ) from error
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAssetError("invalid-path", "Runtime asset is not a regular file")
        if before.st_size != entry["bytes"]:
            raise RuntimeAssetError("size-mismatch", f"Runtime asset size mismatch: {relative}")
        signature = _file_signature(before)
        key = f"{directory}\0{relative}"
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and cached[0] == signature and cached[1] == entry["sha256"]:
            return path
        actual = _digest(path)
        try:
            after = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise RuntimeAssetError(
                "missing-asset", f"Runtime asset changed during verification: {relative}"
            ) from error
        if _file_signature(after) != signature:
            raise RuntimeAssetError(
                "digest-mismatch", f"Runtime asset changed during verification: {relative}"
            )
        if actual != entry["sha256"]:
            with self._lock:
                self._entries.pop(key, None)
            raise RuntimeAssetError("digest-mismatch", f"Runtime asset digest mismatch: {relative}")
        with self._lock:
            if len(self._entries) >= self._max and key not in self._entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (signature, actual)
        return path

    def _lstat_components(self, directory: Path, relative: str) -> Path:
        path = _ensure_within(directory, relative)
        current = directory
        for part in relative.split("/"):
            current = current / part
            try:
                st = os.lstat(current)
            except OSError as error:
                raise RuntimeAssetError(
                    "missing-asset", f"Runtime asset is missing: {relative}"
                ) from error
            if stat.S_ISLNK(st.st_mode):
                raise RuntimeAssetError("invalid-path", "Runtime asset path uses a symlink")
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise RuntimeAssetError("invalid-path", "Runtime asset is not a regular file")
        return path

    def open_verified(
        self, directory: Path, relative: str, entry: dict[str, Any]
    ) -> tuple[Any, int]:
        """Open the exact verified file and return (file object, size).

        The descriptor is opened once and streamed, so replacement between
        verification and response opening cannot substitute different bytes.
        """
        path = self._lstat_components(directory, relative)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as error:
            raise RuntimeAssetError(
                "missing-asset", f"Runtime asset cannot be opened: {relative}"
            ) from error
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeAssetError("invalid-path", "Runtime asset is not a regular file")
            if before.st_size != entry["bytes"]:
                raise RuntimeAssetError("size-mismatch", f"Runtime asset size mismatch: {relative}")
            signature = _file_signature(before)
            key = f"{directory}\0{relative}"
            with self._lock:
                cached = self._entries.get(key)
            if cached is None or cached[0] != signature or cached[1] != entry["sha256"]:
                actual = _hash_fd(fd)
                after = os.fstat(fd)
                if _file_signature(after) != signature:
                    raise RuntimeAssetError(
                        "digest-mismatch", f"Runtime asset changed during verification: {relative}"
                    )
                if actual != entry["sha256"]:
                    raise RuntimeAssetError(
                        "digest-mismatch", f"Runtime asset digest mismatch: {relative}"
                    )
                with self._lock:
                    if len(self._entries) >= self._max and key not in self._entries:
                        self._entries.pop(next(iter(self._entries)))
                    self._entries[key] = (signature, actual)
            os.lseek(fd, 0, os.SEEK_SET)
            return os.fdopen(fd, "rb"), before.st_size
        except BaseException:
            os.close(fd)
            raise


def open_runtime_asset(
    snapshot: dict[str, Any],
    relative: str,
    *,
    file_cache: VerifiedFileCache | None = None,
) -> tuple[Any, dict[str, Any], int]:
    """Open one immutable snapshot asset for streaming from the verified descriptor."""
    entry = snapshot["files"].get(relative)
    if entry is None:
        raise RuntimeAssetError("unknown-asset", "Runtime asset is not in the manifest")
    cache = file_cache or VerifiedFileCache()
    handle, size = cache.open_verified(snapshot["directory"], relative, entry)
    return handle, entry, size


def runtime_snapshot(
    data_dir: str | Path,
    runtime_id: str,
    *,
    application_version: str | None = None,
    development: bool = False,
    file_cache: VerifiedFileCache | None = None,
) -> dict[str, Any]:
    """Load and verify one immutable runtime identity (never the ``current`` link)."""
    if not isinstance(runtime_id, str) or not RUNTIME_ID.fullmatch(runtime_id):
        raise RuntimeAssetError("runtime-mismatch", "Runtime identity is invalid")
    directory = runtime_root(data_dir) / runtime_id
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeUnavailable("runtime-missing", "Runtime identity is not staged")
    manifest = verify_runtime(
        directory,
        runtime_id=runtime_id,
        application_version=application_version,
        development=development,
        file_cache=file_cache,
    )
    inventory = {entry["path"]: entry for entry in manifest["files"]}
    return {
        "runtime_id": runtime_id,
        "directory": directory,
        "manifest": manifest,
        "files": inventory,
    }


def resolve_runtime_asset(
    snapshot: dict[str, Any],
    relative: str,
    *,
    file_cache: VerifiedFileCache | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Resolve one asset inside the immutable snapshot identity."""
    entry = snapshot["files"].get(relative)
    if entry is None:
        raise RuntimeAssetError("unknown-asset", "Runtime asset is not in the manifest")
    cache = file_cache or VerifiedFileCache()
    path = cache.verify(snapshot["directory"], relative, entry)
    return path, entry


def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Projection safe to expose to browser clients (no filesystem paths)."""
    pins = dict(manifest.get("pins") or {})
    files = [
        {"path": entry["path"], "bytes": entry["bytes"], "sha256": entry["sha256"]}
        for entry in manifest.get("files", [])
        if isinstance(entry, dict)
    ]
    return {
        "schema": manifest.get("schema"),
        "runtime_id": manifest.get("runtime_id"),
        "pins": pins,
        "files": files,
        "total_bytes": sum(entry["bytes"] for entry in files),
        "limits": manifest.get("limits") or LIMITS,
        "base_path": f"/browser-analysis/runtime/{manifest.get('runtime_id')}",
    }


def runtime_root(data_dir: str | Path) -> Path:
    return Path(data_dir).resolve() / RUNTIME_DIRECTORY


def active_runtime_identity(data_dir: str | Path) -> str:
    """Read the activated runtime identity without following it to a directory."""
    root = runtime_root(data_dir)
    current = root / "current"
    if not current.is_symlink():
        raise RuntimeUnavailable("runtime-missing", "No browser runtime is active")
    try:
        target = os.readlink(current)
    except OSError as error:
        raise RuntimeUnavailable("runtime-missing", str(error)) from error
    if not RUNTIME_ID.fullmatch(target):
        raise RuntimeAssetError("runtime-mismatch", "Active runtime identity is invalid")
    if not (root / target).is_dir():
        raise RuntimeUnavailable("runtime-missing", "Active runtime directory is missing")
    return target


def active_runtime_directory(data_dir: str | Path) -> Path:
    return runtime_root(data_dir) / active_runtime_identity(data_dir)


def load_active_runtime(
    data_dir: str | Path,
    *,
    application_version: str | None = None,
    development: bool = False,
    file_cache: VerifiedFileCache | None = None,
) -> dict[str, Any]:
    """Return the verified active manifest or raise :class:`RuntimeUnavailable`."""
    try:
        snapshot = runtime_snapshot(
            data_dir,
            active_runtime_identity(data_dir),
            application_version=application_version,
            development=development,
            file_cache=file_cache,
        )
    except RuntimeUnavailable:
        raise
    except RuntimeAssetError as error:
        raise RuntimeUnavailable("runtime-invalid", error.detail) from error
    return snapshot["manifest"]


def activate_runtime(
    data_dir: str | Path,
    directory: str | Path,
    *,
    application_version: str | None = None,
    development: bool = False,
) -> dict[str, Any]:
    """Atomically point ``current`` at a verified runtime directory."""
    root = runtime_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(directory).resolve()
    manifest = verify_runtime(
        directory, application_version=application_version, development=development
    )
    if directory.parent != root.resolve():
        raise RuntimeAssetError(
            "runtime-mismatch", "Runtime directory must live under the runtime root"
        )
    temporary = root / f".current-{os.getpid()}-{uuid.uuid4().hex}"
    temporary.symlink_to(directory.name)
    os.replace(temporary, root / "current")
    return manifest


def build_runtime(
    data_dir: str | Path,
    *,
    asset_cache: Path | None = None,
    fetch: bool = False,
    activate: bool = False,
    source_root: Path | None = None,
    application_version: str | None = None,
    development: bool = False,
) -> dict[str, Any]:
    """Stage, verify and optionally activate a runtime for ``data_dir``."""
    root = runtime_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        manifest = stage_runtime(
            temporary,
            asset_cache=asset_cache,
            fetch=fetch,
            source_root=source_root,
            application_version=application_version,
            development=development,
        )
        target = root / manifest["runtime_id"]
        pinned_version = manifest["pins"]["application_version"]
        if target.exists():
            verify_runtime(
                target,
                runtime_id=manifest["runtime_id"],
                application_version=pinned_version,
                development=development,
            )
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, target)
        verify_runtime(
            target,
            runtime_id=manifest["runtime_id"],
            application_version=pinned_version,
            development=development,
        )
        if activate:
            activate_runtime(
                data_dir,
                target,
                application_version=pinned_version,
                development=development,
            )
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def prune_runtimes(data_dir: str | Path, keep: int = 2) -> list[str]:
    """Remove inactive runtime directories, retaining the active and newest ones."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    root = runtime_root(data_dir)
    if not root.is_dir():
        return []
    active: Path | None = None
    try:
        active = active_runtime_directory(data_dir)
    except RuntimeUnavailable:
        active = None
    candidates = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    retained = {path for path in candidates if active is None or path == active}
    for path in candidates:
        if len(retained) >= keep:
            break
        retained.add(path)
    removed = []
    for path in candidates:
        if path not in retained:
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage and verify the browser-analysis runtime")
    parser.add_argument("--data-dir", type=Path, help="Application data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Stage a pinned runtime")
    build.add_argument("--asset-cache", type=Path, help="Directory with pyodide/ and ort/")
    build.add_argument("--fetch", action="store_true", help="Fetch pinned assets from the CDN")
    build.add_argument("--activate", action="store_true", help="Activate after verification")
    build.add_argument("--source-root", type=Path, help="Repository root override")
    build.add_argument("--application-version", help="Release version bound into the manifest")
    build.add_argument(
        "--development",
        action="store_true",
        help="Permit the 0+local source-checkout identity (never for production)",
    )

    verify = sub.add_parser("verify", help="Verify a staged runtime")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--application-version")
    verify.add_argument("--development", action="store_true")

    activate = sub.add_parser("activate", help="Verify and activate a staged runtime")
    activate.add_argument("directory", type=Path)
    activate.add_argument("--development", action="store_true")

    status = sub.add_parser("status", help="Report the active runtime")
    status.add_argument("--development", action="store_true")
    prune = sub.add_parser("prune", help="Remove inactive runtimes")
    prune.add_argument("--keep", type=int, default=2)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            if args.data_dir is None:
                parser.error("--data-dir is required")
            manifest = build_runtime(
                args.data_dir,
                asset_cache=args.asset_cache,
                fetch=args.fetch,
                activate=args.activate,
                source_root=args.source_root,
                application_version=args.application_version,
                development=args.development,
            )
            print(json.dumps(public_manifest(manifest), indent=2))
            return 0
        if args.command == "verify":
            manifest = verify_runtime(args.directory, application_version=args.application_version)
            print(json.dumps(public_manifest(manifest), indent=2))
            return 0
        if args.command == "activate":
            if args.data_dir is None:
                parser.error("--data-dir is required")
            manifest = activate_runtime(args.data_dir, args.directory, development=args.development)
            print(json.dumps(public_manifest(manifest), indent=2))
            return 0
        if args.command == "status":
            if args.data_dir is None:
                parser.error("--data-dir is required")
            try:
                manifest = load_active_runtime(args.data_dir, development=args.development)
            except RuntimeUnavailable as error:
                print(json.dumps({"available": False, "reason": error.reason}))
                return 3
            print(json.dumps({"available": True, **public_manifest(manifest)}, indent=2))
            return 0
        if args.command == "prune":
            if args.data_dir is None:
                parser.error("--data-dir is required")
            removed = prune_runtimes(args.data_dir, keep=args.keep)
            print(json.dumps({"removed": removed}))
            return 0
    except RuntimeUnavailable as error:
        print(json.dumps({"available": False, "reason": error.reason, "detail": error.detail}))
        return 3
    except RuntimeAssetError as error:
        print(json.dumps({"available": False, "reason": error.reason, "detail": error.detail}))
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
