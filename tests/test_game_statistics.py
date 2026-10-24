"""Actual CPU self-play counters and portable checkpoint lineage."""

import json

import pytest
import torch

from hnefatafl import training
from hnefatafl.game_statistics import checkpoint_statistics, recorded_count, self_play_statistics
from hnefatafl.models import ModelRegistry, export_checkpoint, load_checkpoint, save_checkpoint


def config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "device": "cpu",
        "cpu_threads": 1,
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "mcts_time_limit": 5,
        "output_dir": str(tmp_path / "job"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


def test_actual_counts_live_truncated_games_and_repeated_resumes(tmp_path):
    snapshots = []
    first = training.train(config(tmp_path / "a", games=2), snapshots.append)
    assert first["self_play_games"] == self_play_statistics(2, 0)
    live = [p for p in snapshots if p["stage"] == "self_play"]
    assert any(p["self_play_games"]["job"] == 1 for p in live)
    assert live[-1]["self_play_games"]["job"] == 2
    assert first["performance"]["truncated_games"] == 2
    assert first["metrics"]["evaluation"]["truncated"] == 2
    previous = first
    for name, iterations, baseline in (("b", 2, 2), ("c", 3, 4)):
        previous = training.train(
            config(tmp_path / name, games=2, iterations=iterations, resume=previous["checkpoint"])
        )
        assert previous["self_play_games"] == self_play_statistics(2, baseline)
        assert previous["games_generated"] == baseline + 2
        _, payload = load_checkpoint(previous["checkpoint"])
        assert payload["extra"]["games_generated"] == baseline + 2
        assert payload["metadata"]["self_play_games"] == previous["self_play_games"]
    assert (
        json.loads((tmp_path / "c/job/progress.json").read_text())["self_play_games"]
        == previous["self_play_games"]
    )


@pytest.mark.parametrize("failure", [False, True])
def test_partial_selfplay_counts_survive_cancellation_failure_and_recovery(tmp_path, failure):
    stop = False

    def progress(state):
        nonlocal stop
        if state["self_play_games"]["job"] == 1 and state["stage"] == "self_play":
            stop = True
            if failure:
                raise RuntimeError("simulated worker failure")

    if failure:
        with pytest.raises(RuntimeError, match="simulated worker failure"):
            training.train(config(tmp_path, games=3), progress)
    else:
        result = training.train(config(tmp_path, games=3), progress, lambda: stop)
        assert result["status"] == "cancelled"
    _, saved = load_checkpoint(tmp_path / "job/last.pt")
    assert saved["extra"]["games_generated"] == 1
    assert saved["metadata"]["self_play_games"] == self_play_statistics(1, 0)
    state = json.loads((tmp_path / "job/progress.json").read_text())
    assert state["status"] == ("failed" if failure else "cancelled")
    assert state["self_play_games"]["job"] == 1
    resumed = training.train(
        config(tmp_path / "resume", games=2, resume=str(tmp_path / "job/last.pt"))
    )
    assert resumed["self_play_games"] == self_play_statistics(2, 1)


def test_genuine_zero_and_unknown_legacy_baseline_are_different(tmp_path):
    zero = training.train(config(tmp_path / "zero"), should_stop=lambda: True)
    assert zero["self_play_games"] == self_play_statistics(0, 0)
    model, payload = load_checkpoint(zero["checkpoint"])
    del payload["metadata"]["self_play_games"]
    del payload["extra"]["games_generated"]
    legacy = save_checkpoint(
        tmp_path / "legacy.pt",
        model,
        payload["metadata"],
        torch.optim.AdamW(model.parameters()),
        payload["extra"],
    )
    resumed = training.train(config(tmp_path / "resumed", resume=str(legacy)))
    assert resumed["self_play_games"] == self_play_statistics(1, None)
    assert resumed["games_generated"] is None
    assert resumed["model"]["self_play_games"]["cumulative"] is None
    again = training.train(config(tmp_path / "again", iterations=2, resume=resumed["checkpoint"]))
    assert again["self_play_games"] == self_play_statistics(1, None)


def test_best_checkpoint_keeps_earlier_count_and_exports_preserve_it(tmp_path):
    # Two-ply games cannot finish; tied evaluation scores retain iteration one's model.
    result = training.train(config(tmp_path, games=2, iterations=3, save_all_best=True))
    assert result["self_play_games"]["cumulative"] == 6
    assert result["model"]["version"] == 1
    assert result["model"]["self_play_games"] == self_play_statistics(2, 0)
    for path in (
        result["best_checkpoint"],
        result["inference_checkpoint"],
        result["model"]["path"],
    ):
        assert load_checkpoint(path)[1]["metadata"]["self_play_games"]["cumulative"] == 2
    exported = export_checkpoint(result["model"]["path"], tmp_path / "export.pt")
    imported = ModelRegistry(tmp_path / "imported").import_model(exported)
    assert imported["self_play_games"]["cumulative"] == 2
    retained = ModelRegistry(tmp_path / "models").list()
    assert all(entry["self_play_games"]["cumulative"] == 2 for entry in retained)


def test_old_full_checkpoint_counter_survives_export_and_registry(tmp_path):
    result = training.train(config(tmp_path))
    model, payload = load_checkpoint(result["checkpoint"])
    del payload["metadata"]["self_play_games"]
    legacy = save_checkpoint(
        tmp_path / "legacy.pt", model, payload["metadata"], extra=payload["extra"]
    )
    exported = export_checkpoint(legacy, tmp_path / "export.pt")
    assert load_checkpoint(exported)[1]["metadata"]["self_play_games"] == {
        "version": 1,
        "job": None,
        "baseline": None,
        "cumulative": 1,
    }
    assert (
        ModelRegistry(tmp_path / "legacy-models").import_model(legacy)["self_play_games"][
            "cumulative"
        ]
        == 1
    )
    assert checkpoint_statistics({"metadata": {}, "extra": {}}) is None


@pytest.mark.parametrize("value", [True, -1, 1.5, "3", 2**53, None])
def test_invalid_counters_are_unavailable(value):
    assert recorded_count(value) is None


def test_invalid_model_statistics_are_rejected(tmp_path):
    result = training.train(config(tmp_path))
    model, payload = load_checkpoint(result["checkpoint"])
    payload["metadata"]["self_play_games"]["cumulative"] = 99
    with pytest.raises(ValueError, match="Inconsistent self-play"):
        save_checkpoint(tmp_path / "invalid.pt", model, payload["metadata"])
