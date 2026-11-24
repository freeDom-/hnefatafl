"""Disposable historical fixture generator.

Run with PYTHONPATH pointing at the historical source tree. Mode ``create-0.40``
creates a schema-15 installation using 0.40.0 code. Mode ``upgrade-schema20``
uses the pinned schema-20 historical code to migrate that installation and add
representative online/player-tournament records. No PRAGMA relabelling is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PASSWORD = "historical-release-fixture-password"
MODEL_BYTES = b"historical-model-bytes"
CHECKPOINT_BYTES = b"historical-checkpoint-bytes"
GAME_ID = "historical-local-game"
JOB_ID = "historical-job"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def current_clock():
    # Generate the fixture at test time so its 600-second game deadlines are in
    # the future for the activated release. A fixed historical instant made the
    # deadline already expired, so the target's one-second expiry sweep could
    # legitimately advance the tournament and race the historical count checks.
    return datetime.now(timezone.utc)


def create_040(data: Path, manifest_path: Path) -> None:
    from hnefatafl.auth import Auth, SecuritySettings
    from hnefatafl.engine import new_game
    from hnefatafl.storage import Store

    store = Store(data / "hnefatafl.sqlite3")
    auth = Auth(store, SecuritySettings())
    accounts = {}
    for name, role in (("Odin", "ADMIN"), ("Raven", "USER"), ("Fox", "USER")):
        accounts[name] = auth.create_account(
            name, name.lower() + "@example.test", PASSWORD, role, bootstrap=role == "ADMIN"
        )
    with store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
    store.save_job(
        {
            "id": JOB_ID,
            "created_at": "2026-11-01T12:00:00Z",
            "status": "completed",
            "ruleset": "brandubh",
            "result": {"note": "historical completed job"},
        }
    )
    (data / "models").mkdir(exist_ok=True)
    model = data / "models/historical-model.bin"
    model.write_bytes(MODEL_BYTES)
    job = data / "jobs" / JOB_ID
    job.mkdir(parents=True)
    (job / "checkpoint.pt").write_bytes(CHECKPOINT_BYTES)
    state = new_game("brandubh")
    store.create_game(
        {
            "id": GAME_ID,
            "version": 0,
            "created_at": "2026-11-01T12:00:00Z",
            "ruleset": "brandubh",
            "state": state.to_dict(),
            "moves": [],
            "players": {},
            "cursor": 0,
            "status": "active",
        }
    )
    with store.connection() as c:
        schema = c.execute("PRAGMA user_version").fetchone()[0]
    write_json(
        manifest_path,
        {
            "schema": schema,
            "accounts": {name: entry["id"] for name, entry in accounts.items()},
            "roles": {name: entry["role"] for name, entry in accounts.items()},
            "email_verified": {
                name: entry.get("email_verified_at") for name, entry in accounts.items()
            },
            "model_sha256": hashlib.sha256(MODEL_BYTES).hexdigest(),
            "checkpoint_sha256": hashlib.sha256(CHECKPOINT_BYTES).hexdigest(),
            "job_id": JOB_ID,
            "local_game_id": GAME_ID,
        },
    )


def upgrade_schema20(data: Path, manifest_path: Path) -> None:
    from hnefatafl.auth import Auth, SecuritySettings, digest
    from hnefatafl.online_core import OnlineCore
    from hnefatafl.online_settings import OnlineSettings
    from hnefatafl.player_tournaments import PlayerTournaments
    from hnefatafl.storage import Store

    store = Store(data / "hnefatafl.sqlite3")
    with store.connection() as c:
        schema = c.execute("PRAGMA user_version").fetchone()[0]
    if schema != 20:
        raise SystemExit(f"Expected authentic schema 20, found {schema}")
    manifest = json.loads(manifest_path.read_text())
    auth = Auth(store, SecuritySettings())
    tokens = {}
    for name in ("Odin", "Raven", "Fox"):
        _, token, _ = auth.login(name, PASSWORD, "historical-fixture")
        tokens[name] = digest(token)
    settings = OnlineSettings(
        enabled=True,
        creation_policy_approved=True,
        retain_seat_without_verification=True,
        timeout_policy_approved=True,
    )
    clock = current_clock
    online = OnlineCore(auth, settings, clock)
    created = online.create(
        tokens["Odin"], "brandubh", "attackers", 600, uuid4().hex, "sequential"
    )["game"]
    joined = online.mutate(tokens["Raven"], created["id"], created["version"], "join")["game"]
    tournaments = PlayerTournaments(auth, online, clock)
    tournament = tournaments.create(
        tokens["Odin"],
        name="Historical Arena Cup",
        ruleset="brandubh",
        capacity=2,
        match_mode="sequential",
        time_per_move=600,
    )
    tournaments.register(tokens["Raven"], tournament["id"])
    tournaments.register(tokens["Fox"], tournament["id"])
    current = tournaments.detail(tokens["Odin"], tournament["id"])
    started = tournaments.start(tokens["Odin"], tournament["id"], current["version"])
    with store.connection() as c:
        counts = {
            table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "online_matches",
                "online_games",
                "player_tournaments",
                "player_tournament_entrants",
                "player_tournament_matches",
                "player_tournament_legs",
            )
        }
    manifest.update(
        {
            "schema": schema,
            "online_game_id": joined["id"],
            "player_tournament_id": started["id"],
            "historical_counts": counts,
        }
    )
    write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create-0.40", "upgrade-schema20"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    if args.mode == "create-0.40":
        create_040(args.data, args.manifest)
    else:
        upgrade_schema20(args.data, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
