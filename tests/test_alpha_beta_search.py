"""Correctness tests for the optimized alpha-beta search.

The reference value is an independent exhaustive minimax over the real engine,
not a second copy of the optimized search. Symmetry features are exercised on
symmetric, asymmetric and history-broken positions. Neural-backed search must
never share D4-equivalent values or moves.
"""

from dataclasses import replace
import json
import time

import pytest

from hnefatafl import ai, shield_maiden
from hnefatafl.ai import _Bound, _TranspositionTable, _search_settings
from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves, new_game
from hnefatafl.symmetry import (
    Symmetry,
    board_stabilizer,
    canonical_state_key,
    inverse,
    state_stabilizer,
    transform_move,
    transform_state,
)


def minimax(state: State, depth: int, incoming: float | None = None) -> float:
    """Independent exhaustive minimax using the real engine and Shield maiden.

    The leaf value is the normalized Shield maiden child score of the move that
    produced it, matching how Jarl threads the maiden evaluation into alpha-beta.
    """
    if state.winner:
        return ai.evaluate(state)
    if depth == 0:
        return incoming if incoming is not None else shield_maiden.evaluate(state)
    moves = legal_moves(state)
    if not moves:
        return -1.0 if state.turn == "defenders" else 1.0
    context = shield_maiden.DecisionContext(state)
    values = []
    for move in moves:
        child = apply_move(state, move)
        values.append(
            minimax(child, depth - 1, shield_maiden.normalise(context.score(child, move)))
        )
    return max(values) if state.turn == "defenders" else min(values)


def position(ruleset: str, pieces: dict[tuple[int, int], str], turn: str) -> State:
    n = RULESETS[ruleset].size
    board = ["."] * (n * n)
    for (row, col), piece in pieces.items():
        board[row * n + col] = piece
    return State(ruleset, tuple(board), turn)


FEATURE_CONFIGS = {
    "legacy": {"legacy": True},
    "transposition": {"transposition": True, "move_ordering": False},
    "ordering": {"transposition": True, "move_ordering": True},
    "symmetry_transposition": {
        "transposition": True,
        "symmetry_transposition": True,
        "move_ordering": True,
    },
    "symmetry_pruning": {
        "transposition": True,
        "symmetry_pruning": True,
        "move_ordering": True,
    },
    "all": {
        "transposition": True,
        "symmetry_transposition": True,
        "symmetry_pruning": True,
        "move_ordering": True,
    },
}

CORRECTNESS_POSITIONS = [
    ("brandubh", new_game("brandubh"), 2),
    ("tablut", new_game("tablut"), 2),
    ("copenhagen", new_game("copenhagen"), 1),
    (
        "brandubh-sparse",
        position(
            "brandubh",
            {(0, 1): "K", (1, 1): "D", (3, 3): "A", (5, 5): "A", (6, 6): "A"},
            "defenders",
        ),
        3,
    ),
    (
        "tablut-defenders",
        position(
            "tablut",
            {(1, 3): "K", (2, 1): "D", (3, 3): "A", (5, 5): "A", (7, 7): "A"},
            "defenders",
        ),
        2,
    ),
    (
        "copenhagen-sparse",
        position(
            "copenhagen",
            {(0, 3): "K", (1, 1): "D", (3, 3): "A", (5, 5): "A", (9, 9): "A"},
            "defenders",
        ),
        2,
    ),
]


# --- Transposition table unit behavior -------------------------------------


def test_transposition_table_depth_and_bound_preference():
    table = _TranspositionTable(8)
    table.store("a", 2, 0.1, _Bound.EXACT, Move(1, 2))
    table.store("a", 1, 0.9, _Bound.EXACT, None)
    assert table.probe("a").value == 0.1
    assert table.probe("a").best_move == Move(1, 2)
    table.store("a", 3, 0.2, _Bound.LOWER, Move(3, 4))
    assert table.probe("a").depth == 3
    assert table.probe("a").value == 0.2
    assert table.probe("a").bound == _Bound.LOWER


def test_transposition_table_prefers_exact_at_equal_depth():
    table = _TranspositionTable(8)
    table.store("b", 2, 0.3, _Bound.LOWER, None)
    table.store("b", 2, 0.4, _Bound.EXACT, None)
    assert table.probe("b").value == 0.4
    table.store("b", 2, 0.5, _Bound.UPPER, None)
    assert table.probe("b").value == 0.4


def test_transposition_table_capacity_is_bounded():
    table = _TranspositionTable(2)
    for index in range(10):
        table.store(index, 1, float(index), _Bound.EXACT, None)
    assert len(table.entries) == 2
    assert table.evictions == 8
    assert table.peak_entries == 2


def test_search_settings_defaults_validation_and_neural_safety():
    defaults = _search_settings({}, False)
    assert defaults.transposition and defaults.move_ordering
    assert not defaults.symmetry_transposition and not defaults.symmetry_pruning
    with pytest.raises(ValueError, match="object"):
        _search_settings({"search": "legacy"}, False)
    with pytest.raises(ValueError, match="Unsupported"):
        _search_settings({"search": {"unknown": True}}, False)
    with pytest.raises(ValueError, match="boolean"):
        _search_settings({"search": {"move_ordering": "yes"}}, False)
    with pytest.raises(ValueError, match="capacity"):
        _search_settings({"search": {"table_capacity": 0}}, False)
    for feature in ("symmetry_transposition", "symmetry_pruning"):
        with pytest.raises(ValueError, match="neural"):
            _search_settings({"search": {feature: True}}, True)
    # The unsafe combination is refused before any model call.
    with pytest.raises(ValueError, match="neural"):
        ai.choose_move(
            new_game("brandubh"),
            {
                "algorithm": "neural",
                "search_wrapper": "alpha_beta",
                "_model": object(),
                "search": {"symmetry_pruning": True},
            },
        )


# --- Symmetry stabilizers and canonicalization -----------------------------


@pytest.mark.parametrize("ruleset", RULESETS)
def test_openings_are_fully_stabilized(ruleset):
    state = new_game(ruleset)
    assert set(state_stabilizer(state)) == set(Symmetry)
    assert set(board_stabilizer(state.board)) == set(Symmetry)


def test_asymmetric_position_has_trivial_stabilizer():
    state = new_game("brandubh")
    moved = apply_move(state, legal_moves(state)[0])
    assert state_stabilizer(moved) == (Symmetry.IDENTITY,)


def test_symmetric_board_with_asymmetric_history_is_not_prunable():
    state = new_game("brandubh")
    moved = apply_move(state, legal_moves(state)[0])
    # A visually symmetric board with a one-sided history must not be treated
    # as fully symmetric, or equivalent-move pruning would be unsound.
    hybrid = replace(state, history=moved.history, ply=moved.ply)
    assert set(board_stabilizer(hybrid.board)) == set(Symmetry)
    assert len(state_stabilizer(hybrid)) < len(Symmetry)
    assert hybrid.history != state.history


@pytest.mark.parametrize("ruleset", RULESETS)
@pytest.mark.parametrize("seed", [3, 11, 29])
def test_canonical_key_is_transform_invariant(ruleset, seed):
    import random

    state = new_game(ruleset)
    rng = random.Random(seed)
    for _ in range(10):
        if state.winner:
            break
        state = apply_move(state, rng.choice(legal_moves(state)))
    expected = canonical_state_key(state)[0]
    for symmetry in Symmetry:
        assert canonical_state_key(transform_state(state, symmetry))[0] == expected


@pytest.mark.parametrize("ruleset", RULESETS)
def test_static_evaluation_is_d4_invariant(ruleset):
    state = new_game(ruleset)
    moved = apply_move(state, legal_moves(state)[0])
    expected = ai.evaluate(moved)
    for symmetry in Symmetry:
        assert ai.evaluate(transform_state(moved, symmetry)) == pytest.approx(expected)


@pytest.mark.parametrize("ruleset", RULESETS)
def test_move_inverse_recovers_original_and_maps_back_legally(ruleset):
    state = new_game(ruleset)
    move = legal_moves(state)[0]
    for symmetry in Symmetry:
        transformed = transform_move(move, state.size, symmetry)
        assert transform_move(transformed, state.size, inverse(symmetry)) == move
        assert transformed in legal_moves(transform_state(state, symmetry))


# --- Minimax preservation ---------------------------------------------------


@pytest.mark.parametrize("label,state,depth", CORRECTNESS_POSITIONS)
@pytest.mark.parametrize("feature", list(FEATURE_CONFIGS))
def test_search_preserves_reference_minimax(label, state, depth, feature):
    expected = minimax(state, depth)
    diagnostics: dict = {}
    move = ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": depth,
            "time_limit": 120,
            "seed": 5,
            "search": FEATURE_CONFIGS[feature],
        },
        diagnostics=diagnostics,
    )
    assert move in legal_moves(state)
    assert diagnostics["completed_depth"] == depth
    assert diagnostics["value"] == pytest.approx(expected, abs=1e-9)
    # The chosen move must itself be optimal for the mover under the same evaluator.
    context = shield_maiden.DecisionContext(state)
    chosen = apply_move(state, move)
    assert minimax(
        chosen, depth - 1, shield_maiden.normalise(context.score(chosen, move))
    ) == pytest.approx(expected, abs=1e-9)


def test_symmetry_pruning_actually_skips_equivalent_moves():
    state = new_game("tablut")
    diagnostics: dict = {}
    ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 2,
            "time_limit": 60,
            "seed": 5,
            "search": {"transposition": True, "symmetry_pruning": True, "move_ordering": True},
        },
        diagnostics=diagnostics,
    )
    assert diagnostics["symmetry_moves_skipped"] > 0


def test_symmetry_transposition_reduces_nodes_on_openings():
    state = new_game("brandubh")
    plain: dict = {}
    symmetric: dict = {}
    base = {"algorithm": "alpha_beta", "depth": 3, "time_limit": 60, "seed": 5}
    ai.choose_move(
        state, {**base, "search": {"transposition": True, "move_ordering": True}}, diagnostics=plain
    )
    ai.choose_move(
        state,
        {
            **base,
            "search": {
                "transposition": True,
                "symmetry_transposition": True,
                "move_ordering": True,
            },
        },
        diagnostics=symmetric,
    )
    assert symmetric["nodes"] < plain["nodes"]
    assert symmetric["symmetry_hits"] > 0
    assert plain["value"] == pytest.approx(symmetric["value"])


# --- Search safety ----------------------------------------------------------


def test_seeded_tie_breaking_is_reproducible():
    state = new_game("brandubh")
    for feature in ("ordering", "all"):
        config = {
            "algorithm": "alpha_beta",
            "depth": 2,
            "time_limit": 60,
            "seed": 17,
            "search": FEATURE_CONFIGS[feature],
        }
        assert ai.choose_move(state, config) == ai.choose_move(state, config)


def test_short_deadline_returns_legal_fallback_and_reports_last_depth():
    state = new_game("copenhagen")
    diagnostics: dict = {}
    move = ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 8,
            "time_limit": 0.02,
            "search": FEATURE_CONFIGS["all"],
        },
        diagnostics=diagnostics,
    )
    assert move in legal_moves(state)
    assert 0 <= diagnostics["completed_depth"] <= 8


def test_cancellation_propagates_without_publishing_a_result():
    state = new_game("brandubh")
    calls = {"count": 0}

    def should_stop() -> bool:
        calls["count"] += 1
        return calls["count"] > 5

    with pytest.raises(InterruptedError):
        ai.choose_move(
            state,
            {"algorithm": "alpha_beta", "depth": 5, "time_limit": 60},
            should_stop=should_stop,
        )


def test_interrupted_iteration_keeps_last_completed_value(monkeypatch):
    state = new_game("brandubh")
    config = {
        "algorithm": "alpha_beta",
        "depth": 1,
        "time_limit": 60,
        "seed": 9,
        "search": FEATURE_CONFIGS["all"],
    }
    expected = ai.analyze_position(state, config)
    ordered = ai._ordered
    root_calls = 0

    def expire_on_second_root(position, moves, rng, maiden=False, check=None):
        nonlocal root_calls
        if position is state:
            root_calls += 1
            if root_calls == 2:
                raise ai._Deadline
        return ordered(position, moves, rng, maiden, check)

    monkeypatch.setattr(ai, "_ordered", expire_on_second_root)
    result = ai.analyze_position(state, {**config, "depth": 4})
    assert root_calls == 2
    assert result == expected


# --- Neural safety and integration -----------------------------------------


def test_neural_alpha_beta_uses_ordinary_table_without_symmetry(monkeypatch):
    state = new_game("brandubh")
    chosen = legal_moves(state)[-1]
    expected_board = apply_move(state, chosen).board

    def policy_value(position, *_args):
        return {}, -0.7 if position.board == expected_board else 0.6

    monkeypatch.setattr(ai, "policy_value", policy_value)
    config = {
        "algorithm": "neural",
        "search_wrapper": "alpha_beta",
        "_model": object(),
        "depth": 1,
        "time_limit": 60,
        "search": {"transposition": True, "move_ordering": True},
    }
    report = ai.analyze_position(state, config)
    assert report["best_move"] == chosen.to_dict()
    # Interior nodes of a two-ply search exercise the ordinary table; a one-ply
    # search reaches leaves before any probe, so use depth two for the counter.
    deeper = {**config, "depth": 2}
    diagnostics: dict = {}
    ai.choose_move(state, deeper, diagnostics=diagnostics)
    assert diagnostics["transposition_lookups"] > 0
    assert diagnostics["symmetry_moves_skipped"] == 0


@pytest.mark.parametrize("ruleset", RULESETS)
def test_jarl_chooses_legal_moves_with_all_optimizations(ruleset):
    state = new_game(ruleset)
    move = ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 2,
            "time_limit": 5,
            "seed": 4,
            "search": FEATURE_CONFIGS["all"],
        },
    )
    assert move in legal_moves(state)


def test_alpha_beta_benchmark_cli_dispatches_config_and_output(tmp_path, monkeypatch):
    from hnefatafl import alpha_beta_benchmark, cli

    captured: dict = {}

    def fake(config, should_stop=None, report=None):
        captured["config"] = config
        return {"status": "ok"}

    monkeypatch.setattr(alpha_beta_benchmark, "run_alpha_beta_benchmark", fake)
    config = tmp_path / "ab.json"
    config.write_text('{"repetitions": 1}', encoding="utf-8")
    output = tmp_path / "out" / "ab.json"
    assert cli.main(["benchmark-alpha-beta", "--config", str(config), "--output", str(output)]) == 0
    assert captured["config"] == {"repetitions": 1}
    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "ok"}


def test_benchmark_corpus_is_deterministic_and_oracle_matches_minimax():
    from hnefatafl.alpha_beta_benchmark import corpus, oracle_value

    first = corpus(123)
    second = corpus(123)
    assert [(label, state) for label, state in first] == [(label, state) for label, state in second]
    assert {state.ruleset for _, state in first} == set(RULESETS)
    assert all(not state.winner and legal_moves(state) for _, state in first)
    for _, state in first[:4]:
        value = oracle_value(state, 2, 200_000)
        if value is not None:
            assert value == pytest.approx(minimax(state, 2), abs=1e-9)


def test_analysis_exposes_search_counters():
    state = new_game("brandubh")
    report = ai.analyze_position(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 2,
            "time_limit": 60,
            "search": FEATURE_CONFIGS["all"],
        },
    )
    assert report["completed_depth"] == 2
    assert report["nodes"] > 0
    assert report["evaluations"] > 0
    assert report["transposition_hits"] >= 0
    assert report["symmetry_moves_skipped"] >= 0


# --- Improved move ordering and precise symmetry statistics ----------------


def test_prioritise_consults_history_without_a_preferred_move():
    state = new_game("brandubh")
    moves = legal_moves(state)
    children = [(move, apply_move(state, move)) for move in moves]
    promoted = moves[-1]
    ordered = ai._prioritise(
        children,
        tt_move=None,
        pv_move=None,
        killers=(),
        history={promoted: 7},
    )
    assert ordered[0][0] == promoted
    # A zero history must not reorder or copy the base list unnecessarily.
    untouched = ai._prioritise(children, tt_move=None, pv_move=None, killers=(), history={})
    assert [item[0] for item in untouched] == moves


def test_prioritise_orders_pv_then_table_then_killer_then_history():
    state = new_game("brandubh")
    moves = legal_moves(state)
    children = [(move, apply_move(state, move)) for move in moves]
    pv, table, killer, hot = moves[0], moves[1], moves[2], moves[3]
    ordered = ai._prioritise(
        children,
        tt_move=table,
        pv_move=pv,
        killers=(killer,),
        history={hot: 9},
    )
    assert [item[0] for item in ordered[:4]] == [pv, table, killer, hot]


def test_clean_search_config_normalises_and_rejects_malformed_values():
    from hnefatafl.ai import SEARCH_SETTINGS_DEFAULTS, clean_search_config

    assert clean_search_config(None) == SEARCH_SETTINGS_DEFAULTS
    # A canonical table is also an ordinary table.
    assert (
        clean_search_config({"symmetry_transposition": True, "transposition": False})[
            "transposition"
        ]
        is True
    )
    # Legacy collapses every optimized flag to one deterministic shape.
    legacy = clean_search_config({"legacy": True, "symmetry_pruning": True})
    assert legacy == {**SEARCH_SETTINGS_DEFAULTS, "legacy": True}
    for malformed in (
        {"unknown": True},
        {"transposition": 1},
        {"table_capacity": 0},
        {"table_capacity": True},
    ):
        with pytest.raises(ValueError):
            clean_search_config(malformed)
    for feature in ("symmetry_transposition", "symmetry_pruning"):
        with pytest.raises(ValueError, match="neural"):
            clean_search_config({feature: True}, model_backed=True)


def test_cross_orientation_hits_are_distinct_from_canonical_hits():
    state = new_game("brandubh")
    details: dict = {}
    ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 3,
            "time_limit": 60,
            "seed": 5,
            "search": {
                "transposition": True,
                "symmetry_transposition": True,
                "move_ordering": True,
            },
        },
        diagnostics=details,
    )
    assert details["symmetry_hits"] > 0
    assert 0 < details["transposition_cross_orientation_hits"] <= details["symmetry_hits"]
    # Ordinary tables never claim cross-orientation reuse.
    plain: dict = {}
    ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 3,
            "time_limit": 60,
            "seed": 5,
            "search": {"transposition": True, "move_ordering": True},
        },
        diagnostics=plain,
    )
    assert plain.get("transposition_cross_orientation_hits", 0) == 0


def long_history_state(ruleset: str = "copenhagen", plies: int = 400) -> State:
    """A fully symmetric board with a long, valid repetition history.

    Every D4 orientation is tied, so canonicalization and the stabilizer must
    transform the whole history for each symmetry instead of returning early.
    """
    state = new_game(ruleset)
    entry = f"{state.turn}:{''.join(state.board)}"
    return replace(state, history=tuple(entry for _ in range(plies)), ply=plies)


def test_symmetry_functions_invoke_the_cancellation_check():
    state = long_history_state()

    class Stop(Exception):
        pass

    calls = {"count": 0}

    def check() -> None:
        calls["count"] += 1
        if calls["count"] > 3:
            raise Stop

    for operation in (canonical_state_key, state_stabilizer, board_stabilizer):
        calls["count"] = 0
        with pytest.raises(Stop):
            operation(state if operation is not board_stabilizer else state.board, check)
        assert calls["count"] > 0


def test_tiny_deadline_interrupts_long_history_canonicalization():
    state = long_history_state()
    # A host-calibrated baseline for the uninterrupted stabilizer.
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        state_stabilizer(state)
        samples.append(time.perf_counter() - started)
    stabilizer_seconds = sorted(samples)[1]
    details: dict = {}
    started = time.perf_counter()
    move = ai.choose_move(
        state,
        {
            "algorithm": "alpha_beta",
            "depth": 8,
            "time_limit": stabilizer_seconds / 8,
            "seed": 3,
            "search": {
                "transposition": True,
                "symmetry_transposition": True,
                "symmetry_pruning": True,
                "move_ordering": True,
            },
        },
        diagnostics=details,
    )
    elapsed = time.perf_counter() - started
    assert move in legal_moves(state)
    assert details["completed_depth"] < 8
    # The deadline must interrupt the stabilizer/canonicalization instead of
    # running the whole computation to completion.
    assert elapsed < stabilizer_seconds


def test_table_capacity_maximum_is_bounded():
    from hnefatafl.ai import MAX_TABLE_CAPACITY, clean_search_config

    assert MAX_TABLE_CAPACITY == 500_000
    assert (
        clean_search_config({"table_capacity": MAX_TABLE_CAPACITY})["table_capacity"]
        == MAX_TABLE_CAPACITY
    )
    with pytest.raises(ValueError, match="capacity"):
        clean_search_config({"table_capacity": MAX_TABLE_CAPACITY + 1})
    with pytest.raises(ValueError, match="capacity"):
        _TranspositionTable(MAX_TABLE_CAPACITY + 1)
