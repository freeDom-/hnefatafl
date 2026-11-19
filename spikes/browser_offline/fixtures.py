"""Build CPython reference vectors, using the authoritative engine unchanged."""

from dataclasses import replace

from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves, new_game


def snapshot(state: State) -> dict:
    return {"state": state.to_dict(), "legal_moves": [m.to_dict() for m in legal_moves(state)]}


def vectors() -> list[dict]:
    cases = []

    def add(name: str, state: State, move: Move) -> None:
        cases.append(
            {
                "name": name,
                "before": snapshot(state),
                "move": move.to_dict(),
                "after": snapshot(apply_move(state, move)),
            }
        )

    def position(rule: str, pieces: dict, turn="attackers") -> State:
        n = RULESETS[rule].size
        board = ["."] * (n * n)
        for (row, col), piece in pieces.items():
            board[row * n + col] = piece
        return State(rule, tuple(board), turn)

    for rule, rules in RULESETS.items():
        state = new_game(rule)
        for step in range(8):
            selected = legal_moves(state)[step % len(legal_moves(state))]
            add(f"{rule}-opening-{step}", state, selected)
            state = apply_move(state, selected)
            if state.winner:
                break
        n = rules.size
        state = position(
            rule,
            {
                (5, 2): "A",
                (2, 1): "D",
                (2, 3): "D",
                (1, 2): "D",
                (2, 0): "A",
                (2, 4): "A",
                (0, 2): "A",
                (4, 4): "K",
            },
        )
        add(f"{rule}-three-captures", state, Move(5 * n + 2, 2 * n + 2))
        state = position(rule, {(0, 2): "K", (4, 4): "A"}, "defenders")
        add(f"{rule}-king-escape", state, Move(2, 0))
        state = new_game(rule)
        move = legal_moves(state)[0]
        key = apply_move(state, move).history[-1]
        add(f"{rule}-repetition", replace(state, history=(key, key)), move)

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
            (5, 5): "K",
            (9, 9): "D",
            (3, 2): "A",
        },
    )
    add("copenhagen-shieldwall", state, Move(35, 2))
    return cases


def _sparse(rule: str, pieces: dict, turn: str) -> State:
    n = RULESETS[rule].size
    board = ["."] * (n * n)
    for (row, col), piece in pieces.items():
        board[row * n + col] = piece
    return State(rule, tuple(board), turn)


def jarl_positions() -> list[dict]:
    """Deterministic analysis positions for the native/browser Jarl benchmark.

    Covers opening, midgame, endgame, tactical, king-escape, repetition-sensitive
    and terminal states for all three rulesets. These are serialized positions,
    never time-limited strength claims; the runner records whether each search
    completed. The same list is staged for the browser worker and read by the
    native benchmark so both compare identical workloads.
    """
    cases: list[dict] = []

    def add(rule: str, kind: str, state: State) -> None:
        cases.append(
            {
                "name": f"{rule}-{kind}",
                "ruleset": rule,
                "kind": kind,
                "state": state.to_dict(),
            }
        )

    for rule, rules in RULESETS.items():
        n = rules.size
        center = n // 2
        add(rule, "opening", new_game(rule))
        state = new_game(rule)
        for step in range(6):
            moves = legal_moves(state)
            if not moves or state.winner:
                break
            state = apply_move(state, moves[step % len(moves)])
        if not state.winner:
            add(rule, "midgame", state)
        add(
            rule,
            "endgame",
            _sparse(
                rule,
                {
                    (center, center): "K",
                    (center, center - 1): "D",
                    (center - 1, center): "D",
                    (0, center): "A",
                    (n - 1, center): "A",
                    (center, 0): "A",
                },
                "attackers",
            ),
        )
        add(
            rule,
            "tactical",
            _sparse(
                rule,
                {
                    (5, 2): "A",
                    (2, 1): "D",
                    (2, 3): "D",
                    (1, 2): "D",
                    (2, 0): "A",
                    (2, 4): "A",
                    (0, 2): "A",
                    (4, 4): "K",
                },
                "attackers",
            ),
        )
        escape = _sparse(rule, {(0, 2): "K", (4, 4): "A"}, "defenders")
        add(rule, "king-escape", escape)
        add(rule, "terminal", apply_move(escape, Move(2, 0)))
        fresh = new_game(rule)
        move = legal_moves(fresh)[0]
        key = apply_move(fresh, move).history[-1]
        add(rule, "repetition", replace(fresh, history=(key, key)))
    return cases


def search_vectors() -> list[dict]:
    """Completed one-ply tactical references, never time-limited strength claims."""
    from hnefatafl.ai import analyze_position

    config = {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.5, "seed": 7}
    return [
        {
            "name": case["name"],
            "state": case["before"]["state"],
            "config": config,
            "analysis": analyze_position(State.from_dict(case["before"]["state"]), config),
        }
        for case in vectors()
        if case["name"].endswith("-king-escape")
    ]
