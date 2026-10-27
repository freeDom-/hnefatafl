"""Production build + real sessions/SQLite; no fixture routes in the application."""

from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

root = Path(os.environ.get("HNEFATAFL_DATA_DIR", "")).resolve()
if (
    not root.name.startswith("hnefatafl-personal-browser-")
    or os.environ.get("HNEFATAFL_ENV", "development") != "development"
):
    raise SystemExit("Personal browser fixtures require isolated development storage")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from record_fixtures import historical_response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from hnefatafl.auth import digest  # noqa: E402
from hnefatafl.online_settings import OnlineSettings  # noqa: E402
from hnefatafl.server import create_app  # noqa: E402
import uvicorn  # noqa: E402

clock = [datetime(2035, 1, 1, tzinfo=timezone.utc)]
policy = OnlineSettings(
    enabled=True, timeout_policy_approved=True, waiting_limit=100, creation_limit=100
)


def application():
    return create_app(
        root,
        online_settings=policy,
        online_clock=lambda: clock[0],
        online_background=False,
        account_background=False,
    )


seed = application()
accounts, credentials = {}, {}
for name in ("PersonalArcher", "PersonalKeeper", "PersonalObserver", "PersonalOdin"):
    accounts[name] = seed.state.auth.create_account(
        name,
        name.lower() + "@example.test",
        "personal-browser-fixture-password",
        "ADMIN" if name == "PersonalOdin" else "USER",
    )
    _, token, _ = seed.state.auth.login(name, "personal-browser-fixture-password", "fixture")
    credentials[name] = digest(token)
with seed.state.store.connection() as c:
    c.execute("UPDATE accounts SET email_verified_at=1")

core = seed.state.online
archer, keeper, odin = (
    credentials[n] for n in ("PersonalArcher", "PersonalKeeper", "PersonalOdin")
)


def active(mode="single", side="attackers", ruleset="brandubh"):
    game = core.create(archer, ruleset, side, 259200, str(uuid4()), mode)["game"]
    return core.mutate(keeper, game["id"], 0, "join")["game"]


ids = {}
# Terminal records first so time advancing for the timeout cannot expire live fixtures.
expired = active()
core.set_gate(odin, 0, True, "isolated fixture approval")
clock[0] += timedelta(seconds=259201)
core.sweep()
ids["timeout"] = expired["id"]
voided = active()
voided = core.mutate(archer, voided["id"], 1, "concede")["game"]
core.mutate(odin, voided["id"], voided["version"], "void", note="private-browser-correction")
ids["voided"] = voided["id"]
conceded = active()
moved = core.mutate(archer, conceded["id"], 1, "move", source=3, target=1)["game"]
core.mutate(keeper, moved["id"], moved["version"], "concede")
ids["completed"] = conceded["id"]
for _ in range(3):
    extra = active()
    core.mutate(archer, extra["id"], extra["version"], "concede")
cancelled = core.create(archer, "brandubh", "random", 259200, str(uuid4()))["game"]
core.mutate(archer, cancelled["id"], 0, "cancel")
ids["cancelled"] = cancelled["id"]
waiting = core.create(archer, "tablut", "random", 259200, str(uuid4()))["game"]
ids["waiting"] = waiting["id"]
for _ in range(12):
    ids["active"] = active()["id"]
ids["paired"] = active("simultaneous", "defenders")["id"]
ids["tablut"] = active(ruleset="tablut")["id"]


def concede_mover(game):
    side = game["position"]["turn"]
    account_id = game["participants"][side]["account_id"]
    credential = next(
        c for name, c in credentials.items() if accounts[name]["id"] == account_id
    )
    return core.mutate(credential, game["id"], game["version"], "concede")["game"]


# Ten rated Tablut games make PersonalArcher and PersonalKeeper qualified.
for _ in range(10):
    rated = core.create(archer, "tablut", "attackers", 259200, str(uuid4()), rated=True)["game"]
    rated = core.mutate(keeper, rated["id"], 0, "join")["game"]
    ids["rated"] = concede_mover(rated)["id"]
with TestClient(seed, base_url="http://127.0.0.1:8009") as client:
    session = client.post(
        "/api/auth/login",
        json={"identity": "PersonalArcher", "password": "personal-browser-fixture-password"},
        headers={"Origin": "http://127.0.0.1:8009"},
    ).json()
    client.headers.update(
        {"Origin": "http://127.0.0.1:8009", "X-CSRF-Token": session["csrf_token"]}
    )
    for i in range(12):
        response = historical_response(client, json={"ruleset": "brandubh", "mode": "human-human"})
        assert response.status_code in (200, 201), response.text
        game = response.json()
        if i == 0:
            for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
                response = client.post(
                    f"/api/games/{game['id']}/moves",
                    json={"version": game["version"], "source": source, "target": target},
                )
                assert response.status_code == 200, response.text
                game = response.json()
            ids["account"] = game["id"]
        if i == 1:
            ids["unfinished_account"] = game["id"]
    assert historical_response(client, json={"ruleset": "tablut", "mode": "human-human"}).status_code in (200, 201)

    session = client.post(
        "/api/auth/login",
        json={"identity": "PersonalOdin", "password": "personal-browser-fixture-password"},
        headers={"Origin": "http://127.0.0.1:8009"},
    ).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    game = historical_response(client, json={"ruleset": "brandubh", "mode": "human-human"}).json()
    for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
        game = client.post(
            f"/api/games/{game['id']}/moves",
            json={"version": game["version"], "source": source, "target": target},
        ).json()
    ids["admin_account"] = game["id"]

# Fresh production resources after fixture TestClient shutdown.
app = application()


class Control(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != "/reset-login-attempts":
            self.send_error(404)
            return
        # Each browser case gets its own login budget in this isolated fixture.
        with app.state.store.connection() as c:
            c.execute("DELETE FROM login_attempts")
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path != "/snapshot":
            self.send_error(404)
            return
        with app.state.store.connection() as c:
            tables = ("games", "online_games", "online_moves", "online_results")
            counts = {
                name: c.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in tables
            }
            records = {
                name: sorted(tuple(row) for row in c.execute("SELECT * FROM " + name))
                for name in tables
            }
        raw = json.dumps(
            {
                "ids": ids,
                "counts": counts,
                "fingerprint": hashlib.sha256(
                    json.dumps(records, sort_keys=True).encode()
                ).hexdigest(),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


control = ThreadingHTTPServer(("127.0.0.1", 8010), Control)
threading.Thread(target=control.serve_forever, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=8009, access_log=False)
