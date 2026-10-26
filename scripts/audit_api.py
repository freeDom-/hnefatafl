"""Regenerate/check the actual route inventory using disposable storage."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--write", action="store_true")
args = parser.parse_args()
previous = os.environ.get("HNEFATAFL_DATA_DIR")
with tempfile.TemporaryDirectory(prefix="hnefatafl-audit-") as root:
    os.environ["HNEFATAFL_DATA_DIR"] = root
    from hnefatafl.auth import POLICY
    from hnefatafl.server import create_app
    app = create_app(root)
    rows = []
    for route in app.routes:
        if not getattr(route, "path", "").startswith("/api/"):
            continue
        for method in sorted(route.methods):
            permission = POLICY.get((method, route.path))
            if permission is None:
                raise SystemExit(f"Unclassified route: {method} {route.path}")
            rows.append({
                "method": method, "route": route.path,
                "authentication": "worker bearer + lease" if permission == "worker" else "public" if permission == "public" else "session",
                "permission": permission,
                "persistent_state": method not in {"GET", "HEAD", "OPTIONS"} or (not route.path.startswith(("/api/online/", "/api/personal/", "/api/history/")) and route.path not in {"/api/health", "/api/auth/registration", "/api/auth/me", "/api/games/{game_id}/analytics"}),
                "user_specific": route.path.startswith(("/api/games", "/api/players", "/api/stats", "/api/auth/", "/api/online/", "/api/personal/", "/api/history/")) and route.path != "/api/auth/registration",
            })
    target = Path(__file__).resolve().parents[1] / "docs/API_PERMISSIONS.json"
    rendered = json.dumps(sorted(rows, key=lambda row: (row["route"], row["method"])), indent=2) + "\n"
    if args.write:
        target.write_text(rendered)
    elif target.read_text() != rendered:
        raise SystemExit("Route inventory changed: review permissions, then run scripts/audit_api.py --write")
    print(f"{len(rows)} registered API method/path grants accounted for.")
if previous is None:
    os.environ.pop("HNEFATAFL_DATA_DIR", None)
else:
    os.environ["HNEFATAFL_DATA_DIR"] = previous
