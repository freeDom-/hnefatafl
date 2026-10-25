"""Regenerate frontend contract snapshots using isolated real core transactions.

Run from repository root with PYTHONPATH=. .venv/bin/python followed by this path.
UUIDs are fresh; no sessions/passwords or private audit fields are written.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
from hnefatafl.auth import Auth, SecuritySettings, digest
from hnefatafl.storage import Store
from hnefatafl.online_core import OnlineCore
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.engine import State, legal_moves

with TemporaryDirectory() as root:
    auth = Auth(Store(Path(root) / "db.sqlite3"), SecuritySettings())
    accounts = {}
    credentials = {}
    for name in ("Archer", "Keeper", "Viewer", "FixtureAdmin"):
        accounts[name] = auth.create_account(
            name,
            name.lower() + "@example.test",
            "online-fixture-password",
            "ADMIN" if name == "FixtureAdmin" else "USER",
        )
        _, token, _ = auth.login(name, "online-fixture-password", "fixture-" + name)
        credentials[name] = digest(token)
    with auth.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
    clock = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    core = OnlineCore(
        auth, OnlineSettings(enabled=True, timeout_policy_approved=True), lambda: clock[0]
    )
    waiting = core.create(credentials["Archer"], "brandubh", "attackers", 259200, str(uuid4()))[
        "game"
    ]
    active = core.mutate(credentials["Keeper"], waiting["id"], 0, "join")["game"]
    move = legal_moves(State.from_dict(active["position"]))[0]
    moved = core.mutate(
        credentials["Archer"], active["id"], 1, "move", source=move.source, target=move.target
    )["game"]
    completed = core.mutate(credentials["Keeper"], active["id"], 2, "concede")["game"]
    replay = core.replay(credentials["Viewer"], active["id"])
    output = {
        "account_id": accounts["Archer"]["id"],
        "waiting": waiting,
        "active": active,
        "moved": moved,
        "completed": completed,
        "replay": replay,
    }
    for mode in ("simultaneous", "sequential"):
        game = core.create(
            credentials["Archer"], "brandubh", "defenders", 259200, str(uuid4()), mode
        )["game"]
        game = core.mutate(credentials["Keeper"], game["id"], 0, "join")["game"]
        if mode == "sequential":
            game = core.mutate(credentials["Keeper"], game["id"], 1, "concede")["game"]
        output[mode] = game
        output[mode + "_return"] = core.read(credentials["Viewer"], game["match"]["games"][1]["id"])
        if mode == "simultaneous":
            output["simultaneous_return_completed"] = core.mutate(
                credentials["Archer"], output[mode + "_return"]["id"], 1, "concede"
            )["game"]
    timeout = core.create(credentials["Archer"], "brandubh", "attackers", 60, str(uuid4()))["game"]
    output["timeout_active"] = core.mutate(credentials["Keeper"], timeout["id"], 0, "join")["game"]
    core.set_gate(credentials["FixtureAdmin"], 0, True, "Deterministic fixture policy")
    clock[0] += timedelta(seconds=61)
    output["timeout_response"] = core.mutate(
        credentials["Archer"], timeout["id"], 1, "move", source=move.source, target=move.target
    )
    Path(__file__).resolve().parents[1].joinpath("src/onlineFixture.testdata.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
