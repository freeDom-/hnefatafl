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
    online_background=False,
    account_background=False,
)
for name in ("OnlineArcher", "OnlineKeeper", "OnlineViewer"):
    app.state.auth.create_account(
        name, name.lower() + "@example.test", "online-browser-fixture-password"
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
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


control = ThreadingHTTPServer(("127.0.0.1", 8006), Control)
threading.Thread(target=control.serve_forever, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=8005, access_log=False)
