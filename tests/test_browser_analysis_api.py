"""Production browser-analysis delivery: authorization, CSP and artifact serving."""

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from hnefatafl import browser_pins, browser_runtime
from hnefatafl.auth import (
    BROWSER_ANALYSIS_POLICY,
    CONTENT_SECURITY_POLICY,
    COOKIE,
)
from hnefatafl.server import create_app
from hnefatafl.versioning import application_metadata

PASSWORD = "browser-analysis-test-password-1"
ORIGIN = {"Origin": "http://testserver"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_BYTES = b"published-browser-model" * 256


def _synthetic_runtime(data_dir: Path, monkeypatch) -> dict:
    cache = data_dir / "asset-cache"
    pyodide = {}
    for name in browser_pins.PYODIDE_ASSETS:
        payload = f"pyodide:{name}".encode()
        (cache / "pyodide").mkdir(parents=True, exist_ok=True)
        (cache / "pyodide" / name).write_bytes(payload)
        pyodide[name] = hashlib.sha256(payload).hexdigest()
    ort = {}
    for name in browser_pins.ORT_ASSETS:
        payload = f"ort:{name}".encode()
        (cache / "ort").mkdir(parents=True, exist_ok=True)
        (cache / "ort" / name).write_bytes(payload)
        ort[name] = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(browser_runtime, "PYODIDE_ASSETS", pyodide)
    monkeypatch.setattr(browser_runtime, "ORT_ASSETS", ort)
    return browser_runtime.build_runtime(
        data_dir,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=application_metadata()["package_version"],
    )


def _app(monkeypatch, tmp_path, *, enabled: bool, stage_runtime: bool = True):
    monkeypatch.setenv("HNEFATAFL_BROWSER_ANALYSIS_ENABLED", "1" if enabled else "0")
    if stage_runtime:
        _synthetic_runtime(tmp_path, monkeypatch)
    return create_app(tmp_path)


def _login(client, app, role="USER", name="Raven"):
    app.state.auth.create_account(name, name + "@example.test", PASSWORD, role)
    response = client.post(
        "/api/auth/login", json={"identity": name, "password": PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return client


def test_browser_analysis_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("HNEFATAFL_BROWSER_ANALYSIS_ENABLED", raising=False)
    app = create_app(tmp_path)
    client = _login(TestClient(app), app)
    response = client.get("/api/browser-analysis/availability")
    assert response.status_code == 200
    body = response.json()
    assert {
        key: body[key]
        for key in (
            "schema",
            "enabled",
            "runtime_available",
            "runtime",
            "reason",
            "published_models",
        )
    } == {
        "schema": 1,
        "enabled": False,
        "runtime_available": False,
        "runtime": None,
        "reason": "disabled",
        "published_models": 0,
    }
    assert body["analysis_mode"] == "server_only"
    assert body["execution"]["browser_execution"] is False
    assert body["execution"]["server_computation"] is True
    assert client.get("/api/browser-analysis/models").status_code == 404
    assert client.get("/api/browser-analysis/models/" + "0" * 24 + "/artifact").status_code == 404
    assert client.get("/browser-analysis/runtime/0/runtime/pyodide.js").status_code in (401, 404)
    policy = client.get("/api/health").headers["content-security-policy"]
    assert policy == CONTENT_SECURITY_POLICY
    assert "wasm-unsafe-eval" not in policy


def test_enabled_csp_adds_only_narrow_wasm_allowance(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True, stage_runtime=False)
    client = _login(TestClient(app), app)
    policy = client.get("/api/health").headers["content-security-policy"]
    assert policy == BROWSER_ANALYSIS_POLICY
    assert "script-src 'self' 'wasm-unsafe-eval'" in policy
    assert "worker-src 'self'" in policy
    assert "'unsafe-eval'" not in policy
    assert "blob:" not in policy
    assert "*" not in policy
    for directive in (
        "default-src 'self'",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ):
        assert directive in policy


def test_requests_require_authentication(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    client = TestClient(app)
    for path in (
        "/api/browser-analysis/availability",
        "/api/browser-analysis/models",
        "/api/browser-analysis/models/" + "0" * 24,
    ):
        assert client.get(path).status_code == 401, path
    # Ordinary page requests keep the existing authenticated-shell redirect.
    redirected = client.get(
        "/browser-analysis/runtime/0/runtime/pyodide.js", follow_redirects=False
    )
    assert redirected.status_code == 307
    assert redirected.headers["location"].startswith("/login")


def test_runtime_manifest_and_assets_are_served_from_the_app_origin(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    client = _login(TestClient(app), app)
    availability = client.get("/api/browser-analysis/availability").json()
    assert availability["enabled"] is True
    assert availability["runtime_available"] is True
    runtime = availability["runtime"]
    assert runtime["pins"]["pyodide"] == browser_pins.PYODIDE_VERSION
    assert runtime["pins"]["onnxruntime_web"] == browser_pins.ORT_VERSION
    assert runtime["pins"]["encoder_version"] == "six-plane-v1"
    assert str(tmp_path) not in json.dumps(runtime)
    base = runtime["base_path"]
    digest = next(
        entry["sha256"] for entry in runtime["files"] if entry["path"] == "runtime/pyodide.js"
    )
    asset = client.get(f"{base}/runtime/pyodide.js")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/javascript")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["x-content-sha256"] == digest
    assert hashlib.sha256(asset.content).hexdigest() == digest
    assert asset.headers["content-security-policy"] == BROWSER_ANALYSIS_POLICY
    wasm = client.get(f"{base}/runtime/pyodide.asm.wasm")
    assert wasm.status_code == 200
    assert wasm.headers["content-type"] == "application/wasm"
    worker = client.get(f"{base}/search-worker.js")
    assert worker.status_code == 200
    assert client.get(f"{base}/unknown.js").status_code == 404
    assert client.get(f"/browser-analysis/runtime/{'0' * 16}/runtime/pyodide.js").status_code == 404
    assert client.get(f"{base}/runtime/%2e%2e/bridge.py").status_code == 404
    assert client.get(f"{base}/runtime/bridge.py").status_code == 404


def test_published_models_are_listed_and_downloadable(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    store = app.state.browser_models
    # Publication itself is covered in the dedicated publication tests; inject a
    # completed publication record and immutable artifact here.
    digest = hashlib.sha256(MODEL_BYTES).hexdigest()
    (store.artifacts / f"{digest}.onnx").write_bytes(MODEL_BYTES)
    record = {
        "schema": 1,
        "publication_id": "1" * 24,
        "model_id": None,
        "display_name": "Brandubh assignment",
        "ruleset": "brandubh",
        "board_size": 7,
        "architecture": {"name": "tafl-policy-value-v1", "channels": 32, "blocks": 2},
        "encoder_version": "six-plane-v1",
        "source_weights_sha256": "a" * 64,
        "source_checkpoint_sha256": "b" * 64,
        "onnx_sha256": digest,
        "artifact_bytes": len(MODEL_BYTES),
        "runtime": {
            "runtime_manifest_schema": 1,
            "ort_version": browser_pins.ORT_VERSION,
            "min_opset": 13,
            "max_opset": 21,
            "encoder_version": "six-plane-v1",
            "search_protocol": 1,
            "bridge_protocol": 1,
        },
        "io": {"input": {"name": "planes"}, "outputs": {"policy_logits": None}},
        "validation": {},
        "status": "published",
        "published_at": "2026-11-25T00:00:00+00:00",
        "published_by": "Odin",
        "revoked_at": None,
        "revoked_by": None,
        "revocation_reason": None,
        "superseded_at": None,
        "superseded_by": None,
    }
    store._write_index({"schema": 1, "publications": [record]})

    client = _login(TestClient(app), app)
    listing = client.get("/api/browser-analysis/models")
    assert listing.status_code == 200
    models = listing.json()["models"]
    assert len(models) == 1
    assert models[0]["artifact_version"] == digest
    serialized = json.dumps(listing.json())
    assert str(tmp_path) not in serialized
    assert "published_by" not in serialized
    assert "source_checkpoint_sha256" not in serialized

    manifest = client.get(f"/api/browser-analysis/models/{record['publication_id']}")
    assert manifest.status_code == 200
    assert manifest.json()["onnx_sha256"] == digest

    artifact = client.get(f"/api/browser-analysis/models/{record['publication_id']}/artifact")
    assert artifact.status_code == 200
    assert artifact.content == MODEL_BYTES
    assert artifact.headers["content-type"] == "application/octet-stream"
    assert artifact.headers["cache-control"] == "no-store"
    assert artifact.headers["etag"] == f'"{digest}"'
    assert artifact.headers["x-content-sha256"] == digest
    assert record["publication_id"] in artifact.headers["content-disposition"]
    assert "attachment" in artifact.headers["content-disposition"]
    assert client.get("/api/browser-analysis/models/" + "2" * 24 + "/artifact").status_code == 404

    # A revoked publication and the unscoped server-model export route are both
    # unreachable through browser-analysis delivery.
    store.revoke(record["publication_id"], actor="Odin", reason="test")
    assert client.get("/api/browser-analysis/models").json()["models"] == []
    assert (
        client.get(f"/api/browser-analysis/models/{record['publication_id']}/artifact").status_code
        == 404
    )
    assert client.get("/api/models/" + "a" * 24 + "/export").status_code in (403, 404)


def test_artifact_size_mismatch_fails_closed(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    store = app.state.browser_models
    digest = hashlib.sha256(MODEL_BYTES).hexdigest()
    (store.artifacts / f"{digest}.onnx").write_bytes(b"truncated")
    record = {
        "publication_id": "3" * 24,
        "display_name": "x",
        "ruleset": "brandubh",
        "board_size": 7,
        "architecture": {"name": "tafl-policy-value-v1", "channels": 32, "blocks": 2},
        "encoder_version": "six-plane-v1",
        "source_weights_sha256": "a" * 64,
        "onnx_sha256": digest,
        "artifact_bytes": len(MODEL_BYTES),
        "runtime": {},
        "io": {},
        "status": "published",
        "published_at": "2026-11-25T00:00:00+00:00",
    }
    store._write_index({"schema": 1, "publications": [record]})
    client = _login(TestClient(app), app)
    assert (
        client.get(f"/api/browser-analysis/models/{record['publication_id']}/artifact").status_code
        == 404
    )


def test_enabled_without_runtime_reports_unavailable_but_lists_models(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True, stage_runtime=False)
    client = _login(TestClient(app), app)
    response = client.get("/api/browser-analysis/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["runtime_available"] is False
    assert body["reason"] == "runtime-missing"
    assert body["runtime"] is None
    assert client.get("/api/browser-analysis/models").status_code == 200
    assert client.get("/browser-analysis/runtime/0/runtime/pyodide.js").status_code == 404


def test_logout_invalidates_artifact_access(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    client = _login(TestClient(app), app)
    assert client.get("/api/browser-analysis/availability").status_code == 200
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    client.cookies.delete(COOKIE)
    assert client.get("/api/browser-analysis/availability").status_code == 401
    assert client.get("/api/browser-analysis/models").status_code == 401


def _second_runtime(data_dir: Path, monkeypatch, byte: bytes):
    cache = data_dir / "asset-cache"
    (cache / "pyodide" / "pyodide.js").write_bytes(byte)
    pyodide = dict(browser_runtime.PYODIDE_ASSETS)
    pyodide["pyodide.js"] = hashlib.sha256(byte).hexdigest()
    monkeypatch.setattr(browser_runtime, "PYODIDE_ASSETS", pyodide)
    return browser_runtime.build_runtime(
        data_dir,
        asset_cache=cache,
        activate=True,
        source_root=PROJECT_ROOT,
        application_version=application_metadata()["package_version"],
    )


def test_runtime_identity_is_immutable_across_activation(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    client = _login(TestClient(app), app)
    first = client.get("/api/browser-analysis/availability").json()["runtime"]
    first_id = first["runtime_id"]
    first_asset = client.get(f"{first['base_path']}/runtime/pyodide.js")
    assert first_asset.status_code == 200
    old_bytes = first_asset.content

    second = _second_runtime(tmp_path, monkeypatch, b"Y" * len(old_bytes))
    assert second["runtime_id"] != first_id
    latest = client.get("/api/browser-analysis/availability").json()["runtime"]
    assert latest["runtime_id"] == second["runtime_id"]
    new_asset = client.get(f"{latest['base_path']}/runtime/pyodide.js")
    assert new_asset.status_code == 200
    assert new_asset.content != old_bytes

    # The retained identity still serves exactly its own immutable bytes.
    retained = client.get(f"{first['base_path']}/runtime/pyodide.js")
    assert retained.status_code == 200
    assert retained.content == old_bytes
    first_digest = next(
        entry["sha256"] for entry in first["files"] if entry["path"] == "runtime/pyodide.js"
    )
    assert retained.headers["x-content-sha256"] == first_digest
    # A same-size replacement under the retained identity is detected.
    directory = tmp_path / "browser_runtime" / first_id
    (directory / "runtime" / "pyodide.js").write_bytes(b"Z" * len(old_bytes))
    assert client.get(f"{first['base_path']}/runtime/pyodide.js").status_code == 404

    # Pruning removes the retained identity from delivery.
    browser_runtime.prune_runtimes(tmp_path, keep=1)
    assert client.get(f"{first['base_path']}/runtime/pyodide.js").status_code == 404
    assert client.get(f"{latest['base_path']}/runtime/pyodide.js").status_code == 200


def test_runtime_activation_after_cached_absence_is_detected(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True, stage_runtime=False)
    client = _login(TestClient(app), app)
    missing = client.get("/api/browser-analysis/availability").json()
    assert missing["runtime_available"] is False
    assert missing["reason"] == "runtime-missing"
    assert client.get("/browser-analysis/runtime/0/runtime/pyodide.js").status_code == 404

    manifest = _synthetic_runtime(tmp_path, monkeypatch)
    activated = client.get("/api/browser-analysis/availability").json()
    assert activated["runtime_available"] is True
    assert activated["runtime"]["runtime_id"] == manifest["runtime_id"]
    assert (
        client.get(
            f"/browser-analysis/runtime/{manifest['runtime_id']}/runtime/pyodide.js"
        ).status_code
        == 200
    )
    # Deactivation removes delivery without a restart.
    (tmp_path / "browser_runtime" / "current").unlink()
    deactivated = client.get("/api/browser-analysis/availability").json()
    assert deactivated["runtime_available"] is False
    assert deactivated["reason"] == "runtime-missing"


def _published_record(store, publication_id="7" * 24):
    digest = hashlib.sha256(MODEL_BYTES).hexdigest()
    (store.artifacts / f"{digest}.onnx").write_bytes(MODEL_BYTES)
    record = {
        "publication_id": publication_id,
        "display_name": "Versioned model",
        "ruleset": "brandubh",
        "board_size": 7,
        "architecture": {"name": "tafl-policy-value-v1", "channels": 32, "blocks": 2},
        "encoder_version": "six-plane-v1",
        "source_weights_sha256": "a" * 64,
        "onnx_sha256": digest,
        "artifact_bytes": len(MODEL_BYTES),
        "runtime": {},
        "io": {},
        "status": "published",
        "published_at": "2026-11-25T00:00:00+00:00",
    }
    store._write_index({"schema": 1, "publications": [record]})
    return record


def test_artifact_version_binding_and_revocation(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    store = app.state.browser_models
    record = _published_record(store)
    client = _login(TestClient(app), app)
    base = f"/api/browser-analysis/models/{record['publication_id']}/artifact"
    assert client.get(f"{base}?version={record['onnx_sha256']}").status_code == 200
    assert client.get(f"{base}?version={'f' * 64}").status_code == 404
    assert client.get(f"{base}?version=not-a-digest").status_code == 404
    manifest = client.get(f"/api/browser-analysis/models/{record['publication_id']}")
    assert manifest.status_code == 200
    assert manifest.json()["onnx_sha256"] == record["onnx_sha256"]

    store.revoke(record["publication_id"], actor="Odin", reason="test")
    assert client.get(base).status_code == 404
    assert client.get(f"{base}?version={record['onnx_sha256']}").status_code == 404
    assert client.get(f"/api/browser-analysis/models/{record['publication_id']}").status_code == 404


def test_tampered_same_size_artifact_fails_closed(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=True)
    store = app.state.browser_models
    record = _published_record(store)
    client = _login(TestClient(app), app)
    (store.artifacts / f"{record['onnx_sha256']}.onnx").write_bytes(b"x" * len(MODEL_BYTES))
    assert (
        client.get(f"/api/browser-analysis/models/{record['publication_id']}/artifact").status_code
        == 404
    )
