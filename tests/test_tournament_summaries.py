"""Lightweight tournament summaries: cheap listing without retained match history."""

import json
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.server import create_app
from hnefatafl.storage import Store
from hnefatafl.tournaments import TournamentManager


def stored_tournament(identifier, *, games=120, plies=40, mode="round_robin", status="completed"):
    """A saved record with a deliberately large retained match history."""
    match = {
        "ruleset": "brandubh",
        "winner": "attackers",
        "outcome": "completed",
        "reason": "king_captured",
        "plies": plies,
        "moves": [{"source": index % 10, "target": (index + 1) % 10} for index in range(plies)],
        "attacker_player": 0,
        "defender_player": 1,
        "average_decision_time": 0.01,
        "total_decision_time": 0.4,
        "state": {"board": ["." * 7] * 7, "turn": "attackers", "ply": plies},
    }
    config = {
        "players": [
            {"name": "Raven", "config": {"algorithm": "baseline", "time_limit": 0.01}},
            {"name": "Wolf", "config": {"algorithm": "baseline", "time_limit": 0.01}},
        ],
        "rulesets": ["brandubh"],
        "games": 2,
        "max_plies": 40,
        "seed": 11,
    }
    if mode is not None:
        config["mode"] = mode
    return {
        "id": identifier,
        "name": f"Tournament {identifier}",
        "status": status,
        "created_at": "2026-10-31T15:00:00Z",
        "ended_at": "2026-10-31T15:05:00Z" if status != "running" else None,
        "config": config,
        "progress": {"completed_games": games, "total_games": games},
        "result": {
            "players": [player["config"] for player in config["players"]],
            "scores": [60.0, 60.0],
            "standings": [
                dict(player=0, wins=60, losses=0, draws=0, truncated=0, games=games, score=60.0),
                dict(player=1, wins=0, losses=60, draws=0, truncated=0, games=games, score=0.0),
            ],
            "by_ruleset": {
                "brandubh": {"wins": [60, 0], "draws": 0, "truncated": 0, "games": games}
            },
            "games": [match for _ in range(games)],
            "truncated": 0,
            "average_game_length": plies,
            "average_decision_time": 0.01,
            "seed": 11,
        },
        "error": None,
    }


def test_storage_summary_projection_never_decodes_match_history(tmp_path, monkeypatch):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    record = stored_tournament("a" * 32)
    store.save_tournament(record)
    full_size = len(json.dumps(record))
    decoded = []
    recording = False
    original = json.loads

    def recorded(payload, *args, **kwargs):
        if recording and isinstance(payload, str):
            decoded.append(len(payload))
        return original(payload, *args, **kwargs)

    monkeypatch.setattr("hnefatafl.storage.json.loads", recorded)
    recording = True
    try:
        summary = store.tournaments(summary=True)
        recording = False
        assert len(decoded) == 1
        assert decoded[0] < full_size / 10
        recording = True
        full = store.tournaments()
    finally:
        recording = False
    assert len(decoded) == 2 and decoded[1] > full_size / 2
    assert all("result" not in item for item in summary)
    assert summary[0]["progress"] == {"completed_games": 120, "total_games": 120}
    assert summary[0]["config"]["players"][0]["name"] == "Raven"
    assert len(full[0]["result"]["games"]) == 120


def test_listing_reads_only_running_records(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    store.save_tournament(stored_tournament("a" * 32))
    store.save_tournament(stored_tournament("b" * 32))
    interrupted = stored_tournament("c" * 32, status="running")
    interrupted["pid"] = 2147483647
    store.save_tournament(interrupted)
    reads = []
    original = Store.get_tournament

    def spy(self, identifier):
        reads.append(identifier)
        return original(self, identifier)

    Store.get_tournament = spy
    try:
        manager = TournamentManager(store, tmp_path)
        for summary in (True, False):
            # Restore the active worker record so both passes exercise reconciliation.
            store.save_tournament(interrupted)
            reads.clear()
            records = manager.list(summary=summary)
            assert {record["id"] for record in records} == {"a" * 32, "b" * 32, "c" * 32}
            # Only the active worker needs a full read for reconciliation.
            assert reads and set(reads) == {"c" * 32}
            assert all(record["status"] != "running" for record in records)
        assert store.get_tournament("c" * 32)["status"] == "failed"
    finally:
        Store.get_tournament = original


def test_summary_endpoint_omits_history_and_detail_keeps_everything(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        created = client.post(
            "/api/tournaments",
            json={
                "name": "Saved series",
                "rulesets": ["brandubh"],
                "players": [
                    {"config": {"algorithm": "baseline", "time_limit": 0.01}},
                    {"config": {"algorithm": "baseline", "time_limit": 0.01}},
                ],
                "games": 2,
                "max_plies": 2,
                "seed": 3,
            },
        ).json()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            detail = client.get(f"/api/tournaments/{created['id']}").json()
            if detail["status"] == "completed":
                break
            time.sleep(0.03)
        assert detail["status"] == "completed"
        default = client.get("/api/tournaments").json()[0]
        explicit = client.get("/api/tournaments?summary=true").json()[0]
        assert default == explicit
        for summary in (default, explicit):
            assert summary["summary"] is True
            assert summary["mode"] == "round_robin"
            assert "result" not in summary
            assert summary["progress"] == {"completed_games": 2, "total_games": 2}
            assert [player["name"] for player in summary["config"]["players"]] == [
                "Shield bearer",
                "Shield bearer",
            ]
            assert all(player["agent_rune"] for player in summary["config"]["players"])
        full_listing = client.get("/api/tournaments?summary=false").json()[0]
        assert "summary" not in full_listing
        assert len(full_listing["result"]["games"]) == 2
        assert full_listing == detail
        # The lightweight collection is far smaller than the complete record.
        assert len(json.dumps(explicit)) < len(json.dumps(detail))


def test_historical_records_without_mode_still_summarise(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    store.save_tournament(stored_tournament("d" * 32, mode=None))
    with TestClient(create_app(tmp_path)) as client:
        summary = client.get("/api/tournaments?summary=true").json()[0]
        assert summary["mode"] == "round_robin"
        assert "result" not in summary
        detail = client.get(f"/api/tournaments/{'d' * 32}").json()
        assert detail["mode"] == "round_robin"
        assert len(detail["result"]["games"]) == 120
        # A stored record is never rewritten just to serve a summary.
        assert store.get_tournament("d" * 32)["config"].get("mode") is None


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
def test_terminal_states_are_preserved_by_the_summary_listing(tmp_path, status):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    store.save_tournament(stored_tournament("e" * 32, status=status))
    with TestClient(create_app(tmp_path)) as client:
        summary = client.get("/api/tournaments?summary=true").json()[0]
        assert summary["status"] == status
        assert summary["ended_at"]
        assert "result" not in summary
        assert store.get_tournament("e" * 32)["status"] == status


def test_interrupted_worker_is_reconciled_through_the_summary_listing(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    interrupted = stored_tournament("f" * 32, status="running")
    interrupted["pid"] = 2147483647
    store.save_tournament(interrupted)
    with TestClient(create_app(tmp_path)) as client:
        summary = client.get("/api/tournaments?summary=true").json()[0]
        assert summary["status"] == "failed"
        assert summary["error"]
        assert summary["ended_at"]
        assert "result" not in summary
        assert client.delete(f"/api/tournaments/{'f' * 32}").status_code == 200
