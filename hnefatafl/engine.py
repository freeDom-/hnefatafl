"""Pure, deterministic tafl rules, independent of web, storage, and AI.

Coordinates are zero-based, row-major indices. Each successful move returns a
new immutable state. See docs/rules for the exact source-defined variants.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Iterable


@dataclass(frozen=True)
class Ruleset:
    id: str
    name: str
    size: int
    first_player: str
    escape: str
    king_capture: str
    restricted_throne: bool
    hostile_throne: bool
    restricted_corners: bool
    shieldwall: bool = False
    encirclement: bool = False
    edge_fort: bool = False
    repetition: str = "draw"
    version: str = "1"


RULESETS = {
    "tablut": Ruleset(
        "tablut", "Tablut (market rules)", 9, "defenders", "edge", "sandwich", False, False, False
    ),
    "brandubh": Ruleset(
        "brandubh", "Brandubh", 7, "attackers", "corner", "sandwich", True, False, True
    ),
    "copenhagen": Ruleset(
        "copenhagen",
        "Copenhagen Hnefatafl",
        11,
        "attackers",
        "corner",
        "surround",
        True,
        True,
        True,
        shieldwall=True,
        encirclement=True,
        edge_fort=True,
        repetition="defenders_lose",
    ),
}


@dataclass(frozen=True, order=True)
class Move:
    source: int
    target: int

    def to_dict(self) -> dict[str, int]:
        return {"source": self.source, "target": self.target}


@dataclass(frozen=True)
class State:
    ruleset: str
    board: tuple[str, ...]
    turn: str = "attackers"
    ply: int = 0
    winner: str | None = None
    reason: str | None = None
    history: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.ruleset not in RULESETS:
            raise ValueError("Unknown ruleset")
        if len(self.board) != self.size * self.size or any(
            p not in ".ADK" or len(p) != 1 for p in self.board
        ):
            raise ValueError("Invalid board")
        if self.turn not in ("attackers", "defenders"):
            raise ValueError("Invalid player to move")
        if type(self.ply) is not int or self.ply < 0:
            raise ValueError("Invalid ply")
        if self.winner not in (None, "attackers", "defenders", "draw"):
            raise ValueError("Invalid winner")
        kings = self.board.count("K")
        if kings != 1 and not (kings == 0 and self.winner == "attackers"):
            raise ValueError("A state must contain exactly one king until his capture")

    @property
    def size(self) -> int:
        return RULESETS[self.ruleset].size

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleset": self.ruleset,
            "board": list(self.board),
            "turn": self.turn,
            "ply": self.ply,
            "winner": self.winner,
            "reason": self.reason,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        if not isinstance(data, dict):
            raise ValueError("State must be an object")
        try:
            history = data.get("history", [])
            if not isinstance(history, (list, tuple)) or any(
                not isinstance(k, str) for k in history
            ):
                raise ValueError("Invalid position history")
            return cls(
                ruleset=data["ruleset"],
                board=tuple(data["board"]),
                turn=data.get("turn", "attackers"),
                ply=data.get("ply", 0),
                winner=data.get("winner"),
                reason=data.get("reason"),
                history=tuple(history),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid serialized state") from exc


def _side(piece: str) -> str | None:
    return "attackers" if piece == "A" else "defenders" if piece in ("D", "K") else None


def _opponent(side: str) -> str:
    return "defenders" if side == "attackers" else "attackers"


def _key(board: Iterable[str], turn: str) -> str:
    return turn + ":" + "".join(board)


def _corners(size: int) -> frozenset[int]:
    return frozenset((0, size - 1, size * (size - 1), size * size - 1))


def _neighbors(index: int, size: int) -> list[int]:
    row, col = divmod(index, size)
    return [
        r * size + c
        for r, c in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
        if 0 <= r < size and 0 <= c < size
    ]


def _edge(index: int, size: int) -> bool:
    row, col = divmod(index, size)
    return row in (0, size - 1) or col in (0, size - 1)


def new_game(ruleset: str = "tablut") -> State:
    if ruleset not in RULESETS:
        raise ValueError("Unknown ruleset")
    rules = RULESETS[ruleset]
    n = rules.size
    c = n // 2
    board = ["."] * (n * n)
    board[c * n + c] = "K"

    def put_rotations(row: int, col: int, piece: str) -> None:
        for _ in range(4):
            board[row * n + col] = piece
            row, col = col, n - 1 - row

    if ruleset == "brandubh":
        put_rotations(0, c, "A")
        put_rotations(1, c, "A")
        put_rotations(c - 1, c, "D")
    elif ruleset == "tablut":
        for col in (c - 1, c, c + 1):
            put_rotations(0, col, "A")
        put_rotations(1, c, "A")
        put_rotations(c - 1, c, "D")
        put_rotations(c - 2, c, "D")
    else:
        for col in range(c - 2, c + 3):
            put_rotations(0, col, "A")
        put_rotations(1, c, "A")
        put_rotations(c - 1, c, "D")
        put_rotations(c - 2, c, "D")
        put_rotations(c - 1, c - 1, "D")
    turn = rules.first_player
    return State(ruleset, tuple(board), turn, history=(_key(board, turn),))


def _piece_moves(state: State, source: int) -> list[Move]:
    rules = RULESETS[state.ruleset]
    n = state.size
    piece = state.board[source]
    row, col = divmod(source, n)
    throne = n * n // 2
    corners = _corners(n)
    moves: list[Move] = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        r, c = row + dr, col + dc
        while 0 <= r < n and 0 <= c < n:
            target = r * n + c
            if state.board[target] != ".":
                break
            forbidden = piece != "K" and (
                (rules.restricted_throne and target == throne)
                or (rules.restricted_corners and target in corners)
            )
            if not forbidden:
                moves.append(Move(source, target))
            # Empty throne may be crossed, even by an ordinary soldier.
            r, c = r + dr, c + dc
    return moves


def legal_moves(state: State) -> list[Move]:
    if state.winner is not None:
        return []
    return [
        move
        for source, piece in enumerate(state.board)
        if _side(piece) == state.turn
        for move in _piece_moves(state, source)
    ]


def _valid_move(state: State, move: Move) -> bool:
    if not isinstance(move, Move) or type(move.source) is not int or type(move.target) is not int:
        return False
    if state.winner is not None or not (
        0 <= move.source < len(state.board) and 0 <= move.target < len(state.board)
    ):
        return False
    if _side(state.board[move.source]) != state.turn:
        return False
    return move in _piece_moves(state, move.source)


def _hostile(board: list[str], index: int, victim: str, rules: Ruleset) -> bool:
    if rules.restricted_corners and index in _corners(rules.size):
        return True
    if rules.hostile_throne and index == rules.size * rules.size // 2:
        # The occupied throne is still hostile to attackers; only an EMPTY
        # throne is hostile to defenders in Copenhagen.
        return victim == "A" or board[index] == "."
    return False


def _normal_captures(board: list[str], target: int, side: str, rules: Ruleset) -> set[int]:
    captures: set[int] = set()
    n = rules.size
    row, col = divmod(target, n)
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        r1, c1, r2, c2 = row + dr, col + dc, row + 2 * dr, col + 2 * dc
        if not (0 <= r1 < n and 0 <= c1 < n and 0 <= r2 < n and 0 <= c2 < n):
            continue
        victim, far = r1 * n + c1, r2 * n + c2
        if _side(board[victim]) != _opponent(side):
            continue
        if board[victim] == "K" and rules.king_capture != "sandwich":
            continue
        if _side(board[far]) == side or _hostile(board, far, board[victim], rules):
            captures.add(victim)
    return captures


def _shieldwall_captures(board: list[str], target: int, side: str, rules: Ruleset) -> set[int]:
    n = rules.size
    captures: set[int] = set()
    edges = [
        (list(range(n)), list(range(n, 2 * n))),
        (list(range(n * (n - 1), n * n)), list(range(n * (n - 2), n * (n - 1)))),
        ([r * n for r in range(n)], [r * n + 1 for r in range(n)]),
        ([r * n + n - 1 for r in range(n)], [r * n + n - 2 for r in range(n)]),
    ]
    for edge, inner in edges:
        i = 0
        while i < n:
            if _side(board[edge[i]]) != _opponent(side):
                i += 1
                continue
            start = i
            while i < n and _side(board[edge[i]]) == _opponent(side):
                i += 1
            end = i
            if end - start < 2 or start == 0 or end == n:
                continue
            left, right = edge[start - 1], edge[end]

            def bracket(index: int) -> bool:
                return _side(board[index]) == side or (index in _corners(n) and board[index] == ".")

            back = inner[start:end]
            if bracket(left) and bracket(right) and all(_side(board[x]) == side for x in back):
                if target == -1 or target in {left, right, *back}:
                    captures.update(x for x in edge[start:end] if board[x] != "K")
    return captures


def _encircled(board: list[str], n: int) -> bool:
    defenders = {i for i, piece in enumerate(board) if piece in ("D", "K")}
    if not defenders:
        return False
    # A flood from every board edge finds all defender-accessible exits. An
    # attacker is a wall; an empty square or defender can belong to an interior.
    reachable = {i for i in range(n * n) if _edge(i, n) and board[i] != "A"}
    queue = deque(reachable)
    while queue:
        for nxt in _neighbors(queue.popleft(), n):
            if nxt not in reachable and board[nxt] != "A":
                reachable.add(nxt)
                queue.append(nxt)
    return not bool(defenders & reachable)


def _edge_fort(board: list[str], n: int) -> bool:
    """Recognize sealed, structurally unbreakable Copenhagen exit forts.

    Flood the maximal attacker-accessible exterior, treating their own men as
    traversable. A wall is permanent only if none of its men can be sandwiched
    from that exterior, including captures against a hostile special square.
    This is a structural proof, not a time-limited AI search.
    """
    king = board.index("K")
    if not _edge(king, n):
        return False
    pocket = {king}
    queue = deque(pocket)
    while queue:
        for nxt in _neighbors(queue.popleft(), n):
            if nxt not in pocket and board[nxt] in (".", "K"):
                pocket.add(nxt)
                queue.append(nxt)
    if not any(board[x] == "." for x in _neighbors(king, n)):
        return False
    if not any(board[x] == "D" for cell in pocket for x in _neighbors(cell, n)):
        return False
    # Peel every potentially capturable wall man and enlarge the exterior.
    # This also handles thick or irregular walls, and remote defenders that
    # would otherwise incorrectly appear to protect a breakable inner wall.
    working = board.copy()
    hostile = _corners(n) | {n * n // 2}
    while True:
        exterior = {i for i, piece in enumerate(working) if piece == "A"}
        queue = deque(exterior)
        while queue:
            for nxt in _neighbors(queue.popleft(), n):
                if nxt not in exterior and working[nxt] not in ("D", "K"):
                    exterior.add(nxt)
                    queue.append(nxt)
        if exterior & pocket:
            return False
        removable = set()
        for cell, piece in enumerate(working):
            if piece != "D":
                continue
            row, col = divmod(cell, n)
            for dr, dc in ((1, 0), (0, 1)):
                r1, c1, r2, c2 = row + dr, col + dc, row - dr, col - dc
                if 0 <= r1 < n and 0 <= c1 < n and 0 <= r2 < n and 0 <= c2 < n:
                    a, b = r1 * n + c1, r2 * n + c2
                    if (a in exterior and (b in exterior or b in hostile)) or (
                        b in exterior and a in hostile
                    ):
                        removable.add(cell)
        saturated = [
            "A" if i in exterior and i not in hostile else piece for i, piece in enumerate(working)
        ]
        removable |= _shieldwall_captures(saturated, -1, "attackers", RULESETS["copenhagen"])
        if not removable:
            return True
        for cell in removable:
            working[cell] = "."


def apply_move(state: State, move: Move) -> State:
    if not _valid_move(state, move):
        raise ValueError("Illegal move")
    rules = RULESETS[state.ruleset]
    n = state.size
    board = list(state.board)
    board[move.target], board[move.source] = board[move.source], "."
    captured = _normal_captures(board, move.target, state.turn, rules)
    if rules.shieldwall:
        captured |= _shieldwall_captures(board, move.target, state.turn, rules)
    winner = reason = None
    if any(board[index] == "K" for index in captured):
        winner, reason = "attackers", "king_captured"
    for index in captured:
        board[index] = "."
    if winner is None and rules.king_capture == "surround" and state.turn == "attackers":
        king = board.index("K")
        adjacent = _neighbors(king, n)
        if (
            len(adjacent) == 4
            and move.target in adjacent
            and all(board[x] == "A" or (x == n * n // 2 and board[x] == ".") for x in adjacent)
        ):
            board[king] = "."
            winner, reason = "attackers", "king_captured"
    if winner is None:
        king = board.index("K")
        if (rules.escape == "edge" and _edge(king, n)) or (
            rules.escape == "corner" and king in _corners(n)
        ):
            winner, reason = "defenders", "king_escaped"
        elif rules.encirclement and _encircled(board, n):
            winner, reason = "attackers", "encirclement"
        elif rules.edge_fort and _edge_fort(board, n):
            winner, reason = "defenders", "edge_fort"
    turn = _opponent(state.turn)
    history = state.history or (_key(state.board, state.turn),)
    key = _key(board, turn)
    history += (key,)
    if winner is None and history.count(key) >= 3:
        winner = "attackers" if rules.repetition == "defenders_lose" else "draw"
        reason = "threefold_repetition"
    result = State(state.ruleset, tuple(board), turn, state.ply + 1, winner, reason, history)
    if winner is None and not legal_moves(result):
        if state.ruleset == "copenhagen":
            return replace(result, winner=state.turn, reason="no_legal_moves")
        return replace(result, winner="draw", reason="no_legal_moves")
    return result


def replay(ruleset: str, moves: Iterable[Move | dict[str, int]]) -> State:
    state = new_game(ruleset)
    for move in moves:
        if isinstance(move, dict):
            try:
                move = Move(source=move["source"], target=move["target"])
            except (KeyError, TypeError) as exc:
                raise ValueError("Invalid replay move") from exc
        state = apply_move(state, move)
    return state
