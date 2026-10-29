"""Disposable real browser backend with an isolated historical-fixture controller."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(os.environ["HNEFATAFL_DATA_DIR"])
if not root.name.startswith("hnefatafl-browser-") or os.environ.get("HNEFATAFL_ENV") == "production":
    raise SystemExit("Browser fixture requires isolated temporary development data")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from fastapi.testclient import TestClient
from hnefatafl.server import create_app
from record_fixtures import historical_game
import uvicorn

app = create_app(root)
auth = app.state.auth
auth.create_account("BrowserAdmin", "browser-admin@example.test", "browser-fixture-only-password", "ADMIN", bootstrap=True)
auth.create_account("BrowserUser", "browser-user@example.test", "browser-fixture-only-password")
with app.state.store.connection() as connection:
    connection.execute("UPDATE accounts SET email_verified_at=1 WHERE username='BrowserAdmin'")
# No lifespan here: the real uvicorn app alone owns workers and cleanup.
fixture_client = TestClient(app)
session = fixture_client.post("/api/auth/login", json={"identity":"BrowserAdmin", "password":"browser-fixture-only-password"}, headers={"Origin":"http://testserver"}).json()
fixture_client.headers.update({"Origin":"http://testserver", "X-CSRF-Token":session["csrf_token"]})

class HistoricalFixtures(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/historical-game" or not 0 < size <= 32768:
            self.send_error(400)
            return
        game = historical_game(fixture_client, **json.loads(self.rfile.read(size)))
        raw = json.dumps(game).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

control = ThreadingHTTPServer(("127.0.0.1",8002),HistoricalFixtures)
threading.Thread(target=control.serve_forever,daemon=True).start()
uvicorn.run(app,host="127.0.0.1",port=8001,access_log=False)
