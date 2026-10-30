"""Packed replay parity, optimizer/RNG equivalence and CUDA lookahead ownership."""

import random

import pytest
import torch

from hnefatafl.engine import apply_move, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, encode_state, load_checkpoint
from hnefatafl.replay_pipeline import PackedReplay, ReplayBatches
from hnefatafl.training import normalize_config, optimize_replay, train
from hnefatafl.training_augmentation import AugmentationStats, collate_batch, sample_symmetries


@pytest.fixture(autouse=True)
def threads():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def examples(rule):
    state = new_game(rule)
    rng = random.Random(17)
    rows = []
    for index in range(5):
        moves = legal_moves(state)
        ids = [move.source * state.size**2 + move.target for move in moves]
        selected = ids[: min(7, len(ids))]
        rows.append(
            {
                "input": encode_state(state),
                "actions": selected,
                "probabilities": [1 / len(selected)] * len(selected),
                "legal": ids,
                "value": (index - 2) / 2,
            }
        )
        state = apply_move(state, rng.choice(moves))
    return rows


@pytest.mark.parametrize("rule", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("augmenting", [False, True])
def test_packed_matches_legacy_with_ragged_and_duplicate_indices(rule, augmenting):
    replay = examples(rule)
    packed = PackedReplay(replay)
    indices = [4, 0, 3, 1, 1, 2, 0, 4]
    symmetries = list(range(8)) if augmenting else None
    stats = AugmentationStats()
    actual = packed.collate(indices, symmetries, stats)
    expected = collate_batch([replay[index] for index in indices], "cpu", symmetries)
    assert all(torch.equal(left, right) for left, right in zip(actual, expected))
    assert stats.exposures == (8 if augmenting else 0)
    assert packed.actions.numel() == sum(len(row["actions"]) for row in replay)
    assert packed.legal.numel() == sum(len(row["legal"]) for row in replay)
    assert all(
        tensor.shape != (len(replay), packed.size**4)
        for tensor in vars(packed).values()
        if isinstance(tensor, torch.Tensor)
    )


@pytest.mark.parametrize("mode", ["off", "random_d4"])
def test_cpu_updates_losses_and_rng_are_identical_to_legacy(mode):
    replay = examples("brandubh") * 3
    states = []
    for pipeline in ("legacy", "packed"):
        torch.manual_seed(42)
        model = PolicyValueNet("brandubh", 4, 0)
        config = normalize_config(
            {
                "preset": "smoke",
                "epochs": 2,
                "batch_size": 4,
                "training_symmetry_mode": mode,
                "replay_pipeline": pipeline,
            }
        )
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
        scaler = torch.amp.GradScaler("cuda", enabled=False)
        rng, augment_rng = random.Random(9), random.Random(10)
        result = optimize_replay(
            model, optimizer, scaler, replay, config, torch.device("cpu"), False, rng, augment_rng
        )
        states.append((model.state_dict(), result, rng.getstate(), augment_rng.getstate()))
    left, right = states
    assert all(torch.equal(tensor, right[0][key]) for key, tensor in left[0].items())
    assert left[1]["policy_losses"] == right[1]["policy_losses"]
    assert left[1]["value_losses"] == right[1]["value_losses"]
    assert left[1]["presented"] == right[1]["presented"] == 30
    assert left[2:] == right[2:]


def test_cancel_before_batch_keeps_rng_and_rejects_invalid_replay():
    model = PolicyValueNet("brandubh", 4, 0)
    config = normalize_config({"preset": "smoke"})
    optimizer = torch.optim.AdamW(model.parameters())
    scaler = torch.amp.GradScaler("cuda", enabled=False)
    augment_rng = random.Random(12)
    saved = augment_rng.getstate()
    with pytest.raises(InterruptedError):
        optimize_replay(
            model,
            optimizer,
            scaler,
            examples("brandubh"),
            config,
            torch.device("cpu"),
            False,
            random.Random(11),
            augment_rng,
            should_stop=lambda: True,
        )
    assert augment_rng.getstate() == saved
    with pytest.raises(ValueError):
        PackedReplay([])
    bad = examples("brandubh")
    bad[0]["actions"] = [7**4]
    with pytest.raises(ValueError):
        PackedReplay(bad)


def test_legacy_to_packed_resume_matches_uninterrupted_updates(tmp_path):
    config = {
        "preset": "smoke",
        "device": "cpu",
        "cpu_threads": 1,
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "registry_dir": str(tmp_path / "models"),
    }
    first = train({**config, "replay_pipeline": "legacy", "output_dir": str(tmp_path / "first")})
    resumed = train(
        {
            **config,
            "resume": first["checkpoint"],
            "iterations": 2,
            "replay_pipeline": "packed",
            "output_dir": str(tmp_path / "resumed"),
        }
    )
    full = train(
        {
            **config,
            "iterations": 2,
            "replay_pipeline": "packed",
            "output_dir": str(tmp_path / "full"),
        }
    )
    _, left = load_checkpoint(resumed["checkpoint"])
    _, right = load_checkpoint(full["checkpoint"])
    assert all(
        torch.equal(value, right["state_dict"][key]) for key, value in left["state_dict"].items()
    )
    assert left["extra"]["augmentation_rng"] == right["extra"]["augmentation_rng"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cuda_prefetch_matches_inputs_and_commits_only_consumed_rng():
    replay = examples("brandubh") * 4
    loader = ReplayBatches(PackedReplay(replay), torch.device("cuda"))
    rng, reference = random.Random(17), random.Random(17)
    stats = AugmentationStats()
    batches = loader.epoch(list(range(len(replay))), 4, rng, True, stats)
    try:
        for start in (0, 4):
            actual = next(batches)
            expected = collate_batch(
                replay[start : start + 4], "cuda", sample_symmetries(reference, 4)
            )
            assert all(torch.equal(left, right) for left, right in zip(actual, expected))
    finally:
        batches.close()
        loader.close()
    assert rng.getstate() == reference.getstate()
    assert stats.exposures == 8


def test_pipeline_configuration_rejects_unknown_values():
    with pytest.raises(ValueError):
        normalize_config({"replay_pipeline": "unknown"})


def test_auto_cpu_keeps_legacy_without_packing(monkeypatch):
    from hnefatafl import replay_pipeline

    def forbidden(*args):
        raise AssertionError("Automatic CPU mode must keep the measured legacy fallback")

    monkeypatch.setattr(replay_pipeline, "PackedReplay", forbidden)
    model = PolicyValueNet("brandubh", 4, 0)
    config = normalize_config({"preset": "smoke"})
    result = optimize_replay(
        model,
        torch.optim.AdamW(model.parameters()),
        torch.amp.GradScaler("cuda", enabled=False),
        examples("brandubh"),
        config,
        torch.device("cpu"),
        False,
        random.Random(9),
        random.Random(10),
    )
    assert result["updates"] > 0 and result["pipeline"] == "legacy"


def test_packed_remote_jobs_require_a_capable_worker():
    from hnefatafl.remote_training import worker_compatible

    config = normalize_config({"preset": "smoke", "training_symmetry_mode": "off"})
    assert worker_compatible(config, {})
    assert not worker_compatible({**config, "replay_pipeline": "packed"}, {})
    assert worker_compatible(
        {**config, "replay_pipeline": "packed"}, {"replay_pipelines": ["auto", "packed", "legacy"]}
    )


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cuda_prefetch_optimizer_and_scaler_match_legacy():
    replay = examples("brandubh") * 3
    results = []
    for pipeline in ("legacy", "auto"):
        torch.manual_seed(42)
        model = PolicyValueNet("brandubh", 4, 0).cuda()
        config = normalize_config(
            {"preset": "smoke", "epochs": 2, "batch_size": 4, "replay_pipeline": pipeline}
        )
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
        scaler = torch.amp.GradScaler("cuda", enabled=True)
        rng, augment_rng = random.Random(9), random.Random(10)
        optimized = optimize_replay(
            model, optimizer, scaler, replay, config, torch.device("cuda"), True, rng, augment_rng
        )
        results.append((model.state_dict(), scaler.state_dict(), augment_rng.getstate(), optimized))
    left, right = results
    assert all(
        torch.allclose(value, right[0][key], atol=1e-5, rtol=1e-5) for key, value in left[0].items()
    )
    assert left[1] == right[1] and left[2] == right[2]
    assert left[3]["policy_losses"] == pytest.approx(right[3]["policy_losses"], abs=1e-5)
