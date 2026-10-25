"""Development-only browser fixture with an in-memory fake mail transport.

The separate loopback mailbox belongs to the test harness, not the application.
No capture mode or test endpoint is added to production routes.
"""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit

root = Path(os.environ.get("HNEFATAFL_DATA_DIR", "")).resolve()
if (
    not root.name.startswith("hnefatafl-registration-browser-")
    or os.environ.get("HNEFATAFL_ENV", "development") != "development"
):
    raise SystemExit("Registration browser fixture requires isolated development storage")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hnefatafl.account_mail import AccountSettings  # noqa: E402
from hnefatafl.server import create_app  # noqa: E402
import uvicorn  # noqa: E402


class FakeSMTP:
    def __init__(self):
        self.lock = threading.Lock()
        self.messages = []

    def send(self, recipient, purpose, link):
        with self.lock:
            self.messages.append({"recipient": recipient, "purpose": purpose, "link": link})


mailbox = FakeSMTP()


class MailboxHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        with mailbox.lock:
            matches = [
                m
                for m in mailbox.messages
                if m["recipient"] == query.get("email", [""])[0]
                and m["purpose"] == query.get("purpose", [""])[0]
            ]
        data = json.dumps(matches).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 8004), MailboxHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
settings = replace(
    AccountSettings(),
    enabled=True,
    public_origin="http://127.0.0.1:4175",
    sender="fixture@example.test",
    smtp_host="fake.invalid",
    poll_seconds=1,
    register_ip=20,
)
app = create_app(root, account_settings=settings, mail_transport=mailbox)
uvicorn.run(app, host="127.0.0.1", port=8003, access_log=False)
