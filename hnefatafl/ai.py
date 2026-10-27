"""Bounded, reproducible computer players independent of HTTP and persistence.

All evaluations use the defender's perspective. Search never mutates a state.
The neural evaluator is optional: ordinary play does not import PyTorch.
"""

from __future__ import annotations

import math
import random
import time
from array import array
from dataclasses import dataclass, field
from enum import IntEnum
from functools import lru_cache
from itertools import combinations
from typing import Any, Callable, Generator

from .engine import Move, State, apply_move, legal_moves, new_game
from .symmetry import Symmetry, canonical_state_key, inverse, state_stabilizer, transform_move


def ai_profiles() -> list[dict[str, Any]]:
    common = ["tablut", "brandubh", "copenhagen"]
    return [
        {
            "id": "baseline",
            "name": "Shield bearer",
            "algorithm": "baseline",
            "difficulty": "Beginner",
            "parameters": {"time_limit": 1.0},
            "supported_rulesets": common,
        },
        {
            "id": "shield_maiden",
            "name": "Shield maiden",
            "description": "Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.",
            "algorithm": "shield_maiden",
            "difficulty": "Intermediate",
            "parameters": {"time_limit": 1.0, "heuristic_version": 1},
            "supported_rulesets": common,
        },
        {
            "id": "alpha_beta",
            "name": "Jarl",
            "algorithm": "alpha_beta",
            "difficulty": "Advanced",
            "parameters": {"depth": 5, "time_limit": 2.0},
            "supported_rulesets": common,
        },
        {
            "id": "mcts",
            "name": "Seer",
            "algorithm": "mcts",
            "difficulty": "Advanced",
            "parameters": {
                # The Seer preset is ruleset-independent. The 2-second ceiling
                # bounds search, with 5,000 visits as the upper cap. Per-ruleset
                # recommended plans (128/192/256 visits) are available in
                # training jobs.
                "iterations": 5000,
                "time_limit": 2.0,
                "exploration": 1.4,
                "temperature": 0.0,
                "rollout_depth": 8,
            },
            "supported_rulesets": common,
        },
        {
            "id": "neural",
            "name": "Trained model",
            "algorithm": "neural",
            "difficulty": "Model dependent",
            "parameters": {"iterations": 40, "time_limit": 10.0},
            "supported_rulesets": common,
        },
    ]


def evaluate(state: State) -> float:
    """Rules-aware static score in [-1, 1], positive for defenders."""
    if state.winner:
        return {"defenders": 1.0, "attackers": -1.0, "draw": 0.0}[state.winner]
    n = state.size
    try:
        king = state.board.index("K")
    except ValueError:
        return -1.0
    row, col = divmod(king, n)
    attackers = state.board.count("A")
    defenders = state.board.count("D")
    initial_attackers = {"brandubh": 8, "tablut": 16, "copenhagen": 24}[state.ruleset]
    # Escaping to an edge is valuable in Tablut; the other variants use corners.
    distance = (
        min(row, col, n - 1 - row, n - 1 - col)
        if state.ruleset == "tablut"
        else min(row + col, row + n - 1 - col, n - 1 - row + col, 2 * (n - 1) - row - col)
    )
    escape = 1.0 - distance / (n - 1)
    adjacent_attackers = 0
    free_rays = 0
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = row + dr, col + dc
        if 0 <= rr < n and 0 <= cc < n and state.board[rr * n + cc] == "A":
            adjacent_attackers += 1
        ray_clear = True
        while 0 <= rr < n and 0 <= cc < n:
            if state.board[rr * n + cc] != ".":
                ray_clear = False
                break
            rr += dr
            cc += dc
        free_rays += int(ray_clear)
    material = (defenders * 2 - attackers) / initial_attackers
    return math.tanh(
        0.9 * material + 0.9 * escape - 0.45 * adjacent_attackers + 0.12 * free_rays - 0.45
    )


@lru_cache(maxsize=8)
def _loaded_model(path: str):
    from .models import load_checkpoint

    return load_checkpoint(path, device="cpu")[0]


def policy_value(
    state: State, model_path: str | None = None, model=None
) -> tuple[dict[Move, float], float]:
    """Return legal priors and a defender-perspective value."""
    moves = legal_moves(state)
    if not moves:
        return {}, evaluate(state)
    if model_path is None and model is None:
        return {move: 1 / len(moves) for move in moves}, evaluate(state)
    import torch
    from .models import encode_state

    model = model if model is not None else _loaded_model(str(model_path))
    if model.ruleset != state.ruleset:
        raise ValueError("Model ruleset does not match this game")
    with torch.inference_mode():
        logits, value = model(encode_state(state).unsqueeze(0).to(next(model.parameters()).device))
        action_ids = [m.source * (state.size**2) + m.target for m in moves]
        probabilities = torch.softmax(logits[0, action_ids], dim=0).tolist()
    return dict(zip(moves, probabilities)), float(value.reshape(-1)[0])


_HEURISTICS = ("shield_bearer", "shield_maiden")


def _heuristic_name(config: dict) -> str:
    """Resolve the model-free evaluator, defaulting per algorithm.

    Jarl (alpha-beta) and Seer (MCTS) default to the Shield maiden heuristic.
    The standalone Shield bearer baseline keeps the generic static evaluation.
    An explicit ``heuristic`` value is preserved for testing and evaluation;
    only alpha-beta and MCTS consume it.
    """
    algorithm = config.get("algorithm", config.get("profile_id", "baseline"))
    name = config.get("heuristic")
    if name is None:
        return (
            "shield_maiden" if algorithm in ("alpha_beta", "alphabeta", "mcts") else "shield_bearer"
        )
    if name not in _HEURISTICS:
        raise ValueError("Unsupported search heuristic")
    return str(name)


def _heuristic_evaluator(name: str) -> Callable[[State], float]:
    """Return the model-free leaf evaluator for a resolved heuristic name."""
    if name == "shield_maiden":
        from .shield_maiden import evaluate as shield_maiden_evaluate

        return shield_maiden_evaluate
    return evaluate


def _ordered(
    state: State,
    moves: list[Move],
    rng: random.Random,
    maiden: bool = False,
    check: Callable[[], None] | None = None,
) -> list[tuple[Move, State, float]]:
    """Order children by a defender-perspective heuristic value.

    Returns ``(move, child, value)`` triples, where ``value`` is also the
    Shield maiden leaf value threaded into the search. ``maiden=True`` reuses
    the Shield maiden child scoring with one decision context per node;
    otherwise the generic Shield bearer static evaluation is used. Terminal
    results and material/king progression sort first with stable seeded ties.
    """
    rng.shuffle(moves)
    sign = 1 if state.turn == "defenders" else -1
    children: list[tuple[Move, State]] = []
    for move in moves:
        if check is not None:
            check()
        children.append((move, apply_move(state, move)))
    if maiden:
        from .shield_maiden import DecisionContext, normalise

        context = DecisionContext(state)
        scored = [(move, child, normalise(context.score(child, move))) for move, child in children]
    else:
        scored = [(move, child, evaluate(child)) for move, child in children]

    def order_key(item: tuple[Move, State, float]) -> float:
        if check is not None:
            check()
        return sign * item[2]

    scored.sort(key=order_key, reverse=True)
    return scored


class _Deadline(Exception):
    pass


class _Bound(IntEnum):
    EXACT = 0
    LOWER = 1
    UPPER = 2


def _bound_for(value: float, alpha: float, beta: float) -> _Bound:
    """Classify a fail-soft result against the window that was actually searched.

    ``alpha`` and ``beta`` must be the *effective* window after any
    transposition-table tightening. Classifying against a stale, wider alpha
    would store a fail-low upper bound as an exact value: with a lower-bound
    entry raising alpha from 0 to 5 and a fail-low result of 3, the node knows
    only ``value <= 3``, yet ``3 <= 0`` is false and ``3 >= beta`` may be false,
    so the unsafe classifier would publish an exact 3.

    Fail-soft alpha-beta guarantees, for the window ``(alpha, beta)``:

    * ``value <= alpha`` means the true value is at most ``value`` (upper bound),
    * ``value >= beta`` means the true value is at least ``value`` (lower bound),
    * otherwise the true value equals ``value`` (exact).
    """
    if value <= alpha:
        return _Bound.UPPER
    if value >= beta:
        return _Bound.LOWER
    return _Bound.EXACT


def _tt_tighten(
    entry: _TTEntry, remaining: int, alpha: float, beta: float
) -> tuple[float, float, _Bound | None]:
    """Apply a depth-compatible transposition entry to the search window.

    Returns ``(alpha, beta, cutoff_bound)``. When ``cutoff_bound`` is not
    ``None`` the entry proves a cutoff and the caller must return
    ``entry.value``; the bound is ``EXACT`` for an exact entry and ``LOWER`` or
    ``UPPER`` for a bound cutoff. Otherwise the returned window is the effective
    one that was actually searched and that :func:`_bound_for` must classify
    against. An entry shallower than ``remaining`` never narrows the window.
    """
    if entry.depth < remaining:
        return alpha, beta, None
    if entry.bound == _Bound.EXACT:
        return alpha, beta, _Bound.EXACT
    if entry.bound == _Bound.LOWER:
        if entry.value >= beta:
            return alpha, beta, _Bound.LOWER
        return max(alpha, entry.value), beta, None
    # UPPER: a value at or below alpha proves a fail-low cutoff; otherwise it
    # only lowers beta.
    if entry.value <= alpha:
        return alpha, beta, _Bound.UPPER
    return alpha, min(beta, entry.value), None


@dataclass(slots=True)
class _TTEntry:
    depth: int
    value: float
    bound: _Bound
    best_move: Move | None
    generation: int
    # Canonical orientation used when the key was stored. ``None`` for ordinary
    # (non-symmetry) keys. A probe in a different orientation is a genuine
    # cross-orientation reuse rather than a same-orientation table hit.
    orientation: int | None = None


def _repetition_signature(history: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Order-independent occurrence counts of a position history.

    The engine's threefold rule and the Shield maiden repetition penalty depend
    only on how many times each board/turn key has occurred, never on the order
    of those occurrences. Replacing the ordered history with this compact
    signature therefore preserves every future outcome and evaluation while
    letting move-order transpositions share one table entry. Two histories with
    different counts (for example a position seen once versus twice) keep
    distinct keys, so repetition-sensitive states never alias.
    """
    counts: dict[str, int] = {}
    for key in history:
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items()))


@lru_cache(maxsize=8192)
def _cached_repetition_signature(history: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    """Cache the signature for histories revisited across deepening passes."""
    return _repetition_signature(history)


# Per-search transposition-table ceiling. A single search owns one table and
# discards it afterwards, but a repetition-aware key can be several kilobytes on
# a long game, so the capacity is bounded to keep worst-case memory predictable.
# The measured per-entry cost and the long-game peak are recorded in
# docs/benchmarks/alpha-beta-controls.md.
MAX_TABLE_CAPACITY = 500_000


class _TranspositionTable:
    """Depth-aware exact/bounded table for exactly one alpha-beta search.

    A table is never shared across games, evaluators, rulesets or neural models.
    Keys carry the full repetition-aware state identity (optionally D4-canonical),
    so identical boards with different histories cannot alias. Replacement prefers
    deeper entries and, among equal depths, exact bounds; a bounded capacity keeps
    memory predictable with FIFO eviction.
    """

    def __init__(self, capacity: int = 100_000) -> None:
        if type(capacity) is not int or not 1 <= capacity <= MAX_TABLE_CAPACITY:
            raise ValueError(
                f"Transposition table capacity must be between 1 and {MAX_TABLE_CAPACITY}"
            )
        self.capacity = capacity
        self.entries: dict[object, _TTEntry] = {}
        self.generation = 0
        self.lookups = 0
        self.hits = 0
        self.exact_hits = 0
        self.bound_hits = 0
        self.cutoffs = 0
        self.stores = 0
        self.replacements = 0
        self.evictions = 0
        self.peak_entries = 0

    def probe(self, key: object) -> _TTEntry | None:
        self.lookups += 1
        entry = self.entries.get(key)
        if entry is not None:
            self.hits += 1
        return entry

    def store(
        self,
        key: object,
        depth: int,
        value: float,
        bound: _Bound,
        best_move: Move | None,
        orientation: int | None = None,
    ) -> None:
        existing = self.entries.get(key)
        if existing is not None:
            if existing.generation == self.generation:
                if existing.depth > depth:
                    return
                if (
                    existing.depth == depth
                    and existing.bound == _Bound.EXACT
                    and bound != _Bound.EXACT
                ):
                    return
            self.replacements += 1
        elif len(self.entries) >= self.capacity:
            self.entries.pop(next(iter(self.entries)))
            self.evictions += 1
        self.entries[key] = _TTEntry(depth, value, bound, best_move, self.generation, orientation)
        self.stores += 1
        if len(self.entries) > self.peak_entries:
            self.peak_entries = len(self.entries)


# The single canonical alpha-beta search configuration. API, tournament and
# frontend validation all resolve to this shape so identity and behaviour cannot
# drift apart. ``legacy`` selects the original exact-value cache search and
# overrides every optimized flag.
SEARCH_SETTINGS_DEFAULTS: dict[str, object] = {
    "legacy": False,
    "transposition": True,
    "move_ordering": True,
    "symmetry_transposition": False,
    "symmetry_pruning": False,
    "table_capacity": 100_000,
}

_SEARCH_BOOLEAN_FLAGS = (
    "legacy",
    "transposition",
    "move_ordering",
    "symmetry_transposition",
    "symmetry_pruning",
)


def clean_search_config(raw: object, *, model_backed: bool = False) -> dict[str, object]:
    """Validate and normalise a nested alpha-beta ``search`` configuration.

    Unknown keys and malformed values raise ``ValueError`` so callers can reject
    a request atomically. Booleans must be real booleans and the table capacity a
    bounded positive integer. ``symmetry_transposition`` implies an ordinary
    table, and ``legacy`` collapses every optimized flag to its deterministic
    default. Neural-backed search rejects symmetry reuse because a value head is
    not guaranteed to be D4-equivariant.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Search configuration must be an object")
    unknown = set(raw) - set(SEARCH_SETTINGS_DEFAULTS)
    if unknown:
        raise ValueError(f"Unsupported search configuration field: {sorted(unknown)[0]}")
    result = dict(SEARCH_SETTINGS_DEFAULTS)
    for name in _SEARCH_BOOLEAN_FLAGS:
        if name in raw:
            value = raw[name]
            if type(value) is not bool:
                raise ValueError(f"Search {name} must be a boolean")
            result[name] = value
    if "table_capacity" in raw:
        capacity = raw["table_capacity"]
        if type(capacity) is not int or not 1 <= capacity <= MAX_TABLE_CAPACITY:
            raise ValueError(
                f"Search table_capacity must be an integer between 1 and {MAX_TABLE_CAPACITY}"
            )
        result["table_capacity"] = capacity
    if result["symmetry_transposition"]:
        # A canonical table is also an ordinary table; keep the dependency explicit.
        result["transposition"] = True
    if model_backed and (result["symmetry_transposition"] or result["symmetry_pruning"]):
        # Neural value heads are not guaranteed D4-equivariant even after
        # augmentation, so rotated positions must never share values or moves.
        raise ValueError("Symmetry search reuse is not available with neural evaluation")
    if result["legacy"]:
        result = {**SEARCH_SETTINGS_DEFAULTS, "legacy": True}
    return result


# Move-ordering strategies used by the benchmark harness to ablate individual
# priorities. ``full`` is the production default; the others exist so a measured
# comparison can justify or reject each heuristic. ``depth`` keeps the cheap,
# reliable table/PV ordering everywhere and only enables the volatile
# killer/history heuristics near the horizon. This is an internal, benchmark-only
# hook: it never enters the public ``search`` object or a saved identity.
_ORDERING_STRATEGIES = ("static", "pv", "tt", "killer", "history", "full", "depth")


@dataclass(frozen=True, slots=True)
class _SearchSettings:
    transposition: bool = True
    symmetry_transposition: bool = False
    symmetry_pruning: bool = False
    move_ordering: bool = True
    table_capacity: int = 100_000
    legacy: bool = False
    ordering: str = "full"
    root_window: bool = True


def _search_settings(config: dict, model_backed: bool) -> _SearchSettings:
    cleaned = clean_search_config(config.get("search"), model_backed=model_backed)
    capacity = cleaned["table_capacity"]
    assert isinstance(capacity, int)
    ordering = config.get("_ordering", "full")
    if ordering not in _ORDERING_STRATEGIES:
        raise ValueError(f"Unsupported move ordering strategy: {ordering}")
    root_window = config.get("_root_window", True)
    if type(root_window) is not bool:
        raise ValueError("Internal root_window flag must be a boolean")
    return _SearchSettings(
        transposition=bool(cleaned["transposition"]),
        symmetry_transposition=bool(cleaned["symmetry_transposition"]),
        symmetry_pruning=bool(cleaned["symmetry_pruning"]),
        move_ordering=bool(cleaned["move_ordering"]),
        table_capacity=capacity,
        legacy=bool(cleaned["legacy"]),
        ordering=str(ordering),
        root_window=root_window,
    )


def _order_children(
    children: list[tuple[Move, State, float]],
    *,
    strategy: str,
    remaining: int,
    tt_move: Move | None,
    pv_move: Move | None,
    killers: tuple[Move, ...],
    history: dict[Move, int],
) -> list[tuple[Move, State, float]]:
    """Apply one benchmark ordering strategy without changing correctness."""
    if strategy == "static":
        return children
    if strategy == "pv":
        return _prioritise(children, tt_move=None, pv_move=pv_move, killers=(), history={})
    if strategy == "tt":
        return _prioritise(children, tt_move=tt_move, pv_move=None, killers=(), history={})
    if strategy == "killer":
        return _prioritise(children, tt_move=None, pv_move=None, killers=killers, history={})
    if strategy == "history":
        return _prioritise(children, tt_move=None, pv_move=None, killers=(), history=history)
    if strategy == "depth":
        if remaining <= 3:
            return _prioritise(
                children,
                tt_move=tt_move,
                pv_move=pv_move,
                killers=killers,
                history=history,
            )
        return _prioritise(children, tt_move=tt_move, pv_move=pv_move, killers=(), history={})
    return _prioritise(children, tt_move=tt_move, pv_move=pv_move, killers=killers, history=history)


def _prioritise(
    children: list[tuple[Move, State, float]],
    *,
    tt_move: Move | None,
    pv_move: Move | None,
    killers: tuple[Move, ...],
    history: dict[Move, int],
) -> list[tuple[Move, State, float]]:
    """Move known-good moves forward while preserving the base order.

    Priority is the principal-variation move, then the transposition-table move,
    then killers, then history-ranked quiet moves, then the stable static order.
    History is consulted even when no explicit preferred move exists, so a quiet
    move that caused a cutoff still rises. The input is returned untouched when
    there is nothing to reorder: no preferred move and no nonzero history score.
    """
    if not children:
        return children
    priority: dict[Move, int] = {}
    if pv_move is not None:
        priority[pv_move] = 0
    if tt_move is not None:
        priority.setdefault(tt_move, 1)
    for index, move in enumerate(killers):
        priority.setdefault(move, 2 + index)
    preferred = [item for item in children if item[0] in priority]
    rest = [item for item in children if item[0] not in priority]
    # History promotes quiet moves that caused cutoffs before. Unscored moves
    # keep their stable static order because Python's sort is stable.
    if any(history.get(item[0], 0) for item in rest):
        rest.sort(key=lambda item: history.get(item[0], 0), reverse=True)
    if not preferred:
        return rest
    preferred.sort(key=lambda item: priority[item[0]])
    return preferred + rest


def _symmetry_move_pruning(
    position: State,
    moves: list[Move],
    check: Callable[[], None] | None = None,
    timing: dict | None = None,
) -> tuple[list[Move], int]:
    """Collapse legal moves into one deterministic representative per orbit.

    Successors are built only for the retained moves. A symmetry may prune only
    when the complete state, including ordered repetition history, is invariant
    under it, so the pruned move set preserves the minimax value. The
    representative is the least transformed move, which is guaranteed legal
    because a state stabilizer maps legal moves onto legal moves. ``check`` is
    invoked between orbit computations so a monotonic deadline is observed inside
    the stabilizer and the orbit loop.
    """
    if len(moves) < 2:
        return moves, 0
    stabilizer_started = time.perf_counter() if timing is not None else 0.0
    stabilizer = state_stabilizer(position, check)
    if timing is not None:
        timing["stabilizer_seconds"] += time.perf_counter() - stabilizer_started
    if len(stabilizer) < 2:
        return moves, 0
    retained: list[Move] = []
    seen: set[Move] = set()
    skipped = 0
    for move in moves:
        if check is not None:
            check()
        if move in seen:
            skipped += 1
            continue
        orbit = {transform_move(move, position.size, symmetry) for symmetry in stabilizer}
        seen |= orbit
        retained.append(min(orbit))
    return retained, skipped


def _alpha_beta_legacy(
    state: State,
    config: dict,
    rng: random.Random,
    deadline: float,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    """Original exact-value cache search, retained as the benchmark reference."""
    depth = int(config.get("depth", 2))
    if not 1 <= depth <= 8:
        raise ValueError("Search depth must be between 1 and 8")
    root_moves = legal_moves(state)
    best_move = rng.choice(root_moves)
    cache: dict[tuple, float] = {}
    model_path = config.get("model_path")
    model = config.get("_model")
    model_backed = bool(model_path) or model is not None
    heuristic = _heuristic_name(config)
    evaluator = _heuristic_evaluator(heuristic)
    maiden = heuristic == "shield_maiden" and not model_backed
    if diagnostics is None and progress is not None:
        diagnostics = {}
    if diagnostics is not None:
        diagnostics.update(
            value=policy_value(state, model_path, model)[1] if model_backed else evaluator(state),
            completed_depth=0,
            best_move=best_move.to_dict(),
        )

    def check_search() -> None:
        if should_stop and should_stop():
            raise InterruptedError("Search cancelled")
        if time.monotonic() >= deadline:
            raise _Deadline

    def search(
        position: State, remaining: int, alpha: float, beta: float, incoming: float | None
    ) -> float:
        check_search()
        if position.winner:
            return evaluate(position)
        if remaining == 0:
            if model_backed:
                return policy_value(position, model_path, model)[1]
            return incoming if incoming is not None else evaluator(position)
        # Include repetition context: identical boards can have different outcomes.
        key = (position.board, position.turn, position.history, remaining)
        if key in cache:
            return cache[key]
        moves = legal_moves(position)
        if not moves:
            return -1.0 if position.turn == "defenders" else 1.0
        maximizing = position.turn == "defenders"
        value = -math.inf if maximizing else math.inf
        fully_searched = True
        for _, child, maiden_value in _ordered(
            position, moves, rng, maiden=maiden, check=check_search
        ):
            score = search(
                child, remaining - 1, alpha, beta, None if model_backed else maiden_value
            )
            value = max(value, score) if maximizing else min(value, score)
            if maximizing:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if beta <= alpha:
                fully_searched = False
                break
        if fully_searched:
            cache[key] = value
        return value

    # Iterative deepening retains the last completed result on deadline expiry.
    sign = 1 if state.turn == "defenders" else -1
    for current_depth in range(1, depth + 1):
        iteration_best = best_move
        score_best = -math.inf
        try:
            ordered = _ordered(state, list(root_moves), rng, maiden=maiden, check=check_search)
            for move, child, maiden_value in ordered:
                score = sign * search(
                    child,
                    current_depth - 1,
                    -math.inf,
                    math.inf,
                    None if model_backed else maiden_value,
                )
                if score > score_best:
                    score_best, iteration_best = score, move
        except _Deadline:
            break
        best_move = iteration_best
        if diagnostics is not None:
            diagnostics.update(
                value=sign * score_best,
                completed_depth=current_depth,
                best_move=best_move.to_dict(),
            )
            if progress is not None:
                progress(analysis_from_search(state, config, best_move, diagnostics))
    return best_move


def _alpha_beta(
    state: State,
    config: dict,
    rng: random.Random,
    deadline: float,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    """Iterative-deepening alpha-beta with bounded transpositions and symmetry.

    The optional ``search`` configuration selects ordinary transposition bounds,
    D4-canonical transpositions, full-state symmetry move pruning and improved
    move ordering independently. Symmetry features are refused with a neural
    evaluator, whose value head is not guaranteed to be D4-equivariant.
    """
    depth = int(config.get("depth", 2))
    if not 1 <= depth <= 8:
        raise ValueError("Search depth must be between 1 and 8")
    model_path = config.get("model_path")
    model = config.get("_model")
    model_backed = bool(model_path) or model is not None
    settings = _search_settings(config, model_backed)
    heuristic = _heuristic_name(config)
    evaluator = _heuristic_evaluator(heuristic)
    maiden = heuristic == "shield_maiden" and not model_backed
    if settings.legacy:
        return _alpha_beta_legacy(
            state,
            config,
            rng,
            deadline,
            diagnostics=diagnostics,
            progress=progress,
            should_stop=should_stop,
        )
    root_moves = legal_moves(state)
    best_move = rng.choice(root_moves)
    sign = 1 if state.turn == "defenders" else -1
    table = (
        _TranspositionTable(settings.table_capacity)
        if settings.transposition or settings.symmetry_transposition
        else None
    )
    killers: dict[int, list[Move]] = {}
    history: dict[Move, int] = {}
    pv_move: Move | None = None
    counters = {
        "nodes": 0,
        "evaluations": 0,
        "cutoffs": 0,
        "successors": 0,
        "ordered_moves": 0,
        "symmetry_hits": 0,
        "transposition_cross_orientation_hits": 0,
        "symmetry_moves_skipped": 0,
        "canonicalization_seconds": 0.0,
        "stabilizer_seconds": 0.0,
        "pruning_seconds": 0.0,
        "successor_seconds": 0.0,
        "sort_seconds": 0.0,
        "transposition_seconds": 0.0,
        "transposition_depth_compatible": 0,
        "transposition_board_history_divergence": 0,
    }
    # Benchmark-only: distinct (board, turn) keys whose repetition context
    # differed. This measures the reuse the full-history key forgoes.
    seen_board_keys: dict[tuple, tuple] = {}
    # Timing instrumentation is opt-in through the benchmark-only ``_profile``
    # hook. Ordinary play and tournaments still collect the cheap integer
    # counters without paying for per-node perf_counter calls.
    profile = bool(config.get("_profile", False))
    search_started = time.monotonic()
    if diagnostics is None and progress is not None:
        diagnostics = {}
    if diagnostics is not None:
        diagnostics.update(
            value=policy_value(state, model_path, model)[1] if model_backed else evaluator(state),
            completed_depth=0,
            best_move=best_move.to_dict(),
        )

    def check_search() -> None:
        if should_stop and should_stop():
            raise InterruptedError("Search cancelled")
        if time.monotonic() >= deadline:
            raise _Deadline

    def tt_key(position: State) -> tuple[object, Symmetry | None]:
        if settings.symmetry_transposition:
            started = time.perf_counter()
            key, symmetry = canonical_state_key(position, check_search)
            counters["canonicalization_seconds"] += time.perf_counter() - started
            return key, symmetry
        signature = _cached_repetition_signature(position.history)
        if profile:
            board_key = (position.ruleset, position.board, position.turn)
            previous = seen_board_keys.get(board_key)
            if previous is None:
                seen_board_keys[board_key] = signature
            elif previous != signature:
                counters["transposition_board_history_divergence"] += 1
        return (position.ruleset, position.board, position.turn, signature), None

    def leaf(position: State) -> float:
        counters["evaluations"] += 1
        return policy_value(position, model_path, model)[1] if model_backed else evaluator(position)

    def search(
        position: State,
        remaining: int,
        alpha: float,
        beta: float,
        ply: int,
        incoming: float | None,
    ) -> float:
        check_search()
        if position.winner:
            counters["evaluations"] += 1
            return evaluate(position)
        if remaining == 0:
            if model_backed:
                return leaf(position)
            counters["evaluations"] += 1
            return incoming if incoming is not None else evaluator(position)
        counters["nodes"] += 1
        key, symmetry = tt_key(position)
        tt_move: Move | None = None
        if profile:
            transposition_started = time.perf_counter()
        entry = table.probe(key) if table is not None else None
        if profile and table is not None:
            counters["transposition_seconds"] += time.perf_counter() - transposition_started
        if entry is not None:
            if entry.depth >= remaining:
                counters["transposition_depth_compatible"] += 1
            if settings.symmetry_transposition:
                counters["symmetry_hits"] += 1
                # A stored orientation different from this position's canonical
                # orientation is genuine cross-orientation reuse, not a plain
                # same-orientation table hit.
                if symmetry is not None and entry.orientation != int(symmetry):
                    counters["transposition_cross_orientation_hits"] += 1
            if entry.best_move is not None:
                if symmetry is not None:
                    tt_move = transform_move(entry.best_move, position.size, inverse(symmetry))
                else:
                    tt_move = entry.best_move
            alpha, beta, cutoff = _tt_tighten(entry, remaining, alpha, beta)
            if cutoff is not None:
                if table is not None:
                    if cutoff == _Bound.EXACT:
                        table.exact_hits += 1
                    else:
                        table.bound_hits += 1
                        table.cutoffs += 1
                return entry.value
            if entry.depth >= remaining and table is not None:
                # A depth-compatible lower/upper entry narrowed the window.
                table.bound_hits += 1
        # The window actually searched may be narrower than the caller's because a
        # table bound tightened alpha or beta above. Capture it before the move
        # loop mutates the values, so a fail-low is never published as exact.
        alpha_window = alpha
        beta_window = beta
        moves = legal_moves(position)
        if not moves:
            counters["evaluations"] += 1
            return -1.0 if position.turn == "defenders" else 1.0
        maximizing = position.turn == "defenders"
        value = -math.inf if maximizing else math.inf
        best_child_move: Move | None = None
        # Prune symmetry-equivalent moves before building any successor state,
        # then order the retained moves.
        if settings.symmetry_pruning:
            if profile:
                pruning_started = time.perf_counter()
            moves, skipped = _symmetry_move_pruning(
                position, moves, check_search, timing=counters if profile else None
            )
            if profile:
                counters["pruning_seconds"] += time.perf_counter() - pruning_started
            counters["symmetry_moves_skipped"] += skipped
        if profile:
            successor_started = time.perf_counter()
        children = _ordered(position, moves, rng, maiden=maiden, check=check_search)
        if profile:
            counters["successor_seconds"] += time.perf_counter() - successor_started
        counters["successors"] += len(children)
        counters["ordered_moves"] += len(children)
        if settings.move_ordering:
            if profile:
                sort_started = time.perf_counter()
            children = _order_children(
                children,
                strategy=settings.ordering,
                remaining=remaining,
                tt_move=tt_move,
                pv_move=None,
                killers=tuple(killers.get(ply, ())),
                history=history,
            )
            if profile:
                counters["sort_seconds"] += time.perf_counter() - sort_started
        for move, child, maiden_value in children:
            score = search(
                child,
                remaining - 1,
                alpha,
                beta,
                ply + 1,
                None if model_backed else maiden_value,
            )
            if maximizing:
                if score > value:
                    value, best_child_move = score, move
                alpha = max(alpha, value)
            else:
                if score < value:
                    value, best_child_move = score, move
                beta = min(beta, value)
            if beta <= alpha:
                counters["cutoffs"] += 1
                current = killers.setdefault(ply, [])
                if move not in current:
                    current.insert(0, move)
                    del current[2:]
                history[move] = history.get(move, 0) + remaining * remaining
                break
        if table is not None:
            bound = _bound_for(value, alpha_window, beta_window)
            stored_move = best_child_move
            if stored_move is not None and symmetry is not None:
                stored_move = transform_move(stored_move, position.size, symmetry)
            if profile:
                transposition_started = time.perf_counter()
            table.store(
                key,
                remaining,
                value,
                bound,
                stored_move,
                int(symmetry) if symmetry is not None else None,
            )
            if profile:
                counters["transposition_seconds"] += time.perf_counter() - transposition_started
        return value

    iteration_timings: list[dict] = []
    for current_depth in range(1, depth + 1):
        iteration_best = best_move
        score_best = -math.inf
        iteration_started = time.monotonic()
        nodes_before = counters["nodes"]
        try:
            root_candidates = list(root_moves)
            if settings.symmetry_pruning:
                root_candidates, skipped = _symmetry_move_pruning(
                    state, root_candidates, check_search
                )
                counters["symmetry_moves_skipped"] += skipped
            ordered = _ordered(state, root_candidates, rng, maiden=maiden, check=check_search)
            if settings.move_ordering and pv_move is not None:
                ordered = _order_children(
                    ordered,
                    strategy=settings.ordering,
                    remaining=current_depth,
                    tt_move=None,
                    pv_move=pv_move,
                    killers=(),
                    history={},
                )
            # Root alpha-beta: once a candidate value is known, later children
            # are searched with a narrowed window. Passing a full window to every
            # root child made the root a pure minimax fan-out, so move ordering
            # could never prune anything at the widest part of the tree. The
            # first move to reach the best value wins, which stays deterministic.
            root_maximizing = state.turn == "defenders"
            best_value = -math.inf if root_maximizing else math.inf
            for move, child, maiden_value in ordered:
                incoming = None if model_backed else maiden_value
                if not settings.root_window:
                    # Benchmark-only reference: the historical full-window fan-out.
                    value = search(child, current_depth - 1, -math.inf, math.inf, 1, incoming)
                elif root_maximizing:
                    value = search(child, current_depth - 1, best_value, math.inf, 1, incoming)
                else:
                    value = search(child, current_depth - 1, -math.inf, best_value, 1, incoming)
                if root_maximizing:
                    if value > best_value:
                        best_value, iteration_best = value, move
                elif value < best_value:
                    best_value, iteration_best = value, move
            score_best = sign * best_value
        except _Deadline:
            if diagnostics is not None:
                diagnostics.update(
                    interrupted_depth=current_depth,
                    interrupted_seconds=time.monotonic() - iteration_started,
                    discarded_nodes=counters["nodes"] - nodes_before,
                )
            break
        best_move = iteration_best
        pv_move = iteration_best
        iteration_timings.append(
            {
                "depth": current_depth,
                "seconds": time.monotonic() - iteration_started,
                "nodes": counters["nodes"] - nodes_before,
            }
        )
        if diagnostics is not None:
            diagnostics.update(
                value=sign * score_best,
                completed_depth=current_depth,
                chosen_from_completed_iteration=True,
                best_move=best_move.to_dict(),
                nodes=counters["nodes"],
                evaluations=counters["evaluations"],
                cutoffs=counters["cutoffs"],
                successors=counters["successors"],
                ordered_moves=counters["ordered_moves"],
                searched_moves=len(root_moves),
                symmetry_hits=counters["symmetry_hits"],
                transposition_cross_orientation_hits=counters[
                    "transposition_cross_orientation_hits"
                ],
                transposition_depth_compatible=counters["transposition_depth_compatible"],
                transposition_board_history_divergence=counters[
                    "transposition_board_history_divergence"
                ],
                symmetry_moves_skipped=counters["symmetry_moves_skipped"],
                canonicalization_seconds=counters["canonicalization_seconds"],
                stabilizer_seconds=counters["stabilizer_seconds"],
                pruning_seconds=counters["pruning_seconds"],
                successor_seconds=counters["successor_seconds"],
                sort_seconds=counters["sort_seconds"],
                transposition_seconds=counters["transposition_seconds"],
                iteration_timings=list(iteration_timings),
            )
            if table is not None:
                diagnostics.update(
                    transposition_lookups=table.lookups,
                    transposition_successful_lookups=table.hits,
                    transposition_exact_hits=table.exact_hits,
                    transposition_bound_hits=table.bound_hits,
                    transposition_cutoffs=table.cutoffs,
                    transposition_stores=table.stores,
                    transposition_replacements=table.replacements,
                    transposition_evictions=table.evictions,
                    transposition_entries=len(table.entries),
                    transposition_peak_entries=table.peak_entries,
                )
            if progress is not None:
                progress(analysis_from_search(state, config, best_move, diagnostics))
    if diagnostics is not None:
        elapsed = max(time.monotonic() - search_started, 1e-9)
        diagnostics["nodes_per_second"] = counters["nodes"] / elapsed
        diagnostics.setdefault("searched_moves", len(root_moves))
        diagnostics.setdefault("chosen_from_completed_iteration", bool(iteration_timings))
    return best_move


@dataclass(slots=True)
class _Node:
    state: State
    prior: float = 1.0
    visits: int = 0
    total: float = 0.0
    children: dict[Move, "_Node"] = field(default_factory=dict)
    # Owned legal priors only, consumed as edges are created. No logits/tensors.
    remaining: dict[Move, float] | None = None
    value: float | None = None
    # Leaves usually have no children yet. Pack deferred policies to avoid
    # retaining a dictionary, Move objects and boxed floats for every leaf.
    pending_policy: tuple[array, array] | None = None


def _select_child(node: _Node, exploration: float) -> _Node:
    """PUCT in insertion order, retaining the reference arithmetic and first tie."""
    sign = 1 if node.state.turn == "defenders" else -1
    parent_scale = math.sqrt(node.visits + 1)
    best = None
    best_score = -math.inf
    for child in node.children.values():
        # Every selectable edge has completed at least one backup. A pending
        # child cannot be selected while its generator is waiting for inference.
        score = sign * child.total / child.visits + exploration * child.prior * parent_scale / (
            1 + child.visits
        )
        if best is None or score > best_score:
            best, best_score = child, score
    assert best is not None
    return best


def mcts_context(
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Generator[State, tuple[dict[Move, float], float], dict[Move, float]]:
    """Cooperative PUCT search yielding one dependent evaluation at a time.

    Send legal priors and a defender-relative value for each yielded position.
    Independent searches can share inference batches; this context never advances
    a dependent simulation until its evaluation has been supplied.
    """
    config = {"algorithm": "mcts", **(config or {})}
    budget = int(config.get("iterations", 80))
    if not 1 <= budget <= 100_000:
        raise ValueError("MCTS iterations must be between 1 and 100000")
    exploration = float(config.get("exploration", 1.4))
    if not 0 <= exploration <= 20:
        raise ValueError("Exploration must be between 0 and 20")
    rollout_depth = int(config.get("rollout_depth", 8))
    if not 0 <= rollout_depth <= 128:
        raise ValueError("Rollout depth must be between 0 and 128")
    seconds = float(config.get("time_limit", 10.0))
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    rng = random.Random(config.get("seed", 0))
    model_path = config.get("model_path")
    model = config.get("_model")
    neural = bool(model_path) or model is not None
    evaluator = _heuristic_evaluator(_heuristic_name(config))
    deadline = time.monotonic() + seconds
    root = _Node(state)

    def check_cancelled() -> None:
        if should_stop is not None and should_stop():
            raise InterruptedError("Self-play search cancelled")

    check_cancelled()
    if state.winner or not legal_moves(state):
        raise ValueError("No legal moves in terminal position")
    root_priors, root_value = yield state
    check_cancelled()
    requests = int(neural)
    prediction_reuses = 0
    if diagnostics is None and progress is not None:
        diagnostics = {}
    if diagnostics is not None:
        diagnostics.update(
            value=root_value,
            root_value=root_value,
            simulations=0,
            mcts_neural_requests=requests,
            mcts_node_prediction_reuses=0,
        )
    # Never overlook a one-move win even with a tiny simulation budget.
    for move in root_priors:
        check_cancelled()
        if apply_move(state, move).winner == state.turn:
            if diagnostics is not None:
                diagnostics.update(
                    value=1.0 if state.turn == "defenders" else -1.0,
                    best_move=move.to_dict(),
                    immediate_win_shortcuts=1,
                )
                if progress is not None:
                    progress(analysis_from_search(state, config, move, diagnostics))
            return {move: 1.0}
    noise_fraction = float(config.get("dirichlet_fraction", 0.0))
    if not 0 <= noise_fraction <= 1:
        raise ValueError("Dirichlet fraction must be between zero and one")
    if noise_fraction and root_priors:
        alpha = float(config.get("dirichlet_alpha", 0.3))
        if alpha <= 0:
            raise ValueError("Dirichlet alpha must be positive")
        noise = [rng.gammavariate(alpha, 1) for _ in root_priors]
        total_noise = sum(noise)
        root_priors = {
            move: (1 - noise_fraction) * prior + noise_fraction * value / total_noise
            for (move, prior), value in zip(root_priors.items(), noise)
        }
    if not root_priors:
        raise ValueError("No legal moves in terminal position")
    # Lazily create states. Expanding hundreds of moves is otherwise expensive.
    root.remaining = root_priors.copy()
    root.value = root_value
    next_report = 0.0
    for _ in range(budget):
        check_cancelled()
        if time.monotonic() >= deadline and root.visits:
            if diagnostics is not None:
                diagnostics["deadline_cutoffs"] = 1
            break
        node = root
        path = [node]
        while not node.state.winner:
            check_cancelled()
            remaining = node.remaining
            if remaining is None:
                if node.pending_policy is not None:
                    actions, probabilities = node.pending_policy
                    area = node.state.size**2
                    remaining = {
                        Move(action // area, action % area): probability
                        for action, probability in zip(actions, probabilities)
                    }
                    node.pending_policy = None
                    del actions, probabilities
                    prediction_reuses += 1
                else:
                    priors, predicted_value = yield node.state
                    check_cancelled()
                    remaining = dict(priors)
                    del priors
                    node.value = predicted_value
                node.remaining = remaining
            # Progressive widening explores deeply even on an opening board
            # with hundreds of legal moves and a modest simulation budget.
            # For integer visits/edge count, k < ceil(sqrt(v + 1)) iff k*k <= v.
            if remaining and len(node.children) ** 2 <= node.visits:
                choices = list(remaining)
                # Sample priors, giving every legal move an opportunity.
                weights = [remaining[m] for m in choices]
                move = rng.choices(choices, weights=weights, k=1)[0]
                prior = remaining.pop(move)
                child = _Node(apply_move(node.state, move), prior=prior)
                node.children[move] = child
                node = child
                path.append(node)
                break
            if not node.children:
                break
            node = _select_child(node, exploration)
            path.append(node)
        if node.state.winner:
            value = evaluate(node.state)
        elif neural:
            if node.value is None:
                requests += 1
                priors, value = yield node.state
                check_cancelled()
                area = node.state.size**2
                # uint16 covers all current boards (largest action is 14640).
                # Doubles retain the original Python float exactly; sampling
                # order and RNG consumption are identical after unpacking.
                node.pending_policy = (
                    array("H", (move.source * area + move.target for move in priors)),
                    array("d", priors.values()),
                )
                del priors
                node.value = value
            else:
                prediction_reuses += 1
                value = node.value
        else:
            rollout = node.state
            for _ in range(rollout_depth):
                if time.monotonic() >= deadline or rollout.winner:
                    break
                moves = legal_moves(rollout)
                if not moves:
                    break
                rollout = apply_move(rollout, rng.choice(moves))
            value = evaluator(rollout)
        for ancestor in path:
            ancestor.visits += 1
            ancestor.total += value
        if diagnostics is not None:
            diagnostics.update(
                simulations=root.visits,
                mcts_neural_requests=requests,
                mcts_node_prediction_reuses=prediction_reuses,
            )
        # Bound observer overhead while making long-running searches useful live.
        if progress is not None and time.monotonic() >= next_report:
            assert diagnostics is not None
            best = max(root.children, key=lambda move: root.children[move].visits)
            diagnostics.update(
                value=root.total / root.visits,
                simulations=root.visits,
                best_move=best.to_dict(),
            )
            progress(analysis_from_search(state, config, best, diagnostics))
            next_report = time.monotonic() + 0.1
    total = sum(child.visits for child in root.children.values())
    if diagnostics is not None:
        best = (
            max(root.children, key=lambda move: root.children[move].visits)
            if total
            else max(root_priors, key=lambda move: root_priors[move])
        )
        diagnostics.update(
            value=root.total / root.visits if root.visits else root_value,
            simulations=root.visits,
            best_move=best.to_dict(),
        )
        if progress is not None:
            progress(analysis_from_search(state, config, best, diagnostics))
    if not total:
        return root_priors
    return {move: child.visits / total for move, child in root.children.items()}


def mcts_policy(
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    inference_metrics: Any = None,
) -> dict[Move, float]:
    """Drive the cooperative search synchronously with reference single inference."""
    configuration = config or {}
    model_path = configuration.get("model_path")
    model = configuration.get("_model")
    context = mcts_context(
        state, config, diagnostics=diagnostics, progress=progress, should_stop=should_stop
    )
    try:
        pending = next(context)
        while True:
            if (
                configuration.get("_inference_backend") is not None
                or configuration.get("_inference_cache") is not None
                or configuration.get("_mixed_precision", False)
            ):
                from .inference import policy_value_batch

                assert model is not None
                result = policy_value_batch(
                    [pending],
                    model,
                    mixed_precision=bool(configuration.get("_mixed_precision", False)),
                    metrics=inference_metrics,
                    backend=configuration.get("_inference_backend"),
                    cache=configuration.get("_inference_cache"),
                )[0]
            elif inference_metrics is not None and (model_path or model is not None):
                device = next(model.parameters()).device if model is not None else "cpu"
                inference_metrics.requests += 1
                inference_metrics.cache_misses += 1
                with inference_metrics.measure(1, device=device):
                    result = policy_value(pending, model_path, model)
            else:
                result = policy_value(pending, model_path, model)
            pending = context.send(result)
    except StopIteration as finished:
        return finished.value
    finally:
        context.close()


def _profile_configuration(config: dict | None) -> dict:
    """Apply a selected profile's defaults without replacing explicit settings.

    HTTP sessions already retain resolved configurations. Direct API, CLI and
    experiment callers can instead select only a profile; they must search with
    the same parameters as a game created through the browser.
    """
    configuration = dict(config or {})
    if "algorithm" not in configuration and configuration.get("profile_id"):
        profile = next(
            (profile for profile in ai_profiles() if profile["id"] == configuration["profile_id"]),
            None,
        )
        if profile is None:
            raise ValueError(f"Unknown AI profile: {configuration['profile_id']}")
        configuration = {
            **profile["parameters"],
            **configuration,
            "algorithm": profile["algorithm"],
        }
    return configuration


def choose_move(
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    """Search once, optionally exposing completed results to an observer.

    Progress receives independent analysis snapshots. Observer exceptions propagate,
    permitting callers to cancel a superseded background search cleanly.
    """
    config = _profile_configuration(config)
    if should_stop and should_stop():
        raise InterruptedError("Search cancelled")
    moves = legal_moves(state)
    if state.winner or not moves:
        raise ValueError("Cannot choose a move in a terminal position")
    algorithm = config.get("algorithm", config.get("profile_id", "baseline"))
    wrapper = config.get("search_wrapper", "mcts")
    if algorithm == "neural":
        if wrapper not in ("mcts", "alpha_beta", "none"):
            raise ValueError("Neural search wrapper must be mcts, alpha_beta or none")
        if not config.get("model_path") and config.get("_model") is None:
            raise ValueError("The neural opponent requires a registered model")
    rng = random.Random(config.get("seed", 0))
    # Only time-limited MCTS defaults to ten seconds; non-MCTS strategies keep
    # their historical two-second ceiling when no explicit value is supplied.
    mcts_budget = algorithm == "mcts" or (algorithm == "neural" and wrapper == "mcts")
    seconds = float(config.get("time_limit", 10.0 if mcts_budget else 2.0))
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    deadline = time.monotonic() + seconds
    if diagnostics is None and progress is not None:
        diagnostics = {}
    latest: dict | None = None

    def notify(snapshot: dict) -> None:
        nonlocal latest
        if progress is not None and snapshot != latest:
            # Keep private comparison state even if an observer changes its snapshot.
            latest = {**snapshot, "best_move": dict(snapshot["best_move"])}
            progress(snapshot)

    observer = notify if progress is not None else None

    def finished(move: Move) -> Move:
        if diagnostics is not None:
            diagnostics["best_move"] = move.to_dict()
            notify(analysis_from_search(state, config, move, diagnostics))
        return move

    if algorithm == "shield_maiden":
        from .shield_maiden import VERSION, choose

        version = config.get("heuristic_version", VERSION)
        if type(version) is not int or version != VERSION:
            raise ValueError("Unsupported Shield maiden heuristic version")
        return finished(
            choose(state, moves, rng, deadline, diagnostics=diagnostics, should_stop=should_stop)
        )
    if algorithm == "baseline":
        sign = 1 if state.turn == "defenders" else -1
        rng.shuffle(moves)
        best, score = moves[0], -math.inf
        searched = 0
        for move in moves:
            if should_stop and should_stop():
                raise InterruptedError("Search cancelled")
            if time.monotonic() >= deadline and score > -math.inf:
                break
            child = apply_move(state, move)
            value = sign * evaluate(child)
            searched += 1
            if value > score:
                best, score = move, value
        if diagnostics is not None:
            diagnostics.update(value=sign * score, completed_depth=1, searched_moves=searched)
        return finished(best)
    if algorithm in ("alpha_beta", "alphabeta") or (
        algorithm == "neural" and wrapper == "alpha_beta"
    ):
        return finished(
            _alpha_beta(
                state,
                config,
                rng,
                deadline,
                diagnostics=diagnostics if diagnostics is not None or algorithm != "neural" else {},
                progress=observer,
                should_stop=should_stop,
            )
        )
    if algorithm in ("mcts", "neural"):
        if algorithm == "neural" and wrapper == "none":
            probabilities, value = policy_value(
                state, config.get("model_path"), config.get("_model")
            )
            if diagnostics is not None:
                diagnostics["value"] = value
        else:
            probabilities = mcts_policy(
                state, config, diagnostics=diagnostics, progress=observer, should_stop=should_stop
            )
        temperature = float(config.get("temperature", 0.0))
        if not math.isfinite(temperature) or not 0 <= temperature <= 10:
            raise ValueError("Temperature must be between 0 and 10")
        if temperature > 0:
            options = list(probabilities)
            # Scale before exponentiating so a low temperature cannot underflow
            # every legal policy probability to zero. Preserve historical MCTS
            # sampling arithmetic for existing seeded games.
            maximum = (
                max(probabilities.values()) if algorithm == "neural" and wrapper == "none" else 1
            )
            weights = [(probabilities[move] / maximum) ** (1 / temperature) for move in options]
            return finished(rng.choices(options, weights=weights, k=1)[0])
        return finished(max(probabilities, key=lambda move: probabilities[move]))
    raise ValueError(f"Unknown AI algorithm: {algorithm}")


def analysis_from_search(
    state: State, config: dict | None, move: Move | None, details: dict
) -> dict:
    """Build a report from actual search diagnostics without evaluating again."""
    if state.winner:
        return {"value": evaluate(state), "source": "terminal", "best_move": None}
    configuration = _profile_configuration(config)
    algorithm = configuration.get("algorithm", configuration.get("profile_id", "baseline"))
    model_backed = algorithm in {"alpha_beta", "alphabeta", "mcts", "neural"} and (
        bool(configuration.get("model_path")) or configuration.get("_model") is not None
    )
    source = (
        "neural_search" if model_backed else "mcts_rollout" if algorithm == "mcts" else "heuristic"
    )
    if model_backed and algorithm == "neural" and configuration.get("search_wrapper") == "none":
        source = "neural_value"
    value = details["value"]
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("Agent evaluation must be finite and between -1 and 1")
    result = {"value": value, "source": source, "best_move": move.to_dict() if move else None}
    for key in (
        "completed_depth",
        "interrupted_depth",
        "simulations",
        "searched_moves",
        "nodes",
        "evaluations",
        "cutoffs",
        "successors",
        "ordered_moves",
        "symmetry_moves_skipped",
        "chosen_from_completed_iteration",
    ):
        if key in details:
            result[key] = details[key]
    exact = details.get("transposition_exact_hits")
    bound = details.get("transposition_bound_hits")
    if exact is not None or bound is not None:
        result["transposition_hits"] = (exact or 0) + (bound or 0)
    return result


def analyze_position(
    state: State, config: dict | None = None, *, progress: Callable[[dict], None] | None = None
) -> dict:
    """Expose an agent's bounded value estimate and strongest legal suggestion.

    Values retain the defender perspective used by training: +1 is a defender
    win, -1 an attacker win, and a rule draw is zero. A linear conversion to a
    percentage estimates expected score (draws half), not a calibrated frequency
    of wins. Traditional MCTS averages terminal/heuristic rollout leaves; a
    neural-search agent averages its backed-up neural/terminal leaf values. Alpha-beta
    reports its last completed minimax depth using its heuristic or neural leaf
    evaluator, falling back to its root evaluator if its deadline expires before
    completing depth one.

    Temperature affects actual play sampling, but suggestions always select the
    strongest searched option. Search and evaluation never mutate the position
    or caller configuration.
    """
    if state.winner:
        result = analysis_from_search(state, config, None, {})
        if progress is not None:
            progress(result.copy())
        return result
    configuration = _profile_configuration(config)
    configuration["temperature"] = 0
    details: dict = {}
    move = choose_move(state, configuration, diagnostics=details, progress=progress)
    return analysis_from_search(state, configuration, move, details)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _search_diagnostics_summary(records: list[tuple[dict, float, int | None]]) -> dict:
    """Aggregate bounded per-decision search diagnostics for one opponent.

    Each record is ``(details, seconds, configured_depth)``. Only counters the
    search actually produced are summarised, so non-alpha-beta or historical
    opponents simply yield fewer fields. The summary is intentionally compact:
    tournaments store means and totals, never the raw per-decision stream.
    """
    if not records:
        return {"decisions": 0}
    depths = [
        float(d["completed_depth"]) for d, _, _ in records if d.get("completed_depth") is not None
    ]
    caps = [cap for _, _, cap in records if isinstance(cap, int)]
    reached = sum(
        1 for d, _, cap in records if isinstance(cap, int) and d.get("completed_depth") == cap
    )

    def mean_key(key: str) -> float | None:
        return _mean([float(d[key]) for d, _, _ in records if isinstance(d.get(key), (int, float))])

    return {
        "decisions": len(records),
        "mean_completed_depth": _mean(depths),
        "mean_decision_time": _mean([seconds for _, seconds, _ in records]),
        "mean_nodes": mean_key("nodes"),
        "mean_evaluations": mean_key("evaluations"),
        "total_cutoffs": sum(int(d.get("cutoffs", 0)) for d, _, _ in records),
        "transposition_cutoffs": sum(int(d.get("transposition_cutoffs", 0)) for d, _, _ in records),
        "symmetry_moves_skipped": sum(
            int(d.get("symmetry_moves_skipped", 0)) for d, _, _ in records
        ),
        "depth_cap_reached_fraction": (reached / len(caps)) if caps else None,
    }


def _merge_diagnostics(accumulator: dict, summary: dict) -> None:
    """Accumulate one game's per-side summary into an opponent's running totals."""
    decisions = int(summary.get("decisions", 0))
    if not decisions:
        return
    accumulator["decisions"] = accumulator.get("decisions", 0) + decisions
    for key in ("completed_depth", "nodes", "evaluations"):
        value = summary.get(f"mean_{key}")
        if value is not None:
            accumulator[f"{key}_sum"] = accumulator.get(f"{key}_sum", 0.0) + value * decisions
            accumulator[f"{key}_count"] = accumulator.get(f"{key}_count", 0) + decisions
    accumulator["decision_time_sum"] = (
        accumulator.get("decision_time_sum", 0.0)
        + float(summary.get("mean_decision_time") or 0.0) * decisions
    )
    for key in ("total_cutoffs", "transposition_cutoffs", "symmetry_moves_skipped"):
        accumulator[key] = accumulator.get(key, 0) + int(summary.get(key, 0))
    fraction = summary.get("depth_cap_reached_fraction")
    if fraction is not None:
        accumulator["cap_sum"] = accumulator.get("cap_sum", 0.0) + float(fraction) * decisions
        accumulator["cap_count"] = accumulator.get("cap_count", 0) + decisions


def _finalise_diagnostics(accumulator: dict) -> dict:
    decisions = int(accumulator.get("decisions", 0))
    if not decisions:
        return {"decisions": 0}

    def ratio(sum_key: str, count_key: str) -> float | None:
        count = accumulator.get(count_key, 0)
        return accumulator[sum_key] / count if count else None

    return {
        "decisions": decisions,
        "mean_completed_depth": ratio("completed_depth_sum", "completed_depth_count"),
        "mean_decision_time": accumulator.get("decision_time_sum", 0.0) / decisions,
        "mean_nodes": ratio("nodes_sum", "nodes_count"),
        "mean_evaluations": ratio("evaluations_sum", "evaluations_count"),
        "total_cutoffs": accumulator.get("total_cutoffs", 0),
        "transposition_cutoffs": accumulator.get("transposition_cutoffs", 0),
        "symmetry_moves_skipped": accumulator.get("symmetry_moves_skipped", 0),
        "depth_cap_reached_fraction": ratio("cap_sum", "cap_count"),
    }


def play_game(
    ruleset: str,
    attackers: dict | None = None,
    defenders: dict | None = None,
    *,
    seed: int = 0,
    max_plies: int = 300,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Play an experiment, distinguishing unfinished cap runs from rule draws."""
    if not 1 <= max_plies <= 5000:
        raise ValueError("max_plies must be between 1 and 5000")
    state = new_game(ruleset)
    moves, durations = [], []
    records: dict[str, list[tuple[dict, float, int | None]]] = {"attackers": [], "defenders": []}
    for ply in range(max_plies):
        if should_stop and should_stop():
            raise InterruptedError("Tournament stopped during the current game")
        if state.winner:
            break
        config = dict((attackers if state.turn == "attackers" else defenders) or {})
        config["seed"] = seed + ply
        details: dict = {}
        started = time.monotonic()
        move = choose_move(state, config, diagnostics=details)
        elapsed = time.monotonic() - started
        durations.append(elapsed)
        configured_depth = config.get("depth")
        cap = (
            int(configured_depth)
            if isinstance(configured_depth, (int, float)) and not isinstance(configured_depth, bool)
            else None
        )
        records[state.turn].append((details, elapsed, cap))
        moves.append({"source": move.source, "target": move.target})
        state = apply_move(state, move)
    return {
        "ruleset": ruleset,
        "winner": state.winner,
        "outcome": "completed" if state.winner else "truncated",
        "reason": state.reason or "experiment_ply_limit",
        "plies": len(moves),
        "moves": moves,
        "average_decision_time": sum(durations) / max(1, len(durations)),
        "total_decision_time": sum(durations),
        "diagnostics": {
            side: _search_diagnostics_summary(records[side]) for side in ("attackers", "defenders")
        },
        "state": state.to_dict(),
    }


def tournament(
    config: dict,
    report: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    profiles = config.get(
        "players", [{"algorithm": "baseline"}, {"algorithm": "mcts", "iterations": 20}]
    )
    if not isinstance(profiles, list) or not 2 <= len(profiles) <= 4:
        raise ValueError("A tournament compares between two and four profiles")
    games_per_ruleset = int(config.get("games", 2))
    if not 2 <= games_per_ruleset <= 10000 or games_per_ruleset % 2:
        raise ValueError("Use an even number of games (2–10000) to alternate sides fairly")
    results: list[dict] = []
    scores = [0.0] * len(profiles)
    standings: list[dict] = [
        dict(player=index, wins=0, losses=0, draws=0, truncated=0, games=0, score=0.0)
        for index in range(len(profiles))
    ]
    # Per-opponent running search diagnostics, merged from each game's per-side
    # summary. Kept out of the public ``results`` payload until snapshot time.
    diagnostics_accumulators: list[dict] = [{} for _ in profiles]
    by_ruleset: dict = {}

    def snapshot() -> dict:
        for index, standing in enumerate(standings):
            standing["score"] = scores[index]
            summary = _finalise_diagnostics(diagnostics_accumulators[index])
            # Only publish the panel when an opponent actually reported search
            # decisions; a historical or mocked game without diagnostics keeps
            # the original standings shape.
            if summary.get("decisions"):
                standing["diagnostics"] = summary
            else:
                standing.pop("diagnostics", None)
        return {
            "players": profiles,
            "scores": scores,
            "standings": standings,
            "by_ruleset": by_ruleset,
            "games": results,
            "truncated": sum(r["winner"] is None for r in results),
            "average_game_length": sum(r["plies"] for r in results) / max(1, len(results)),
            "average_decision_time": sum(r["total_decision_time"] for r in results)
            / max(1, sum(r["plies"] for r in results)),
            "seed": config.get("seed", 0),
        }

    for ruleset in config.get("rulesets", ["tablut", "brandubh", "copenhagen"]):
        by_ruleset[ruleset] = {
            "wins": [0] * len(profiles),
            "draws": 0,
            "truncated": 0,
            "games": 0,
        }
        for first, second in combinations(range(len(profiles)), 2):
            for game in range(games_per_ruleset):
                if should_stop and should_stop():
                    return snapshot()
                attacker, defender = (first, second) if game % 2 == 0 else (second, first)
                # Preserve existing two-opponent comparisons; larger round robins
                # use a distinct derived seed for every scheduled match.
                game_seed = game if len(profiles) == 2 else len(results)
                try:
                    result = play_game(
                        ruleset,
                        profiles[attacker],
                        profiles[defender],
                        seed=int(config.get("seed", 0)) + game_seed,
                        max_plies=int(config.get("max_plies", 300)),
                        should_stop=should_stop,
                    )
                except InterruptedError:
                    return snapshot()
                result["attacker_player"] = attacker
                result["defender_player"] = defender
                game_diagnostics = result.get("diagnostics", {})
                for player, side in ((attacker, "attackers"), (defender, "defenders")):
                    _merge_diagnostics(
                        diagnostics_accumulators[player], game_diagnostics.get(side, {})
                    )
                for player in (attacker, defender):
                    standings[player]["games"] += 1
                by_ruleset[ruleset]["games"] += 1
                if result["winner"] == "draw":
                    for player in (attacker, defender):
                        scores[player] += 0.5
                        standings[player]["draws"] += 1
                    by_ruleset[ruleset]["draws"] += 1
                elif result["winner"] is not None:
                    winner, loser = (
                        (attacker, defender)
                        if result["winner"] == "attackers"
                        else (defender, attacker)
                    )
                    scores[winner] += 1
                    standings[winner]["wins"] += 1
                    standings[loser]["losses"] += 1
                    by_ruleset[ruleset]["wins"][winner] += 1
                else:
                    for player in (attacker, defender):
                        standings[player]["truncated"] += 1
                    by_ruleset[ruleset]["truncated"] += 1
                results.append(result)
                if report:
                    report(snapshot())
    return snapshot()
