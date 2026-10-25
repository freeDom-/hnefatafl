"""Guest shell/CSP stays public while existing API authorization stays closed."""

from pathlib import Path
import json
import runpy

from fastapi.testclient import TestClient
import pytest

from hnefatafl.server import create_app


def test_guest_shell_and_worker_have_narrow_security_policy(tmp_path, monkeypatch):
    build = tmp_path / "project/frontend/dist"
    assets = build / "assets" / ("guest-" + "a" * 64)
    assets.mkdir(parents=True)
    (build / "index.html").write_text("guest and login shell")
    (assets / "worker.js").write_text("/* fixture worker */")
    (assets / "other.js").write_text("/* ordinary asset */")
    monkeypatch.setattr("hnefatafl.server.PROJECT_ROOT", tmp_path / "project")
    app = create_app(tmp_path / "data", online_background=False, account_background=False)
    with TestClient(app) as client:
        for path in ("/guest", "/guest/"):
            response = client.get(path)
            assert response.status_code == 200 and response.text == "guest and login shell"
            assert "wasm-unsafe-eval" not in response.headers["content-security-policy"]
            assert response.headers["cache-control"] == "no-store"
        base = "/assets/" + assets.name
        worker = client.get(base + "/worker.js")
        csp = worker.headers["content-security-policy"]
        assert worker.status_code == 200 and "'wasm-unsafe-eval'" in csp
        assert "'unsafe-eval'" not in csp and "'unsafe-inline'" not in csp
        for path in (base + "/other.js", "/assets/worker.js", "/login"):
            assert "wasm-unsafe-eval" not in client.get(path).headers["content-security-policy"]
        for path in ("/api/games", "/api/online/games", "/api/auth/me"):
            assert client.get(path).status_code == 401
        for path in ("/api/games", "/api/online/games"):
            assert client.post(path, json={}).status_code == 401
        for path in ("/guest/private", "/online", "/online/games/online-" + "a" * 32):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"].startswith("/login?next=")
        assert not app.state.store.list_games()


def test_private_worker_protocol_cannot_accept_board_identity_or_online_operations():
    bridge = runpy.run_path(str(Path(__file__).resolve().parents[1] / "frontend/guest/bridge.py"))
    dispatch = bridge["dispatch"]
    initial = json.loads(
        dispatch(json.dumps({"action": "new", "ruleset": "brandubh", "human_side": "attackers"}))
    )
    assert initial["record"]["moves"] == []
    for request in (
        {"action": "new", "ruleset": "brandubh", "human_side": "attackers", "id": "online-a"},
        {"action": "move", "source": 3, "target": 1, "board": []},
        {"action": "computer", "algorithm": "neural", "model_id": "private"},
        {"action": "upload"},
        {"action": "info", "csrf_token": "secret"},
    ):
        with pytest.raises(ValueError, match="Invalid guest operation"):
            dispatch(json.dumps(request))
