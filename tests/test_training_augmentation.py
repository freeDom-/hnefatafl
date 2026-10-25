"""Phase 3: D4 training augmentation preserves inputs, policies, masks and values."""

import copy
import random
import time

import pytest
import torch

from hnefatafl.engine import RULESETS, Move, apply_move, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, encode_state
from hnefatafl.symmetry import Symmetry, inverse, transform_state
from hnefatafl.training_augmentation import (
    AugmentationStats,
    action_map,
    augmentation_rng,
    collate_batch,
    invert_example,
    sample_symmetries,
    transform_actions,
    transform_example,
    transform_input,
    validate_mode,
)
from test_engine import position


def example_for(state, rng, value=0.5):
    """Replay-shaped example with a random normalized policy over a legal subset."""
    moves = legal_moves(state)
    area = state.size**2
    chosen = rng.sample(moves, k=min(len(moves), 1 + rng.randrange(6)))
    weights = [rng.random() + 0.01 for _ in chosen]
    total = sum(weights)
    return {
        "input": encode_state(state),
        "actions": [m.source * area + m.target for m in chosen],
        "probabilities": [w / total for w in weights],
        "legal": [m.source * area + m.target for m in moves],
        "value": value,
        "note": {"metadata": "kept"},
    }


def policy_of(example):
    return dict(zip(example["actions"], example["probabilities"]))


def played_states(rule, seed, plies=25):
    rng = random.Random(seed)
    state = new_game(rule)
    states = [state]
    for _ in range(plies):
        moves = legal_moves(state)
        if state.winner or not moves:
            break
        state = apply_move(state, rng.choice(moves))
        states.append(state)
    return states


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("symmetry", Symmetry)
def test_transformed_example_equals_example_of_transformed_state(rule, symmetry):
    rng = random.Random(int(symmetry) * 31 + len(rule))
    for state in played_states(rule, 3)[::4]:
        if state.winner:
            continue
        example = example_for(state, rng, value=-1.0)
        frozen = copy.deepcopy(example)
        result = transform_example(example, symmetry)
        # No mutation of the stored replay row.
        assert torch.equal(example["input"], frozen["input"])
        assert all(example[key] == frozen[key] for key in ("actions", "probabilities", "legal"))
        moved = transform_state(state, symmetry)
        assert torch.equal(result["input"], encode_state(moved))
        assert set(result["legal"]) == {
            m.source * state.size**2 + m.target for m in legal_moves(moved)
        }
        assert len(set(result["legal"])) == len(result["legal"])
        assert set(result["actions"]) <= set(result["legal"])
        assert result["value"] == -1.0
        assert result["note"] == {"metadata": "kept"}
        assert sum(result["probabilities"]) == pytest.approx(1.0)
        # Each probability stays with the geometrically corresponding move.
        for action, probability in zip(example["actions"], example["probabilities"]):
            (new,) = transform_actions([action], state.size, symmetry)
            assert policy_of(result)[new] == probability
        back = invert_example(result, symmetry)
        assert torch.equal(back["input"], example["input"])
        assert back["actions"] == example["actions"]
        assert back["probabilities"] == example["probabilities"]
        assert back["legal"] == example["legal"]


@pytest.mark.parametrize("rule", RULESETS)
def test_action_maps_are_permutations_with_correct_inverses(rule):
    size = RULESETS[rule].size
    table = action_map(size)
    assert table.shape == (8, size**4)
    assert action_map(size) is table
    for symmetry in Symmetry:
        assert sorted(table[symmetry].tolist()) == list(range(size**4))
        composed = table[inverse(symmetry)][table[symmetry]]
        assert torch.equal(composed, torch.arange(size**4))
    assert torch.equal(table[Symmetry.IDENTITY], torch.arange(size**4))


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("symmetry", Symmetry)
def test_static_planes_are_invariant_and_input_batches_match(rule, symmetry):
    state = new_game(rule)
    encoded = encode_state(state)
    moved = transform_input(encoded, symmetry)
    # Throne and restricted-corner planes are D4-invariant; Tablut has no corners.
    assert torch.equal(moved[4], encoded[4])
    assert torch.equal(moved[5], encoded[5])
    batch = torch.stack([encoded, encode_state(played_states(rule, 1)[-1])])
    assert torch.equal(transform_input(batch, symmetry)[1], transform_input(batch[1], symmetry))
    assert moved is not encoded


def special_positions():
    cases = [
        ("brandubh", {(0, 3): "K", (4, 4): "A"}, "defenders"),  # king escape race
        ("brandubh", {(3, 3): "K", (2, 3): "A", (3, 2): "A", (3, 4): "A", (5, 3): "A"}, "attackers"),
        ("tablut", {(4, 4): "K", (4, 1): "A", (0, 5): "D", (6, 6): "A"}, "defenders"),
        ("copenhagen", {(0, 0): "A", (0, 1): "D", (5, 5): "K", (2, 3): "A"}, "defenders"),
        (
            "copenhagen",
            {(0, 3): "D", (0, 4): "D", (0, 5): "D", (0, 6): "A", (1, 3): "A", (1, 4): "A",
             (1, 5): "A", (5, 5): "K", (9, 9): "D", (3, 2): "A"},
            "attackers",
        ),  # shield-wall capture available
        ("copenhagen", {(5, 5): "K", (4, 5): "A", (5, 4): "A", (5, 6): "A", (8, 5): "A"}, "attackers"),
    ]  # fmt: skip
    return [position(rule, pieces, turn) for rule, pieces, turn in cases]


@pytest.mark.parametrize("symmetry", Symmetry)
@pytest.mark.parametrize("index", range(6))
def test_tactical_asymmetric_positions_including_restricted_squares(index, symmetry):
    state = special_positions()[index]
    rng = random.Random(index)
    example = example_for(state, rng)
    moved = transform_state(state, symmetry)
    result = transform_example(example, symmetry)
    assert torch.equal(result["input"], encode_state(moved))
    assert set(result["legal"]) == {m.source * state.size**2 + m.target for m in legal_moves(moved)}
    # Moves onto restricted squares stay absent exactly when they stay illegal.
    corners = {r * state.size + c for r in (0, state.size - 1) for c in (0, state.size - 1)}
    area = state.size**2
    assert {a % area for a in result["legal"]} & corners == {
        m.target for m in legal_moves(moved) if m.target in corners
    }
    # Terminal outcomes agree after applying the corresponding transformed move.
    for action, probability in zip(example["actions"], example["probabilities"]):
        original = Move(action // area, action % area)
        (mapped,) = transform_actions([action], state.size, symmetry)
        after = apply_move(state, original)
        after_moved = apply_move(moved, Move(mapped // area, mapped % area))
        assert (after.winner, after.reason) == (after_moved.winner, after_moved.reason)
        assert after_moved.board == transform_state(after, symmetry).board


def test_transform_validation():
    state = new_game("brandubh")
    example = example_for(state, random.Random(0))
    with pytest.raises(ValueError):
        transform_actions([7**4], 7, Symmetry.REFLECT)
    with pytest.raises(ValueError):
        transform_actions([True], 7, Symmetry.REFLECT)  # type: ignore[list-item]
    with pytest.raises(ValueError):
        transform_input(torch.zeros(6, 3, 4), Symmetry.IDENTITY)
    with pytest.raises(ValueError):
        validate_mode("all_d4")
    assert validate_mode("off") == "off" and validate_mode("random_d4") == "random_d4"
    with pytest.raises(ValueError):
        collate_batch([example], "cpu", [8])
    with pytest.raises(ValueError):
        collate_batch([example], "cpu", [0, 1])


@pytest.mark.parametrize("rule", RULESETS)
def test_collate_matches_per_example_transform_and_off_baseline(rule):
    rng = random.Random(5)
    batch = [
        example_for(s, rng, value=v) for s, v in zip(played_states(rule, 9)[:8], [1, -1, 0] * 3)
    ]
    original = copy.deepcopy(batch)
    inputs, target, mask, values = collate_batch(batch, "cpu")
    for index, row in enumerate(batch):
        assert torch.equal(inputs[index], row["input"])
        assert torch.equal(target[index].nonzero().flatten(), torch.tensor(sorted(row["actions"])))
        assert set(mask[index].nonzero().flatten().tolist()) == set(row["legal"])
        assert values[index].item() == pytest.approx(row["value"])
    symmetries = [i % 8 for i in range(len(batch))]
    stats = AugmentationStats()
    inputs, target, mask, values = collate_batch(batch, "cpu", symmetries, stats)
    assert stats.exposures == len(batch)
    assert stats.counts[0] == 1 and sum(stats.counts) == 8
    for index, (row, symmetry) in enumerate(zip(batch, symmetries)):
        expected = transform_example(row, symmetry)
        assert torch.equal(inputs[index], expected["input"])
        dense = torch.zeros(target.shape[1])
        dense[expected["actions"]] = torch.tensor(expected["probabilities"])
        assert torch.allclose(target[index], dense)
        assert target[index].sum().item() == pytest.approx(1.0, abs=1e-5)
        assert set(mask[index].nonzero().flatten().tolist()) == set(expected["legal"])
        assert (target[index] > 0).logical_and(~mask[index]).sum() == 0
        assert values[index].item() == pytest.approx(row["value"])
    for before, after in zip(original, batch):  # replay rows are never modified
        assert torch.equal(before["input"], after["input"])
        assert all(before[k] == after[k] for k in ("actions", "probabilities", "legal", "value"))


def test_identity_collation_equals_off_and_masked_loss_is_finite():
    rng = random.Random(1)
    states = played_states("brandubh", 4)[:6]
    batch = [example_for(s, rng) for s in states]
    off = collate_batch(batch, "cpu")
    identity = collate_batch(batch, "cpu", [0] * len(batch))
    assert all(torch.equal(a, b) for a, b in zip(off, identity))
    model = PolicyValueNet("brandubh", 4, 0)
    for symmetries in ([0] * 6, [1, 2, 3, 4, 5, 6]):
        inputs, target, mask, values = collate_batch(batch, "cpu", symmetries)
        logits, predicted = model(inputs)
        logits = logits.masked_fill(~mask, -1e9)
        loss = -(target * torch.log_softmax(logits, dim=1)).sum(dim=1).mean()
        assert torch.isfinite(loss) and torch.isfinite(((predicted - values) ** 2).mean())


def test_random_sampling_is_uniform_reproducible_seed_sensitive_and_isolated():
    draws = sample_symmetries(augmentation_rng(7, 0), 8000)
    assert set(draws) == set(range(8))
    assert all(850 < draws.count(i) < 1150 for i in range(8))
    assert draws == sample_symmetries(augmentation_rng(7, 0), 8000)
    assert draws != sample_symmetries(augmentation_rng(8, 0), 8000)
    assert sample_symmetries(augmentation_rng(7, 3), 20) != sample_symmetries(
        augmentation_rng(7, 4), 20
    )
    # Dedicated streams never consume the global or an independent shuffle RNG.
    shuffle = random.Random(3)
    expected = random.Random(3)
    sample_symmetries(augmentation_rng(3, 0), 100)
    assert shuffle.random() == expected.random()
    # Restoring saved state continues the exact sequence.
    stream = augmentation_rng(11, 0)
    sample_symmetries(stream, 13)
    saved = stream.getstate()
    follow = sample_symmetries(stream, 40)
    restored = augmentation_rng(0, 0)
    restored.setstate(saved)
    assert sample_symmetries(restored, 40) == follow


def test_stats_summary_is_compact_and_distinguishes_exposures():
    stats = AugmentationStats()
    stats.counts[0], stats.counts[5] = 3, 2
    stats.seconds = 0.5
    summary = stats.summary("random_d4", 2.0)
    assert summary["exposures"] == 5 and summary["identity"] == 3
    assert summary["optimization_share"] == pytest.approx(0.25)
    assert AugmentationStats().summary("off", 0.0)["optimization_share"] == 0.0


def test_augmentation_overhead_is_small_relative_to_a_training_step():
    rng = random.Random(2)
    batch = [example_for(s, rng) for s in played_states("copenhagen", 2, 40)[:32]]
    batch = (batch * 4)[:128]
    model = PolicyValueNet("copenhagen", 8, 1)
    started = time.perf_counter()
    for _ in range(3):
        collate_batch(batch, "cpu", sample_symmetries(random.Random(0), len(batch)))
    augmented = (time.perf_counter() - started) / 3
    inputs, *_ = collate_batch(batch, "cpu")
    started = time.perf_counter()
    model(inputs)[0].sum().backward()
    step = time.perf_counter() - started
    assert augmented < max(step * 5, 0.5)


# ---------------------------------------------------------------------------
# Real optimizer, checkpoint and resume behavior (CPU)
# ---------------------------------------------------------------------------
from hnefatafl.models import load_checkpoint, save_checkpoint  # noqa: E402
from hnefatafl.training import DEFAULTS, normalize_config, train  # noqa: E402


def smoke(tmp_path, **changes):
    return {
        "preset": "smoke",
        "device": "cpu",
        "seed": 0,
        "mcts_time_limit": 5,
        "cpu_threads": 1,
        "output_dir": str(tmp_path / "training"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


def test_configuration_default_validation_and_backend_enforcement():
    assert DEFAULTS["training_symmetry_mode"] == "random_d4"
    assert normalize_config({"preset": "smoke"})["training_symmetry_mode"] == "random_d4"
    assert normalize_config({"training_symmetry_mode": "random_d4"})["training_symmetry_mode"] == (
        "random_d4"
    )
    for bad in ("all_d4", "", None, True, 1, "RANDOM_D4"):
        with pytest.raises(ValueError):
            normalize_config({"training_symmetry_mode": bad})
    from hnefatafl.training_presets import training_presets

    assert all("training_symmetry_mode" not in p["config"] for p in training_presets())


@pytest.mark.parametrize("ruleset", RULESETS)
@pytest.mark.parametrize("preset", ["recommended", "smoke", "beginner", "balanced"])
def test_random_d4_is_the_default_for_every_new_job_mode(ruleset, preset):
    config = normalize_config({"ruleset": ruleset, "preset": preset})
    assert config["training_symmetry_mode"] == "random_d4"
    # Explicit choices, including disabling augmentation, always win.
    assert (
        normalize_config({"ruleset": ruleset, "preset": preset, "training_symmetry_mode": "off"})[
            "training_symmetry_mode"
        ]
        == "off"
    )


def test_legacy_collation_matches_previous_inline_construction():
    rng = random.Random(8)
    batch = [example_for(s, rng) for s in played_states("tablut", 6)[:7]]
    inputs, target, mask, values = collate_batch(batch, "cpu")
    expected_target = torch.zeros((len(batch), 9**4))
    expected_mask = torch.zeros_like(expected_target, dtype=torch.bool)
    for index, row in enumerate(batch):
        expected_target[index, row["actions"]] = torch.tensor(row["probabilities"])
        expected_mask[index, row["legal"]] = True
    assert torch.equal(target, expected_target) and torch.equal(mask, expected_mask)
    assert torch.equal(inputs, torch.stack([row["input"] for row in batch]))


@pytest.mark.parametrize("ruleset", RULESETS)
def test_real_training_with_random_d4_updates_weights_and_reports_exposures(tmp_path, ruleset):
    torch.manual_seed(0)
    before = PolicyValueNet(ruleset, 4, 0).state_dict()
    result = train(smoke(tmp_path, ruleset=ruleset, training_symmetry_mode="random_d4", epochs=2))
    metrics = result["metrics"]
    augmentation = metrics["augmentation"]
    replay = metrics["replay_positions"]
    # Minibatch/update counts are the same as OFF; exposures are not replay rows.
    batch_size = result["metrics"]["performance"].get("batch_size", 8)
    assert metrics["updates"] == 2 * -(-replay // batch_size)
    assert augmentation["mode"] == "random_d4"
    assert augmentation["exposures"] == augmentation["training_exposures"] == 2 * replay
    assert sum(augmentation["transform_counts"]) == 2 * replay
    assert augmentation["identity"] == augmentation["transform_counts"][0]
    assert metrics["performance"]["augmentation_exposures"] == 2 * replay
    assert 0 <= augmentation["optimization_share"] <= 1
    assert metrics["policy_loss"] > 0 and torch.isfinite(torch.tensor(metrics["value_loss"]))
    model, payload = load_checkpoint(result["checkpoint"])
    assert any(not torch.equal(before[k], v) for k, v in model.state_dict().items())
    assert payload["metadata"]["training_config"]["training_symmetry_mode"] == "random_d4"
    assert "augmentation_rng" in payload["extra"]
    assert len(payload["extra"]["replay"]) == replay  # replay is never multiplied
    assert result["games_generated"] == 1  # augmentation adds no games


def test_off_mode_never_touches_augmentation_state_and_is_deterministic(tmp_path):
    first = train(smoke(tmp_path / "a", epochs=2, training_symmetry_mode="off"))
    second = train(smoke(tmp_path / "b", epochs=2, training_symmetry_mode="off"))
    assert first["metrics"]["augmentation"]["exposures"] == 0
    assert first["metrics"]["augmentation"]["mode"] == "off"
    _, one = load_checkpoint(first["checkpoint"])
    _, two = load_checkpoint(second["checkpoint"])
    assert one["extra"]["augmentation_rng"] == augmentation_rng(0, 0).getstate()
    for key, value in one["state_dict"].items():
        torch.testing.assert_close(value, two["state_dict"][key], rtol=0, atol=0)


def test_same_seed_reproduces_and_different_seed_changes_augmented_training(tmp_path):
    options = {"training_symmetry_mode": "random_d4", "epochs": 2}
    a = train(smoke(tmp_path / "a", **options))
    b = train(smoke(tmp_path / "b", **options))
    c = train(smoke(tmp_path / "c", seed=1, **options))
    off = train(smoke(tmp_path / "off", epochs=2, training_symmetry_mode="off"))
    states = [load_checkpoint(r["checkpoint"])[1] for r in (a, b, c, off)]
    assert states[0]["extra"]["augmentation_rng"] == states[1]["extra"]["augmentation_rng"]
    assert states[0]["extra"]["augmentation_rng"] != states[2]["extra"]["augmentation_rng"]
    assert (
        a["metrics"]["augmentation"]["transform_counts"]
        == (b["metrics"]["augmentation"]["transform_counts"])
    )
    for key, value in states[0]["state_dict"].items():
        torch.testing.assert_close(value, states[1]["state_dict"][key], rtol=0, atol=0)
    assert any(
        not torch.equal(value, states[3]["state_dict"][key])
        for key, value in states[0]["state_dict"].items()
    )


def test_resumed_augmented_training_matches_uninterrupted_training(tmp_path):
    common = {"training_symmetry_mode": "random_d4", "epochs": 2}
    first = train(smoke(tmp_path / "split", **common))
    source_bytes = open(first["checkpoint"], "rb").read()
    resumed = train(smoke(tmp_path / "resumed", **common, iterations=2, resume=first["checkpoint"]))
    full = train(smoke(tmp_path / "full", **common, iterations=2))
    assert open(first["checkpoint"], "rb").read() == source_bytes  # source never rewritten
    _, restored = load_checkpoint(resumed["checkpoint"])
    _, complete = load_checkpoint(full["checkpoint"])
    assert restored["extra"]["augmentation_rng"] == complete["extra"]["augmentation_rng"]
    assert restored["metadata"]["training_config"]["training_symmetry_mode"] == "random_d4"
    assert (
        resumed["metrics"]["augmentation"]["transform_counts"]
        == (full["metrics"]["augmentation"]["transform_counts"])
    )
    for key, value in restored["state_dict"].items():
        torch.testing.assert_close(value, complete["state_dict"][key], rtol=0, atol=0)
    assert restored["extra"]["replay"] and len(restored["extra"]["replay"]) == len(
        complete["extra"]["replay"]
    )


def test_legacy_checkpoint_resumes_off_and_can_explicitly_enable_augmentation(tmp_path):
    first = train(smoke(tmp_path / "base", epochs=2))
    model, payload = load_checkpoint(first["checkpoint"])
    # Recreate a pre-Phase-3 file: no saved setting and no augmentation stream.
    metadata = payload["metadata"]
    del metadata["training_config"]["training_symmetry_mode"]
    legacy_extra = {k: v for k, v in payload["extra"].items() if k != "augmentation_rng"}
    from hnefatafl.training import _make_optimizer

    optimizer = _make_optimizer(model, metadata["training_config"])
    optimizer.load_state_dict(payload["optimizer_state"])
    legacy = save_checkpoint(tmp_path / "legacy.pt", model, metadata, optimizer, legacy_extra)
    legacy_bytes = legacy.read_bytes()
    unchanged = train(smoke(tmp_path / "off", epochs=2, iterations=2, resume=str(legacy)))
    assert unchanged["metrics"]["augmentation"]["mode"] == "off"
    assert unchanged["metrics"]["augmentation"]["exposures"] == 0
    enabled = train(
        smoke(
            tmp_path / "on",
            epochs=2,
            iterations=2,
            resume=str(legacy),
            training_symmetry_mode="random_d4",
        )
    )
    assert legacy.read_bytes() == legacy_bytes
    assert enabled["metrics"]["augmentation"]["exposures"] > 0
    _, saved = load_checkpoint(enabled["checkpoint"])
    assert saved["metadata"]["training_config"]["training_symmetry_mode"] == "random_d4"
    # The deterministic derivation makes legacy→random_d4 repeatable.
    again = train(
        smoke(
            tmp_path / "again",
            epochs=2,
            iterations=2,
            resume=str(legacy),
            training_symmetry_mode="random_d4",
        )
    )
    _, repeated = load_checkpoint(again["checkpoint"])
    for key, value in saved["state_dict"].items():
        torch.testing.assert_close(value, repeated["state_dict"][key], rtol=0, atol=0)
    # Existing learning state is retained through the OFF→random_d4 switch.
    assert enabled["games_generated"] == 2
    assert [row["iteration"] for row in enabled["history"]] == [1, 2]


def test_explicit_off_to_random_resume_records_effective_configuration(tmp_path):
    first = train(smoke(tmp_path / "off", training_symmetry_mode="off"))
    resumed = train(
        smoke(
            tmp_path / "on",
            iterations=2,
            resume=first["checkpoint"],
            training_symmetry_mode="random_d4",
        )
    )
    assert json_config(resumed)["training_symmetry_mode"] == "random_d4"
    _, original = load_checkpoint(first["checkpoint"])
    assert original["metadata"]["training_config"]["training_symmetry_mode"] == "off"


def json_config(result):
    import json
    from pathlib import Path

    return json.loads((Path(result["output_dir"]) / "config.json").read_text())


def test_consistency_metrics_are_zero_for_a_symmetric_function_and_positive_otherwise():
    from hnefatafl.symmetry_benchmark import consistency, fixed_positions, wilson

    class Equivariant(torch.nn.Module):
        """Uniform logits and a constant value are exactly D4-invariant."""

        size = 7

        def forward(self, inputs):
            return torch.zeros(len(inputs), 7**4), torch.full((len(inputs), 1), 0.25)

    states = fixed_positions("brandubh", 10, seed=1)
    assert states == fixed_positions("brandubh", 10, seed=1)
    perfect = consistency(Equivariant(), states)  # type: ignore[arg-type]
    assert perfect["mean_policy_total_variation"] < 1e-6
    assert perfect["mean_value_difference"] == 0 and perfect["max_value_difference"] == 0
    assert perfect["top_move_disagreement"] in (None, 0)
    torch.manual_seed(0)
    random_net = consistency(PolicyValueNet("brandubh", 8, 1), states)
    assert random_net["mean_policy_total_variation"] > 1e-4
    assert len(random_net["per_symmetry"]) == 7
    low, high = wilson(5, 10)
    assert 0 < low < 0.5 < high < 1 and wilson(0, 0) == [0.0, 1.0]
