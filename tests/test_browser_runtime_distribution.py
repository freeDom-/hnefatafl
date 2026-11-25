"""Pinned browser-runtime distribution: manifests, verification and activation.

These tests use synthetic Pyodide/ORT files with patched pinned digests, so no
network access or large artifacts are required. The real pin table is checked
separately for shape and documented versions.
"""

import hashlib
import json
from pathlib import Path

import pytest

from hnefatafl import browser_pins, browser_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.9.9"


def _synthetic_cache(tmp_path: Path, monkeypatch) -> Path:
    cache = tmp_path / "cache"
    pyodide = {}
    ort = {}
    for name in browser_pins.PYODIDE_ASSETS:
        data = f"pyodide:{name}".encode()
        (cache / "pyodide").mkdir(parents=True, exist_ok=True)
        (cache / "pyodide" / name).write_bytes(data)
        pyodide[name] = hashlib.sha256(data).hexdigest()
    for name in browser_pins.ORT_ASSETS:
        data = f"ort:{name}".encode()
        (cache / "ort").mkdir(parents=True, exist_ok=True)
        (cache / "ort" / name).write_bytes(data)
        ort[name] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(browser_runtime, "PYODIDE_ASSETS", pyodide)
    monkeypatch.setattr(browser_runtime, "ORT_ASSETS", ort)
    return cache


def _stage(tmp_path: Path, monkeypatch, *, data_dir: Path | None = None) -> dict:
    cache = _synthetic_cache(tmp_path, monkeypatch)
    data_dir = data_dir or tmp_path / "data"
    return browser_runtime.build_runtime(
        data_dir,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=VERSION,
    )


def test_real_pin_table_is_complete_and_digest_shaped():
    for table in (browser_pins.PYODIDE_ASSETS, browser_pins.ORT_ASSETS):
        assert table
        for name, digest in table.items():
            assert name and "/" not in name
            assert len(digest) == 64 and int(digest, 16) >= 0
    assert browser_pins.PYODIDE_VERSION == "0.27.7"
    assert browser_pins.ORT_VERSION == "1.22.0"
    assert "ort-wasm-simd-threaded.jsep.wasm" in browser_pins.ORT_ASSETS
    assert browser_pins.ORT_WASM_ONLY <= set(browser_pins.ORT_ASSETS)


def test_staging_produces_a_deterministic_verified_manifest(tmp_path, monkeypatch):
    cache = _synthetic_cache(tmp_path, monkeypatch)
    data = tmp_path / "data"
    first = browser_runtime.build_runtime(
        data,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=VERSION,
    )
    second = browser_runtime.build_runtime(
        data,
        asset_cache=cache,
        source_root=PROJECT_ROOT,
        application_version=VERSION,
    )
    assert first["runtime_id"] == second["runtime_id"]
    assert first["pins"]["application_version"] == VERSION
    assert first["pins"]["encoder_version"] == "six-plane-v1"
    assert first["pins"]["search_protocol"] == 1
    paths = {entry["path"] for entry in first["files"]}
    # The runtime manifest covers runtime, engine, bridge, worker and client
    # files. Development-only spike artifacts never enter production output.
    assert "runtime/pyodide.asm.wasm" in paths
    assert "ort/ort.min.js" in paths
    assert "python/hnefatafl/engine.py" in paths
    assert "search_bridge.py" in paths
    assert "bridge.py" in paths
    assert "search-worker.js" in paths
    assert "search-assets.json" in paths
    # Required third-party license texts are part of the distributed artifact set.
    assert "licenses/PYODIDE-MPL-2.0.txt" in paths
    assert "licenses/ONNXRUNTIME-MIT.txt" in paths
    assert "licenses/PYTHON-PSF-2.0.txt" in paths
    for forbidden in (
        "index.html",
        "app.js",
        "neural-worker-test.js",
        "jarl_benchmark.py",
        "fixtures.json",
        "sw.js",
    ):
        assert forbidden not in paths
    directory = data / "browser_runtime" / first["runtime_id"]
    bridge = (directory / "bridge.py").read_text()
    search_bridge = (directory / "search_bridge.py").read_text()
    assert "fixtures" not in bridge and "jarl_benchmark" not in bridge
    for action in ('"parity"', "benchmark_workload", "def _parity"):
        assert action not in search_bridge
    assert (data / "browser_runtime" / "current").is_symlink()
    # The runtime manifest is deterministic: no timestamps or unordered fields.
    text = (directory / "runtime-manifest.json").read_text()
    assert VERSION in text
    assert "created_at" not in text


def test_verify_rejects_every_tampering_class(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    data = tmp_path / "data"
    directory = data / "browser_runtime" / manifest["runtime_id"]
    runtime_id = manifest["runtime_id"]

    target = directory / "runtime" / "pyodide.asm.wasm"
    original = target.read_bytes()
    target.write_bytes(original + b"x")
    with pytest.raises(browser_runtime.RuntimeAssetError, match="size mismatch|digest"):
        browser_runtime.verify_runtime(
            directory, runtime_id=runtime_id, application_version=VERSION
        )
    target.write_bytes(original)

    (directory / "extra.bin").write_bytes(b"unexpected")
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(
            directory, runtime_id=runtime_id, application_version=VERSION
        )
    assert error.value.reason == "unexpected-asset"
    (directory / "extra.bin").unlink()

    (directory / "ort" / "ort.min.js").unlink()
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(
            directory, runtime_id=runtime_id, application_version=VERSION
        )
    assert error.value.reason == "missing-asset"

    manifest_path = directory / "runtime-manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["pins"]["application_version"] = "0.0.1"
    manifest_path.write_text(json.dumps(payload))
    with pytest.raises(browser_runtime.RuntimeAssetError, match="different application release"):
        browser_runtime.verify_runtime(
            directory, runtime_id=runtime_id, application_version=VERSION
        )

    # An old manifest cannot activate a newer asset set: the identity is bound
    # to the file digests, not only to the recorded directory name.
    with pytest.raises(browser_runtime.RuntimeAssetError):
        browser_runtime.verify_runtime(directory, runtime_id="0" * 16)


def test_verify_rejects_symlinks_and_unknown_manifest_schemas(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "browser_runtime" / manifest["runtime_id"]
    (directory / "escape").symlink_to(PROJECT_ROOT / "README.md")
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(directory, application_version=VERSION)
    assert error.value.reason == "unexpected-asset"
    (directory / "escape").unlink()

    manifest_path = directory / "runtime-manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["schema"] = 999
    manifest_path.write_text(json.dumps(payload))
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(directory, application_version=VERSION)
    assert error.value.reason == "manifest-incompatible"


def test_staging_fails_closed_without_assets_or_on_digest_mismatch(tmp_path, monkeypatch):
    cache = _synthetic_cache(tmp_path, monkeypatch)
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.build_runtime(
            tmp_path / "data",
            source_root=PROJECT_ROOT,
            application_version=VERSION,
        )
    assert error.value.reason == "missing-asset"
    assert not (tmp_path / "data" / "browser_runtime" / "current").exists()

    (cache / "ort" / "ort.min.js").write_bytes(b"corrupted")
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.build_runtime(
            tmp_path / "data",
            asset_cache=cache,
            source_root=PROJECT_ROOT,
            application_version=VERSION,
        )
    assert error.value.reason == "digest-mismatch"
    root = tmp_path / "data" / "browser_runtime"
    assert not any(path.is_dir() and not path.name.startswith(".") for path in root.iterdir())


def test_asset_resolution_is_an_exact_manifest_allowlist(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    data = tmp_path / "data"
    snapshot = browser_runtime.runtime_snapshot(
        data, manifest["runtime_id"], application_version=VERSION
    )
    path, entry = browser_runtime.resolve_runtime_asset(snapshot, "runtime/pyodide.js")
    assert path.is_file()
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    for relative in (
        "../runtime-manifest.json",
        "runtime/../bridge.py",
        "runtime/./pyodide.js",
        "/etc/passwd",
        "unknown.js",
        "runtime\\pyodide.js",
    ):
        with pytest.raises(browser_runtime.RuntimeAssetError):
            browser_runtime.resolve_runtime_asset(snapshot, relative)


def test_load_active_runtime_reports_stale_and_missing_states(tmp_path, monkeypatch):
    with pytest.raises(browser_runtime.RuntimeUnavailable) as error:
        browser_runtime.load_active_runtime(tmp_path / "empty")
    assert error.value.reason == "runtime-missing"

    manifest = _stage(tmp_path, monkeypatch)
    loaded = browser_runtime.load_active_runtime(tmp_path / "data", application_version=VERSION)
    assert loaded["runtime_id"] == manifest["runtime_id"]
    with pytest.raises(browser_runtime.RuntimeUnavailable) as error:
        browser_runtime.load_active_runtime(tmp_path / "data", application_version="0.0.2")
    assert error.value.reason == "runtime-invalid"


def test_pruning_keeps_the_active_runtime(tmp_path, monkeypatch):
    data = tmp_path / "data"
    first = _stage(tmp_path, monkeypatch, data_dir=data)
    cache = tmp_path / "cache"
    (cache / "pyodide" / "pyodide.js").write_bytes(b"changed")
    pyodide = dict(browser_runtime.PYODIDE_ASSETS)
    pyodide["pyodide.js"] = hashlib.sha256(b"changed").hexdigest()
    monkeypatch.setattr(browser_runtime, "PYODIDE_ASSETS", pyodide)
    second = browser_runtime.build_runtime(
        data,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=VERSION,
    )
    assert second["runtime_id"] != first["runtime_id"]
    removed = browser_runtime.prune_runtimes(data, keep=1)
    assert removed == [first["runtime_id"]]
    assert browser_runtime.active_runtime_directory(data).name == second["runtime_id"]


def test_cli_status_and_verify_exit_statuses(tmp_path, monkeypatch, capsys):
    assert browser_runtime.main(["--data-dir", str(tmp_path / "data"), "status"]) == 3
    manifest = _stage(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "browser_runtime" / manifest["runtime_id"]
    assert (
        browser_runtime.main(
            [
                "--data-dir",
                str(tmp_path / "data"),
                "verify",
                str(directory),
                "--application-version",
                VERSION,
            ]
        )
        == 0
    )
    (directory / "bridge.py").write_text("# tampered\n")
    assert (
        browser_runtime.main(
            [
                "--data-dir",
                str(tmp_path / "data"),
                "verify",
                str(directory),
                "--application-version",
                VERSION,
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert '"available": false' in output
    assert '"reason": "size-mismatch"' in output


def test_public_projection_hides_filesystem_paths(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    public = browser_runtime.public_manifest(manifest)
    serialized = json.dumps(public)
    assert str(tmp_path) not in serialized
    assert "export_dir" not in serialized
    assert public["base_path"] == f"/browser-analysis/runtime/{manifest['runtime_id']}"
    assert public["pins"]["application_version"] == VERSION
    assert all(entry["path"] and not entry["path"].startswith("/") for entry in public["files"])


def _rewrite_manifest(directory: Path, mutate) -> dict:
    path = directory / "runtime-manifest.json"
    manifest = json.loads(path.read_text())
    mutate(manifest)
    pins = manifest["pins"]
    inventory = sorted(
        ([entry["path"], entry["sha256"]] for entry in manifest["files"]),
        key=lambda item: item[0],
    )
    canonical = json.dumps(
        {"files": inventory, "pins": pins}, sort_keys=True, separators=(",", ":")
    ).encode()
    manifest["runtime_id"] = hashlib.sha256(canonical).hexdigest()[:16]
    manifest["total_bytes"] = sum(entry["bytes"] for entry in manifest["files"])
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def test_required_inventory_and_pins_are_independently_validated(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    data = tmp_path / "data"
    directory = data / "browser_runtime" / manifest["runtime_id"]

    original = directory / "runtime-manifest.json"
    backup = original.read_text()

    def missing_required(payload):
        payload["files"] = [
            entry for entry in payload["files"] if entry["path"] != "neural-worker.js"
        ]

    _rewrite_manifest(directory, missing_required)
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(directory, application_version=VERSION)
    assert error.value.reason == "missing-asset"

    original.write_text(backup)
    payload = json.loads(backup)
    payload["runtime_id"] = manifest["runtime_id"]
    payload["pins"]["search_algorithm"] = "something-else"
    original.write_text(json.dumps(payload))
    with pytest.raises(browser_runtime.RuntimeAssetError, match="expected identity"):
        browser_runtime.verify_runtime(directory, application_version=VERSION)

    original.write_text(backup)
    payload = json.loads(backup)
    payload["python_assets"] = [
        entry for entry in payload["python_assets"] if entry["path"] != "runtime/pyodide.js"
    ]
    original.write_text(json.dumps(payload))
    with pytest.raises(browser_runtime.RuntimeAssetError, match="missing pinned upstream"):
        browser_runtime.verify_runtime(directory, application_version=VERSION)

    original.write_text(backup)
    payload = json.loads(backup)
    payload["total_bytes"] = payload["total_bytes"] - 1
    original.write_text(json.dumps(payload))
    with pytest.raises(browser_runtime.RuntimeAssetError, match="declared total"):
        browser_runtime.verify_runtime(directory, application_version=VERSION)


def test_self_consistent_manifest_cannot_substitute_upstream_binaries(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "browser_runtime" / manifest["runtime_id"]
    # Replace a pinned Pyodide binary with equal-length content and keep the
    # manifest fully self-consistent (file digest, declared upstream digest, id).
    target = directory / "runtime" / "pyodide.js"
    replacement = b"X" * target.stat().st_size
    target.write_bytes(replacement)
    digest = hashlib.sha256(replacement).hexdigest()

    def mutate(payload):
        for entry in payload["files"]:
            if entry["path"] == "runtime/pyodide.js":
                entry["sha256"] = digest
        for entry in payload["python_assets"]:
            if entry["path"] == "runtime/pyodide.js":
                entry["sha256"] = digest

    _rewrite_manifest(directory, mutate)
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.verify_runtime(directory, application_version=VERSION)
    assert error.value.reason == "digest-mismatch"


def test_source_checkout_identity_is_development_only(tmp_path, monkeypatch):
    cache = _synthetic_cache(tmp_path, monkeypatch)
    data = tmp_path / "data"
    manifest = browser_runtime.build_runtime(
        data,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version="0+local",
        development=True,
    )
    directory = data / "browser_runtime" / manifest["runtime_id"]
    with pytest.raises(browser_runtime.RuntimeAssetError, match="development-only"):
        browser_runtime.verify_runtime(directory, application_version="0+local")
    assert (
        browser_runtime.verify_runtime(directory, application_version="0+local", development=True)[
            "runtime_id"
        ]
        == manifest["runtime_id"]
    )
    with pytest.raises(browser_runtime.RuntimeUnavailable):
        browser_runtime.load_active_runtime(data, application_version="0+local")


def test_manifest_identity_is_order_independent(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "browser_runtime" / manifest["runtime_id"]
    path = directory / "runtime-manifest.json"
    payload = json.loads(path.read_text())
    payload["files"] = list(reversed(payload["files"]))
    payload["total_bytes"] = sum(entry["bytes"] for entry in payload["files"])
    path.write_text(json.dumps(payload))
    verified = browser_runtime.verify_runtime(directory, application_version=VERSION)
    assert verified["runtime_id"] == manifest["runtime_id"]


def test_immutable_snapshot_never_resolves_another_runtime(tmp_path, monkeypatch):
    data = tmp_path / "data"
    first = _stage(tmp_path, monkeypatch, data_dir=data)
    snapshot_a = browser_runtime.runtime_snapshot(
        data, first["runtime_id"], application_version=VERSION
    )
    old_bytes = (snapshot_a["directory"] / "runtime" / "pyodide.js").read_bytes()

    # A newer runtime with same-length but different content, then activation.
    cache = tmp_path / "cache"
    (cache / "pyodide" / "pyodide.js").write_bytes(b"Y" * len(old_bytes))
    pyodide = dict(browser_runtime.PYODIDE_ASSETS)
    pyodide["pyodide.js"] = hashlib.sha256(b"Y" * len(old_bytes)).hexdigest()
    monkeypatch.setattr(browser_runtime, "PYODIDE_ASSETS", pyodide)
    second = browser_runtime.build_runtime(
        data,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=VERSION,
    )
    assert second["runtime_id"] != first["runtime_id"]
    assert browser_runtime.active_runtime_identity(data) == second["runtime_id"]

    # The old snapshot still serves its own immutable bytes, never the new ones.
    path, _entry = browser_runtime.resolve_runtime_asset(snapshot_a, "runtime/pyodide.js")
    assert path.read_bytes() == old_bytes
    snapshot_b = browser_runtime.runtime_snapshot(
        data, second["runtime_id"], application_version=VERSION
    )
    new_path, _entry = browser_runtime.resolve_runtime_asset(snapshot_b, "runtime/pyodide.js")
    assert new_path.read_bytes() != old_bytes

    # A same-size replacement under A's identity is detected via the stat cache.
    path.write_bytes(b"Z" * len(old_bytes))
    with pytest.raises(browser_runtime.RuntimeAssetError) as error:
        browser_runtime.resolve_runtime_asset(snapshot_a, "runtime/pyodide.js")
    assert error.value.reason == "digest-mismatch"

    # Pruning removes the directory: retained URL becomes a controlled 404.
    browser_runtime.prune_runtimes(data, keep=1)
    with pytest.raises(browser_runtime.RuntimeAssetError):
        browser_runtime.resolve_runtime_asset(snapshot_a, "runtime/pyodide.js")


def test_runtime_assets_reject_symlinks(tmp_path, monkeypatch):
    manifest = _stage(tmp_path, monkeypatch)
    directory = tmp_path / "data" / "browser_runtime" / manifest["runtime_id"]
    snapshot = browser_runtime.runtime_snapshot(
        tmp_path / "data", manifest["runtime_id"], application_version=VERSION
    )
    target = directory / "runtime" / "pyodide.js"
    target.unlink()
    target.symlink_to(directory / "runtime" / "pyodide.asm.js")
    with pytest.raises(browser_runtime.RuntimeAssetError):
        browser_runtime.resolve_runtime_asset(snapshot, "runtime/pyodide.js")


def test_required_inventory_matches_the_typescript_contract():
    source = (PROJECT_ROOT / "frontend/src/browserAnalysis/contracts.ts").read_text()
    block = source[source.index("export const REQUIRED_RUNTIME_FILES") :]
    block = block[: block.index("] as const;")]
    ts_paths = set(__import__("re").findall(r"'([^']+)'", block))
    assert ts_paths == set(browser_pins.REQUIRED_RUNTIME_FILES)


def test_typescript_and_python_runtime_identity_agree(tmp_path, monkeypatch):
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is required for the cross-language identity contract")
    manifest = _stage(tmp_path, monkeypatch)
    public = browser_runtime.public_manifest(manifest)
    fixture = tmp_path / "runtime.json"
    fixture.write_text(json.dumps(public))
    script = """
const fs = require('fs');
const crypto = require('crypto');
const m = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
const files = m.files.map((entry) => [entry.path, entry.sha256])
  .sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));
const identity = canonical({ files, pins: m.pins });
process.stdout.write(crypto.createHash('sha256').update(identity).digest('hex').slice(0, 16));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout == manifest["runtime_id"]
