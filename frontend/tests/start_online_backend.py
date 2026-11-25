"""Isolated real-backend browser harness; controls never enter application routes."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

root = Path(os.environ.get("HNEFATAFL_DATA_DIR", "")).resolve()
if (
    not root.name.startswith("hnefatafl-online-browser-")
    or os.environ.get("HNEFATAFL_ENV", "development") != "development"
):
    raise SystemExit("Online browser fixture requires isolated development storage")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hnefatafl.server import create_app  # noqa: E402
from hnefatafl.online_core import micros  # noqa: E402
from hnefatafl.online_settings import OnlineSettings  # noqa: E402
import uvicorn  # noqa: E402


class Clock:
    value = datetime.now(timezone.utc)

    def __call__(self):
        return self.value


clock = Clock()
policy = OnlineSettings(enabled=True, waiting_limit=100, creation_limit=100)
app = create_app(
    root,
    online_settings=policy,
    online_clock=clock,
    online_background=True,
    account_background=False,
)
league_users = tuple(
    f"League{role}{project}"
    for project in ("chromium", "mobile")
    for role in ("Archer", "Keeper", "Solo", "Admin")
)
for name in (
    "OnlineArcher",
    "OnlineKeeper",
    "OnlineViewer",
    "SwitcherArcherchromium",
    "SwitcherKeeperchromium",
    "SwitcherArchermobile",
    "SwitcherKeepermobile",
    *league_users,
):
    app.state.auth.create_account(
        name,
        name.lower() + "@example.test",
        "online-browser-fixture-password",
        "ADMIN" if name.startswith("LeagueAdmin") else "USER",
    )
with app.state.store.connection() as connection:
    connection.execute("UPDATE accounts SET email_verified_at=1")


class Control(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        # Loopback-only, bounded, disposable harness controls; no cookies/credentials.
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024:
            self.send_error(413)
            return
        values = json.loads(self.rfile.read(length) or "{}")
        if self.path == "/reset":
            clock.value = datetime.now(timezone.utc)
            app.state.online.settings = policy
            with app.state.store.connection() as c:
                c.execute("UPDATE accounts SET email_verified_at=1")
                c.execute("DELETE FROM login_attempts")
        elif self.path == "/admission":
            app.state.online.settings = replace(policy, enabled=bool(values["enabled"]))
        elif self.path == "/advance":
            clock.value += timedelta(seconds=values["seconds"])
        elif self.path == "/verification":
            with app.state.store.connection() as c:
                c.execute(
                    "UPDATE accounts SET email_verified_at=? WHERE username=?",
                    (1 if values["verified"] else None, values["name"]),
                )
        elif self.path == "/league-clock":
            clock.value = datetime.fromisoformat(values["at"]).astimezone(timezone.utc)
        elif self.path == "/league-rating" and values.get("name") in league_users:
            # Start with a gap that needs the first minute of mutual widening.
            # Official online results and points must still use the real server.
            with app.state.online._write() as c:
                c.execute(
                    """INSERT INTO player_ratings(account_id,pool,rating,rated_games,wins,
                       losses,draws,policy_version,updated_at)
                       SELECT id,'brandubh',?,0,0,0,0,1,? FROM accounts WHERE username=?
                       ON CONFLICT(account_id,pool) DO UPDATE SET rating=excluded.rating""",
                    (values["rating"], micros(clock()), values["name"]),
                )
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


control = ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ.get("HNEFATAFL_ONLINE_FIXTURE_CONTROL_PORT", "8006"))),
    Control,
)
threading.Thread(target=control.serve_forever, daemon=True).start()
uvicorn.run(
    app,
    host="127.0.0.1",
    port=int(os.environ.get("HNEFATAFL_ONLINE_FIXTURE_PORT", "8005")),
    access_log=False,
)
