"""Start a historical application lifespan while the parent holds the gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from hnefatafl.online_settings import OnlineSettings
from hnefatafl.server import create_app

POLICY = OnlineSettings(
    enabled=True,
    creation_policy_approved=True,
    retain_seat_without_verification=True,
    timeout_policy_approved=True,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = {"ok": False}
    app = create_app(
        args.data,
        online_settings=POLICY,
        online_background=True,
        account_background=False,
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/health")
            result.update(
                {"ok": response.json() == {"status": "ok"}, "status": response.status_code}
            )
    except BaseException as error:
        result.update({"error_type": type(error).__name__, "error": str(error)})
    finally:
        result["worker_threads"] = {
            name: bool(getattr(getattr(app.state, name, None), "thread", None))
            for name in ("online", "tournament_settlement")
        }
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
