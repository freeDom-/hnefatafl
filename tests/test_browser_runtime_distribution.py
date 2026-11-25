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
    path, entry = browser_runtime.runtime_asset(data, manifest, "runtime/pyodide.js")
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
            browser_runtime.runtime_asset(data, manifest, relative)


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
