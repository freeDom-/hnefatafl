"""Lightweight tournament summaries: cheap listing without retained match history."""

import json
import time
from types import SimpleNamespace

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.server import create_app
from hnefatafl.storage import Store
from hnefatafl.tournaments import TournamentManager


class LiveProcess:
    """Deterministic stand-in for a worker handle owned by this process."""

    def __init__(self, *, running: bool = True):
        self.running = running

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        return 0


def decode_sizes(monkeypatch):
    """Record the size of every JSON payload the storage layer decodes."""
    import hnefatafl.storage as storage

    sizes = []
    original = storage.json

    def loads(payload, *args, **kwargs):
        if isinstance(payload, str):
            sizes.append(len(payload))
        return original.loads(payload, *args, **kwargs)

    monkeypatch.setattr(storage, "json", SimpleNamespace(loads=loads, dumps=original.dumps))
    return sizes


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


def test_listing_never_reads_a_full_record_for_a_running_tournament(tmp_path, monkeypatch):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    store.save_tournament(stored_tournament("a" * 32))
    store.save_tournament(stored_tournament("b" * 32))
    interrupted = stored_tournament("c" * 32, status="running")
    interrupted["pid"] = 2147483647
    store.save_tournament(interrupted)
    full_size = len(json.dumps(interrupted))
    reads = []
    original = Store.get_tournament

    def spy(self, identifier):
        reads.append(identifier)
        return original(self, identifier)

    monkeypatch.setattr(Store, "get_tournament", spy)
    sizes = decode_sizes(monkeypatch)
    manager = TournamentManager(store, tmp_path)
    # A running tournament is reconciled from the loaded id/pid alone: neither a
    # full read nor a match-history decode happens while polling summaries.
    records = manager.list(summary=True)
    assert {record["id"] for record in records} == {"a" * 32, "b" * 32, "c" * 32}
    assert reads == []
    assert max(sizes) < full_size / 10
    interrupted_summary = next(record for record in records if record["id"] == "c" * 32)
    assert interrupted_summary["status"] == "failed"
    assert interrupted_summary["ended_at"] and interrupted_summary["error"]
    assert "result" not in interrupted_summary
    # The complete listing still needs the retained history of an interrupted run.
    store.save_tournament(interrupted)
    reads.clear()
    complete = manager.list(summary=False)
    assert reads == ["c" * 32]
    assert complete[2]["status"] == "failed"
    assert len(store.get_tournament("c" * 32)["result"]["games"]) == 120
    assert store.get_tournament("c" * 32)["status"] == "failed"


def test_healthy_running_tournament_keeps_polling_lightweight(tmp_path, monkeypatch):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    running = stored_tournament("d" * 32, status="running")
    store.save_tournament(running)
    full_size = len(json.dumps(running))
    reads = []
    original = Store.get_tournament

    def spy(self, identifier):
        reads.append(identifier)
        return original(self, identifier)

    monkeypatch.setattr(Store, "get_tournament", spy)
    sizes = decode_sizes(monkeypatch)
    manager = TournamentManager(store, tmp_path)
    process = LiveProcess()
    manager.processes["d" * 32] = process
    for _ in range(3):
        records = manager.list(summary=True)
        assert records[0]["status"] == "running"
        assert records[0]["progress"] == {"completed_games": 120, "total_games": 120}
        assert "result" not in records[0]
    assert reads == []
    assert max(sizes) < full_size / 10
    # The worker exits unexpectedly: the next listing reports the failure and
    # keeps every completed game.
    process.running = False
    failed = manager.list(summary=True)[0]
    assert failed["status"] == "failed"
    assert failed["ended_at"] and failed["error"]
    assert manager.processes == {}
    assert len(store.get_tournament("d" * 32)["result"]["games"]) == 120


def test_reconciliation_never_overwrites_a_worker_terminal_state(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    completed = stored_tournament("e" * 32, status="completed")
    completed["ended_at"] = "2026-10-31T15:05:00Z"
    store.save_tournament(completed)
    manager = TournamentManager(store, tmp_path)
    manager.processes["e" * 32] = LiveProcess(running=False)
    record = manager.list(summary=True)[0]
    assert record["status"] == "completed"
    assert record["ended_at"] == "2026-10-31T15:05:00Z"
    assert record["error"] is None
    # The conditional transition refuses to touch a terminal row, even directly.
    unchanged = store.fail_running_tournament(
        "e" * 32, ended_at="2026-11-01T00:00:00Z", error="ignored"
    )
    assert unchanged["status"] == "completed"
    assert unchanged["ended_at"] == "2026-10-31T15:05:00Z"
    assert store.get_tournament("e" * 32)["status"] == "completed"
    assert store.fail_running_tournament("missing", ended_at="x", error="y") is None


def test_failed_reconciliation_keeps_results_and_allows_deletion(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    interrupted = stored_tournament("f" * 32, status="running")
    store.save_tournament(interrupted)
    manager = TournamentManager(store, tmp_path)
    record = manager.list(summary=True)[0]
    assert record["status"] == "failed"
    stored = store.get_tournament("f" * 32)
    assert stored["status"] == "failed"
    assert len(stored["result"]["games"]) == 120
    assert stored["result"]["scores"] == [60.0, 60.0]
    # Cancelling a stopped worker leaves the failure state untouched.
    assert manager.cancel("f" * 32)["status"] == "failed"
    assert manager.delete("f" * 32) is True
    assert store.get_tournament("f" * 32) is None


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
        # A running tournament with a large history is summarised cheaply.
        assert len(json.dumps(summary)) < len(json.dumps(interrupted)) / 10
        detail = client.get(f"/api/tournaments/{'f' * 32}").json()
        assert detail["status"] == "failed"
        assert len(detail["result"]["games"]) == 120
        assert detail["error"] == summary["error"]
        assert client.delete(f"/api/tournaments/{'f' * 32}").status_code == 200
