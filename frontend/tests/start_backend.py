"""Disposable browser-test bootstrap with ordinary database accounts and login."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.storage import Store

root = Path(os.environ["HNEFATAFL_DATA_DIR"])
if not root.name.startswith("hnefatafl-browser-") or os.environ.get("HNEFATAFL_ENV") == "production":
    raise SystemExit("Browser fixture requires isolated temporary development data")
auth = Auth(Store(root / "hnefatafl.sqlite3"), SecuritySettings())
auth.create_account("BrowserAdmin", "browser-admin@example.test", "browser-fixture-only-password", "ADMIN", bootstrap=True)
auth.create_account("BrowserUser", "browser-user@example.test", "browser-fixture-only-password")
os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "hnefatafl.server:app", "--host", "127.0.0.1", "--port", "8001", "--app-dir", ".."])
