"""Exact-input reuse with actual neural forwards, independent search and generations."""

from dataclasses import replace

import pytest
import torch

from hnefatafl.ai import mcts_policy
from hnefatafl.engine import RULESETS, apply_move, legal_moves, new_game
from hnefatafl.inference import ExactInferenceCache, InferenceMetrics, policy_value_batch
from hnefatafl.inference_backends import TorchInference, OpenVINOInference
from hnefatafl.models import PolicyValueNet
from hnefatafl.symmetry import Symmetry, transform_state
from hnefatafl.training import generate_self_play, normalize_config, train


def compare(actual, expected):
    for (policy, value), (reference, reference_value) in zip(actual, expected, strict=True):
        assert list(policy) == list(reference)
        assert list(policy.values()) == pytest.approx(list(reference.values()), abs=1e-6, rel=1e-5)
        assert value == pytest.approx(reference_value, abs=1e-6, rel=1e-5)
        if policy:
            assert sum(policy.values()) == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize("rule", RULESETS)
def test_exact_history_terminal_ordering_mutation_and_off(rule):
    model = PolicyValueNet(rule, 4, 0).eval()
    state = new_game(rule)
    other = apply_move(state, legal_moves(state)[0])
    history = replace(state, history=other.history, ply=100)
    terminal = replace(state, winner="attackers", reason="threefold_repetition")
    states = [terminal, state, other, history, terminal, other]
    metrics = InferenceMetrics()
    cache = ExactInferenceCache()
    reference = policy_value_batch(states, model)
    result = policy_value_batch(states, model, cache=cache, metrics=metrics)
    compare(result, reference)
    assert result[0] == result[4] == ({}, -1)
    assert set(result[1][0]) == set(legal_moves(state))
    measured = metrics.snapshot()
    assert (
        measured["inference_requests"],
        measured["neural_evaluations"],
        measured["inference_cache_misses"],
        measured["inference_batch_duplicates"],
    ) == (4, 2, 2, 2)
    assert measured["inference_cache_hits"] == 0
    assert measured["inference_cache_entries"] == measured["inference_cache_peak_entries"] == 2
    result[1][0].clear()
    assert result[3][0]
    compare(policy_value_batch(states, model, cache=cache, metrics=metrics), reference)
    measured = metrics.snapshot()
    assert measured["inference_cache_hits"] == 2
    assert measured["inference_batch_duplicates"] == 4
    assert measured["inference_cache_hit_rate"] == 0.5
    assert measured["neural_evaluations"] == 2
    assert (
        measured["inference_requests"]
        == measured["inference_cache_hits"]
        + measured["inference_cache_misses"]
        + measured["inference_batch_duplicates"]
    )
    assert policy_value_batch([], model, cache=cache) == []
    assert policy_value_batch([terminal], model, cache=cache) == [({}, -1)]
    assert len(cache.entries) == 2
    off = InferenceMetrics()
    compare(policy_value_batch(states, model, metrics=off), reference)
    assert off.evaluations == off.requests == off.cache_misses == 4
    assert off.cache_hits == off.duplicates == 0


def test_orientations_turns_and_models_are_distinct_and_lru_is_bounded():
    state = apply_move(new_game("brandubh"), legal_moves(new_game("brandubh"))[0])
    rotated = transform_state(state, Symmetry.ROTATE_90)
    other_turn = replace(state, turn="attackers")
    assert state.board != rotated.board and state.turn != other_turn.turn
    model = PolicyValueNet("brandubh", 4, 0).eval()
    cache = ExactInferenceCache(2)
    metrics = InferenceMetrics()
    policy_value_batch([state, rotated], model, cache=cache, metrics=metrics)
    policy_value_batch([state], model, cache=cache, metrics=metrics)  # touch state
    policy_value_batch([other_turn], model, cache=cache, metrics=metrics)  # evicts rotated
    policy_value_batch([state], model, cache=cache, metrics=metrics)
    assert metrics.evaluations == 3 and metrics.cache_hits == 2
    policy_value_batch([rotated], model, cache=cache, metrics=metrics)
    assert metrics.evaluations == 4 and len(cache.entries) == 2
    different_model = PolicyValueNet("brandubh", 4, 0).eval()
    compare(
        policy_value_batch([state], different_model, cache=cache),
        policy_value_batch([state], different_model),
    )
    assert len(cache.entries) == 1
    cache.clear()
    assert len(cache.entries) == 0


@pytest.mark.parametrize("change", ["weights", "load", "backend", "refresh", "precision"])
def test_in_place_weights_backend_and_precision_invalidation(change):
    torch.manual_seed(83)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    backend = TorchInference(model, {})
    cache, metrics = ExactInferenceCache(), InferenceMetrics()
    state = new_game("brandubh")
    kwargs = {"backend": backend}
    before = policy_value_batch([state], model, cache=cache, metrics=metrics, **kwargs)
    if change == "weights":
        with torch.no_grad():
            model.value_head[-2].bias.add_(0.5)
    elif change == "load":
        fresh = PolicyValueNet("brandubh", 4, 0)
        model.load_state_dict(fresh.state_dict())
    elif change == "backend":
        kwargs["backend"] = TorchInference(model, {})
    elif change == "refresh":
        backend.refresh()
    else:
        kwargs["mixed_precision"] = True
    after = policy_value_batch([state], model, cache=cache, metrics=metrics, **kwargs)
    compare(after, policy_value_batch([state], model, **kwargs))
    assert metrics.evaluations == 2 and metrics.cache_hits == 0
    if change in ("weights", "load"):
        assert before[0][1] != after[0][1]


@pytest.mark.parametrize("mode", ["sequential", "batched"])
@pytest.mark.parametrize("rule", RULESETS)
def test_seeded_self_play_same_budgets_targets_noise_and_bootstrap(rule, mode):
    torch.manual_seed(19)
    model = PolicyValueNet(rule, 4, 0).eval()
    config = normalize_config(
        {
            "preset": "smoke",
            "ruleset": rule,
            "games": 3,
            "mcts_iterations": 5,
            "max_game_plies": 6,
            "mcts_time_limit": 300,
            "self_play_mode": mode,
            "mixed_precision": False,
        }
    )
    baseline, baseline_metrics = generate_self_play(model, config, seed=23)
    exact_config = {**config, "inference_cache_mode": "exact"}
    optimized, metrics = generate_self_play(model, exact_config, seed=23)
    repeated, _ = generate_self_play(model, exact_config, seed=23)
    for reference, actual, again in zip(baseline, optimized, repeated, strict=True):
        for key in reference[1].keys() - {"duration", "bootstrap_value"}:
            assert reference[1][key] == actual[1][key] == again[1][key]
        for a, b, c in zip(reference[0], actual[0], again[0], strict=True):
            assert torch.equal(a["input"], b["input"]) and torch.equal(b["input"], c["input"])
            for key in ("actions", "legal", "probabilities"):
                assert a[key] == b[key] == c[key]
            assert a["value"] == pytest.approx(b["value"], abs=1e-6)
    assert metrics["mcts_simulations"] == baseline_metrics["mcts_simulations"]
    assert metrics["search_deadline_cutoffs"] == 0
    assert metrics["inference_requests"] == baseline_metrics["neural_evaluations"]
    assert metrics["neural_evaluations"] < metrics["inference_requests"]
    assert metrics["inference_cache_hits"] > 0
    if mode == "batched":
        assert metrics["inference_batch_duplicates"] > 0


def test_noise_is_search_local_and_never_stored_and_cancellation():
    model = PolicyValueNet("brandubh", 4, 0).eval()
    state, cache = new_game("brandubh"), ExactInferenceCache()
    reference = policy_value_batch([state], model, cache=cache)
    policies = []
    for seed in (1, 7):
        config = {
            "_model": model,
            "_inference_cache": cache,
            "iterations": 12,
            "time_limit": 300,
            "seed": seed,
            "dirichlet_fraction": 1.0,
        }
        policies.append(mcts_policy(state, config))
        compare(policy_value_batch([state], model, cache=cache), reference)
    assert policies[0] != policies[1]
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        return calls > 5

    with pytest.raises(InterruptedError):
        mcts_policy(state, config, should_stop=stop)
    compare(policy_value_batch([state], model, cache=cache), reference)
    # The first visit completes even when a running search subsequently expires.
    details = {}
    mcts_policy(state, {**config, "iterations": 100000, "time_limit": 0.001}, diagnostics=details)
    assert details["simulations"] >= 1 and details["deadline_cutoffs"] == 1


def test_real_optimizer_iterations_and_resume_match_disabled(tmp_path):
    base = {
        "preset": "smoke",
        "device": "cpu",
        "seed": 13,
        "games": 2,
        "iterations": 2,
        "max_game_plies": 4,
        "evaluation_max_plies": 2,
        "mcts_iterations": 3,
        "mcts_time_limit": 300,
        "self_play_mode": "batched",
        "mixed_precision": False,
    }
    from hnefatafl.models import load_checkpoint

    def run(label, **overrides):
        return train(
            {
                **base,
                "output_dir": str(tmp_path / label),
                "registry_dir": str(tmp_path / (label + "-models")),
                **overrides,
            }
        )

    off = run("off")
    exact = run("exact", inference_cache_mode="exact")
    first = run("first", iterations=1, inference_cache_mode="exact")
    resumed = run("resumed", resume=first["checkpoint"], inference_cache_mode="exact")
    weights = [
        load_checkpoint(result["checkpoint"])[0].state_dict() for result in (off, exact, resumed)
    ]
    for key in weights[0]:
        torch.testing.assert_close(weights[0][key], weights[1][key], atol=1e-6, rtol=1e-5)
        torch.testing.assert_close(weights[1][key], weights[2][key], atol=0, rtol=0)


def test_openvino_cpu_snapshot_refresh_and_cache():
    pytest.importorskip("openvino")
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = normalize_config(
        {
            "preset": "smoke",
            "inference_backend": "openvino",
            "inference_device": "CPU",
            "device": "cpu",
            "games": 2,
        }
    )
    backend = OpenVINOInference(model, config)
    cache, metrics = ExactInferenceCache(), InferenceMetrics()
    try:
        backend.refresh()
        states = [new_game("brandubh")] * 2
        compare(
            policy_value_batch(states, model, cache=cache, backend=backend, metrics=metrics),
            policy_value_batch(states, model, backend=backend),
        )
        policy_value_batch(states, model, cache=cache, backend=backend, metrics=metrics)
        assert metrics.evaluations == 1 and metrics.cache_hits == 1
        with torch.no_grad():
            model.value_head[-2].bias.add_(0.5)
        backend.refresh()
        compare(
            policy_value_batch(states, model, cache=cache, backend=backend, metrics=metrics),
            policy_value_batch(states, model, backend=backend),
        )
        assert metrics.evaluations == 2
    finally:
        backend.close()


@pytest.mark.parametrize("capacity", [0, 65537, True, 1.5])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        ExactInferenceCache(capacity)


def test_training_mode_and_mismatched_rules_rejected():
    model = PolicyValueNet("brandubh", 4, 0)
    cache = ExactInferenceCache()
    with pytest.raises(ValueError, match="eval"):
        policy_value_batch([new_game("brandubh")], model, cache=cache)
    with pytest.raises(ValueError, match="ruleset"):
        policy_value_batch([new_game("tablut")], model.eval(), cache=cache)
    assert not cache.entries
    other_model = PolicyValueNet("brandubh", 4, 0).eval()
    with pytest.raises(ValueError, match="different model"):
        policy_value_batch(
            [new_game("brandubh")], model, cache=cache, backend=TorchInference(other_model, {})
        )


@pytest.mark.parametrize("mode", ["sequential", "batched"])
def test_cache_generation_cancelled_mid_search_preserves_no_partial_game(mode):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = normalize_config(
        {
            "preset": "smoke",
            "games": 3,
            "self_play_mode": mode,
            "inference_cache_mode": "exact",
            "mcts_iterations": 100,
            "mcts_time_limit": 300,
        }
    )
    calls = 0
    completed = []

    def stop():
        nonlocal calls
        calls += 1
        return calls > 14

    with pytest.raises(InterruptedError):
        generate_self_play(
            model, config, seed=3, should_stop=stop, on_game=lambda *game: completed.append(game)
        )
    assert completed == []
    assert not hasattr(model, "inference_cache")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA hardware unavailable")
def test_cuda_exact_dedup_and_mixed_precision():
    model = PolicyValueNet("brandubh", 4, 0).cuda().eval()
    cache, metrics = ExactInferenceCache(), InferenceMetrics(track_cuda=True)
    state = new_game("brandubh")
    result = policy_value_batch(
        [state, state], model, cache=cache, metrics=metrics, mixed_precision=True
    )
    assert result[0] == result[1] and result[0][0] is not result[1][0]
    compare(policy_value_batch([state], model, cache=cache, mixed_precision=True), [result[0]])
    assert metrics.snapshot(finalize_cuda=True)["neural_evaluations"] == 1
