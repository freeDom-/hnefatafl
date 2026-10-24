"""Frozen Phase 1 search evidence, node lifecycle and rules-aware traversal."""

from dataclasses import replace
import gc
import hashlib
import json
import math
from pathlib import Path
import random
import weakref

import pytest

from hnefatafl import ai
from hnefatafl.engine import Move, RULESETS, State, apply_move, legal_moves, new_game


def prediction(state):
    weights = {move: (move.source + move.target) % 7 + 1 for move in legal_moves(state)}
    total = sum(weights.values())
    return {move: value / total for move, value in weights.items()}, ai.evaluate(state)


def tree_digest(root):
    rows = []

    def visit(node, path):
        rows.append([path, node.visits, node.total, node.prior])
        for move, child in node.children.items():
            visit(child, path + [[move.source, move.target]])

    visit(root, [])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


REFERENCE = json.loads((Path(__file__).parent / "fixtures/mcts_reference.json").read_text())


@pytest.mark.parametrize("case", REFERENCE["cases"])
def test_frozen_reference_preserves_every_edge_visit_value_prior_and_rng(case, monkeypatch):
    factory = ai._Node
    nodes = []

    def capture(*args, **kwargs):
        node = factory(*args, **kwargs)
        nodes.append(node)
        return node

    monkeypatch.setattr(ai, "_Node", capture)
    config = dict(
        iterations=case["budget"],
        seed=42,
        time_limit=300,
        rollout_depth=2,
        dirichlet_fraction=case["noise"],
    )
    if case["neural"]:
        config["_model"] = object()
    details = {}
    context = ai.mcts_context(new_game(case["rule"]), config, diagnostics=details)
    requested = []
    policies = []
    try:
        pending = next(context)
        while True:
            assert not pending.winner
            requested.append(pending)
            result = prediction(pending)
            policies.append((result[0], result[0].copy()))
            pending = context.send(result)
    except StopIteration as done:
        policy = done.value
    assert [[m.source, m.target, v] for m, v in policy.items()] == case["policy"]
    assert tree_digest(nodes[0]) == case["tree_sha256"]
    assert details["value"] == case["value"]
    assert details["simulations"] == case["budget"] == nodes[0].visits
    assert sum(c.visits for c in nodes[0].children.values()) == case["budget"]
    assert all(actual == original for actual, original in policies)
    if case["neural"]:
        assert len({id(state) for state in requested}) == len(requested)
        assert details["mcts_neural_requests"] == len(requested)
        assert len(requested) + details["mcts_node_prediction_reuses"] == case["requests"]
        assert len(requested) == len([n for n in nodes if not n.state.winner])
        if case["budget"] > 1:
            assert details["mcts_node_prediction_reuses"] > 0
    else:
        assert details["mcts_neural_requests"] == details["mcts_node_prediction_reuses"] == 0


@pytest.mark.parametrize("turn", ["attackers", "defenders"])
def test_puct_matches_reference_arithmetic_and_first_tie(turn):
    rng = random.Random(42)
    parent = ai._Node(replace(new_game("brandubh"), turn=turn))
    moves = legal_moves(parent.state)
    for _ in range(100):
        parent.visits = rng.randrange(1, 100000)
        parent.children = {
            move: ai._Node(
                parent.state,
                prior=rng.random(),
                visits=rng.randrange(1, 1000),
                total=rng.uniform(-30, 30),
            )
            for move in moves[:16]
        }
        exploration = rng.uniform(0, 20)
        sign = 1 if turn == "defenders" else -1
        expected = max(
            parent.children.values(),
            key=lambda child: (
                sign * child.total / max(1, child.visits)
                + exploration * child.prior * math.sqrt(parent.visits + 1) / (1 + child.visits)
            ),
        )
        assert ai._select_child(parent, exploration) is expected
    for child in parent.children.values():
        child.prior, child.visits, child.total = 1, 1, 0
    assert ai._select_child(parent, 0) is next(iter(parent.children.values()))


def test_integer_widening_is_exact_at_all_supported_boundaries():
    for visits in range(100001):
        boundary = math.ceil(math.sqrt(visits + 1))
        for count in (boundary - 1, boundary, boundary + 1):
            assert (count < boundary) == (count * count <= visits)


@pytest.mark.parametrize("rule", RULESETS)
def test_history_dependent_terminal_child_never_enters_inference(rule):
    state = replace(new_game(rule), turn="defenders")
    move = next(m for m in legal_moves(state) if apply_move(state, m).winner is None)
    child = apply_move(state, move)
    repeated = replace(state, history=(child.history[-1], child.history[-1], state.history[-1]))
    terminal = apply_move(repeated, move)
    assert terminal.reason == "threefold_repetition"
    assert terminal.winner == ("attackers" if rule == "copenhagen" else "draw")
    details = {}
    context = ai.mcts_context(
        repeated, dict(iterations=8, time_limit=300, _model=object()), diagnostics=details
    )
    assert next(context) is repeated
    with pytest.raises(StopIteration) as done:
        context.send(({move: 1.0}, 0.75))
    assert done.value.value == {move: 1.0}
    assert details["simulations"] == 8
    assert details["value"] == (-1 if rule == "copenhagen" else 0)
    assert details["mcts_neural_requests"] == 1
    # Same layout, fresh history: this independent node must obtain its value.
    fresh = ai.mcts_context(state, dict(iterations=1, time_limit=300, _model=object()))
    assert next(fresh) is state
    pending = fresh.send(({move: 1.0}, 0.75))
    assert pending.board == child.board and pending.winner is None
    fresh.close()


def position(rule, pieces, turn="attackers"):
    n = RULESETS[rule].size
    board = ["."] * (n * n)
    for (r, c), piece in pieces.items():
        board[r * n + c] = piece
    return State(rule, tuple(board), turn=turn)


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("kind", ["escape", "capture"])
def test_tactical_shortcuts_keep_variant_king_rules(rule, kind):
    n = RULESETS[rule].size
    c = n // 2
    if kind == "escape":
        state = position(
            rule, {(0, 1): "K", (c, c): "D", (2, 2): "A", (n - 1, n - 1): "A"}, "defenders"
        )
        if rule == "tablut":
            state = position(rule, {(1, 1): "K", (c, c): "D", (n - 1, n - 1): "A"}, "defenders")
    elif rule == "copenhagen":
        state = position(rule, {(5, 5): "K", (4, 5): "A", (5, 4): "A", (5, 6): "A", (8, 5): "A"})
    else:
        state = position(rule, {(c, c): "K", (c, c - 1): "A", (c + 2, c + 1): "A"})
    details = {}
    context = ai.mcts_context(state, dict(iterations=1, _model=object()), diagnostics=details)
    assert next(context) is state
    with pytest.raises(StopIteration) as done:
        context.send(prediction(state))
    policy = done.value.value
    assert len(policy) == 1
    assert apply_move(state, next(iter(policy))).winner == state.turn
    assert details["simulations"] == 0 and details["immediate_win_shortcuts"] == 1
    assert details["mcts_neural_requests"] == 1


def test_copenhagen_shieldwall_is_applied_lazily_with_full_history():
    state = position(
        "copenhagen",
        {
            (0, 3): "D",
            (0, 4): "D",
            (0, 5): "D",
            (0, 6): "A",
            (1, 3): "A",
            (1, 4): "A",
            (1, 5): "A",
            (3, 2): "A",
            (5, 5): "K",
            (9, 9): "D",
        },
    )
    move = Move(35, 2)
    expected = apply_move(state, move)
    assert expected.board.count("D") == 1 and not expected.winner
    context = ai.mcts_context(state, dict(iterations=1, _model=object(), time_limit=300))
    assert next(context) is state
    child = context.send(({move: 1.0}, 0.0))
    assert child == expected
    with pytest.raises(StopIteration):
        context.send(prediction(child))


def test_cancellation_of_pending_leaf_does_not_backup_and_close_releases_tree(monkeypatch):
    nodes = []
    factory = ai._Node

    def capture(*args, **kwargs):
        node = factory(*args, **kwargs)
        nodes.append(node)
        return node

    monkeypatch.setattr(ai, "_Node", capture)
    stopped = False
    state = new_game("brandubh")
    details = {}
    context = ai.mcts_context(
        state,
        dict(iterations=16, _model=object()),
        diagnostics=details,
        should_stop=lambda: stopped,
    )
    assert next(context) is state
    pending = context.send(prediction(state))
    assert len(nodes) == 2 and all(n.visits == 0 and n.total == 0 for n in nodes)
    reference = weakref.ref(pending)
    stopped = True
    with pytest.raises(InterruptedError):
        context.send(prediction(pending))
    assert details["simulations"] == 0
    assert all(n.visits == 0 and n.total == 0 for n in nodes)
    nodes.clear()
    del pending
    context.close()
    gc.collect()
    assert reference() is None


def test_root_scan_and_traversal_cancellation_are_responsive():
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        return calls > 6

    context = ai.mcts_context(new_game("copenhagen"), should_stop=stop)
    pending = next(context)
    with pytest.raises(InterruptedError):
        context.send(prediction(pending))


@pytest.mark.parametrize("budget", [0, -1, 100001])
def test_invalid_budgets_keep_validation(budget):
    with pytest.raises(ValueError, match="iterations"):
        next(ai.mcts_context(new_game("brandubh"), {"iterations": budget}))


def test_pending_inference_deadline_still_completes_first_simulation(monkeypatch):
    now = 0.0
    monkeypatch.setattr(ai.time, "monotonic", lambda: now)
    state = new_game("brandubh")
    details = {}
    context = ai.mcts_context(
        state, dict(iterations=128, time_limit=1, _model=object()), diagnostics=details
    )
    assert next(context) is state
    now = 2.0
    pending = context.send(prediction(state))
    assert details["simulations"] == 0
    with pytest.raises(StopIteration) as done:
        context.send(prediction(pending))
    assert details["simulations"] == 1 and details["deadline_cutoffs"] == 1
    assert set(done.value.value) <= set(legal_moves(state))
    assert sum(done.value.value.values()) == 1


@pytest.mark.parametrize("neural", [False, True])
def test_large_budget_finishes_without_losing_simulations(neural):
    state = new_game("brandubh")
    config = dict(iterations=512, time_limit=300, rollout_depth=0, seed=19)
    if neural:
        config["_model"] = object()
    details = {}
    context = ai.mcts_context(state, config, diagnostics=details)
    try:
        pending = next(context)
        while True:
            pending = context.send(prediction(pending))
    except StopIteration as done:
        assert sum(done.value.values()) == pytest.approx(1)
    assert details["simulations"] == 512
    assert details.get("deadline_cutoffs", 0) == 0


def test_retained_predictions_are_owned_even_if_caller_reuses_its_dictionary():
    case = next(c for c in REFERENCE["cases"] if c["rule"] == "copenhagen" and c["budget"] == 128)
    config = dict(
        _model=object(),
        iterations=128,
        seed=42,
        time_limit=300,
        rollout_depth=2,
        dirichlet_fraction=0.25,
    )
    context = ai.mcts_context(new_game("copenhagen"), config)
    pending = next(context)
    try:
        while True:
            priors, value = prediction(pending)
            pending = context.send((priors, value))
            priors.clear()
    except StopIteration as done:
        assert [[m.source, m.target, p] for m, p in done.value.items()] == case["policy"]
