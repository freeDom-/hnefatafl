"""Production-build browser tests on isolated development data; never a Vite CSP bypass."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

root = Path(os.environ.get("HNEFATAFL_DATA_DIR", "")).resolve()
if (
    not root.name.startswith("hnefatafl-guest-browser-")
    or os.environ.get("HNEFATAFL_ENV", "development") != "development"
):
    raise SystemExit("Guest browser fixtures require isolated development storage")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hnefatafl.server import create_app  # noqa: E402
from hnefatafl.online_settings import OnlineSettings  # noqa: E402
import uvicorn  # noqa: E402

app = create_app(
    root,
    online_settings=OnlineSettings(enabled=True),
    online_background=False,
    account_background=False,
)
for name in ("GuestArcher", "GuestKeeper"):
    app.state.auth.create_account(
        name,
        name.lower() + "@example.test",
        "guest-browser-fixture-password",
        "ADMIN" if name == "GuestArcher" else "USER",
    )
with app.state.store.connection() as c:
    c.execute("UPDATE accounts SET email_verified_at=1")


class Control(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/counts":
            self.send_error(404)
            return
        with app.state.store.connection() as c:
            data = {
                name: c.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                for name in ("games", "online_games", "accounts")
            }
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


control = ThreadingHTTPServer(("127.0.0.1", 8008), Control)
threading.Thread(target=control.serve_forever, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=8007, access_log=False)
