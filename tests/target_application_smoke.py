"""Post-deployment authentication and protected-mutation smoke test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from hnefatafl.server import create_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--identity", default="Raven")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    result = {"ok": False}
    app = create_app(
        args.data,
        online_background=False,
        account_background=False,
    )
    try:
        with TestClient(app, base_url=args.origin) as client:
            login = client.post(
                "/api/auth/login",
                json={"identity": args.identity, "password": args.password},
                headers={"Origin": args.origin},
            )
            result["login_status"] = login.status_code
            if login.status_code == 200:
                client.headers.update(
                    {
                        "Origin": args.origin,
                        "X-CSRF-Token": login.json()["csrf_token"],
                    }
                )
                me = client.get("/api/auth/me")
                created = client.post("/api/games", json={})
                result.update(
                    {
                        "me_status": me.status_code,
                        "mutation_status": created.status_code,
                        "ok": me.status_code == 200 and created.status_code == 201,
                    }
                )
    except BaseException as error:
        result.update({"error_type": type(error).__name__})
    finally:
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
