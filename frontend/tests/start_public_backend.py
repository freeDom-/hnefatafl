"""Built frontend, production security and disposable real online records.

The fixture controller is loopback-only and separate from the FastAPI app. It
exposes identifiers, never sessions; no fixture route or auth bypass ships.
"""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

root = Path(os.environ.get("HNEFATAFL_DATA_DIR", "")).resolve()
if (
    root.parent != Path("/tmp")
    or not root.name.startswith("hnefatafl-public-browser-")
    or os.environ.get("HNEFATAFL_ENV", "development") != "development"
):
    raise SystemExit("Public browser fixtures require isolated temporary development storage")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Exercise the actual production boundary. HTTP is the internal transport, as
# behind the documented TLS proxy; production still requires an HTTPS origin.
os.environ.update(
    HNEFATAFL_ENV="production",
    HNEFATAFL_PUBLIC_URL="https://127.0.0.1:8011",
    HNEFATAFL_ALLOWED_HOSTS="127.0.0.1,localhost",
    HNEFATAFL_TRUSTED_PROXIES="127.0.0.1",
)
from hnefatafl.auth import digest  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402
from hnefatafl.online_settings import OnlineSettings  # noqa: E402
from hnefatafl.server import create_app  # noqa: E402
import uvicorn  # noqa: E402

clock = datetime.now(timezone.utc)
app = create_app(
    root,
    online_settings=OnlineSettings(
        enabled=True, waiting_limit=100, creation_limit=100, public_rate_limit=100000
    ),
    online_clock=lambda: clock,
    online_background=False,
    account_background=False,
)
accounts, credentials = {}, {}
for name in ("WatchArcher", "WatchKeeper", "WatchOdin"):
    admin = name == "WatchOdin"
    accounts[name] = app.state.auth.create_account(
        name,
        name.lower() + "@example.test",
        "public-browser-fixture-password",
        "ADMIN" if admin else "USER",
        bootstrap=admin,
    )
    _, token, _ = app.state.auth.login(name, "public-browser-fixture-password", "fixture-" + name)
    credentials[name] = digest(token)
with app.state.store.connection() as connection:
    connection.execute("UPDATE accounts SET email_verified_at=1")

core = app.state.online
archer, keeper, odin = (credentials[n] for n in ("WatchArcher", "WatchKeeper", "WatchOdin"))


def waiting(ruleset="brandubh"):
    return core.create(archer, ruleset, "attackers", 259200, str(uuid4()))["game"]


def active(ruleset="brandubh"):
    game = waiting(ruleset)
    return core.mutate(keeper, game["id"], 0, "join")["game"]


def mover(game):
    identifier = game["participants"][game["position"]["turn"]]["account_id"]
    return next(
        credentials[name] for name, account in accounts.items() if account["id"] == identifier
    )


ids = {}
for ruleset in ("brandubh", "tablut", "copenhagen"):
    ids[ruleset] = active(ruleset)["id"]
ids["waiting"] = waiting()["id"]
waiting("tablut")
game = waiting()
ids["cancelled"] = core.mutate(archer, game["id"], 0, "cancel")["game"]["id"]
game = active()
for _ in range(2):
    move = legal_moves(State.from_dict(game["position"]))[0]
    game = core.mutate(
        mover(game), game["id"], game["version"], "move", source=move.source, target=move.target
    )["game"]
ids["completed"] = core.mutate(mover(game), game["id"], game["version"], "concede")["game"]["id"]
game = active()
game = core.mutate(mover(game), game["id"], game["version"], "concede")["game"]
ids["voided"] = core.mutate(
    odin, game["id"], game["version"], "void", note="private fixture correction"
)["game"]["id"]
for _ in range(7):
    game = active()
    core.mutate(mover(game), game["id"], game["version"], "concede")


class Fixtures(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/fixtures":
            self.send_error(404)
            return
        payload = json.dumps(ids).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


control = ThreadingHTTPServer(("127.0.0.1", 8012), Fixtures)
threading.Thread(target=control.serve_forever, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=8011, access_log=False)
