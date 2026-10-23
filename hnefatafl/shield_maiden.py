"""Shield maiden v1: rules-aware, CPU-only one-ply evaluation.

Geometry is immutable and cached. A decision context retains parent material,
position and repetition information. Children always come from apply_move;
geometry recognizes royal threats without expanding the opponent's move tree.
Scores use the defender perspective, with separate terminal/tactical tiers.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .engine import RULESETS, Move, State, apply_move

VERSION = 1


@dataclass(frozen=True)
class Geometry:
    rays: tuple[tuple[tuple[int, ...], ...], ...]
    adjacent: tuple[tuple[int, ...], ...]
    axes: tuple[tuple[tuple[int, int], ...], ...]
    corners: frozenset[int]
    throne: int
    distance: tuple[int, ...]
    position: tuple[float, ...]


@lru_cache(maxsize=3)
def geometry(ruleset: str) -> Geometry:
    rules = RULESETS[ruleset]
    n = rules.size
    rays = []
    adjacent = []
    axes = []
    distance = []
    position = []
    for cell in range(n * n):
        row, col = divmod(cell, n)
        directions = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ray = []
            r, c = row + dr, col + dc
            while 0 <= r < n and 0 <= c < n:
                ray.append(r * n + c)
                r, c = r + dr, c + dc
            directions.append(tuple(ray))
        rays.append(tuple(directions))
        adjacent.append(tuple(ray[0] for ray in directions if ray))
        axes.append(
            tuple(
                (directions[a][0], directions[b][0])
                for a, b in ((0, 1), (2, 3))
                if directions[a] and directions[b]
            )
        )
        edge_distance = min(row, col, n - 1 - row, n - 1 - col)
        corner_distance = min(row, n - 1 - row) + min(col, n - 1 - col)
        distance.append(edge_distance if rules.escape == "edge" else corner_distance)
        # Attackers advance off the outer rim and occupy corner approaches.
        position.append(
            5 * min(edge_distance, 2)
            + (
                160
                if ruleset == "copenhagen" and edge_distance == 0 and corner_distance in (2, 3)
                else 6
                if corner_distance <= 2
                else 0
            )
        )
    return Geometry(
        tuple(rays),
        tuple(adjacent),
        tuple(axes),
        frozenset((0, n - 1, n * (n - 1), n * n - 1)),
        n * n // 2,
        tuple(distance),
        tuple(position),
    )


def reaches(board: tuple[str, ...], cell: int, pieces: str, geo: Geometry) -> bool:
    """Can a rook of this side land on this empty ordinary square?"""
    for ray in geo.rays[cell]:
        for other in ray:
            piece = board[other]
            if piece != ".":
                if piece in pieces:
                    return True
                break
    return False


def capture_threat(board: tuple[str, ...], cell: int, ruleset: str, geo: Geometry) -> bool:
    """Exact one-move ordinary/royal capture geometry, excluding shieldwalls.

    Merely standing between enemies is safe. Only a legal *closing* landing
    counts. Empty thrones can be crossed, but soldiers cannot stop on them in
    corner-escape variants. Board edges never act as hostile supports.
    """
    piece = board[cell]
    if piece == "K" and ruleset == "copenhagen":
        adjacent = geo.adjacent[cell]
        if len(adjacent) != 4:
            return False
        missing = -1
        for other in adjacent:
            occupant = board[other]
            if occupant == "A" or (other == geo.throne and occupant == "."):
                continue
            if occupant != "." or missing != -1:
                return False
            missing = other
        return missing != -1 and reaches(board, missing, "A", geo)
    enemy = "DK" if piece == "A" else "A"
    for a, b in geo.axes[cell]:
        for support, landing in ((a, b), (b, a)):
            if board[landing] != ".":
                continue
            if ruleset != "tablut" and (landing in geo.corners or landing == geo.throne):
                # A king can close a capture on a restricted square.
                if piece != "A" or not reaches(board, landing, "K", geo):
                    continue
                movers = "K"
            else:
                movers = enemy
            hostile = ruleset != "tablut" and support in geo.corners
            if ruleset == "copenhagen" and support == geo.throne:
                hostile = piece == "A" or board[support] == "."
            if (board[support] in enemy or hostile) and reaches(board, landing, movers, geo):
                return True
    return False


def escape_routes(
    board: tuple[str, ...], king: int, ruleset: str, geo: Geometry
) -> tuple[int, float, int]:
    """Immediate escapes, clear independent approaches and legal king mobility.

    Corner routes require both a clear king ray to an edge and a clear edge
    segment to a corner. Blocked Manhattan proximity receives no route bonus.
    This is bounded ray tracing, not per-child graph pathfinding.
    """
    immediate = mobility = 0
    routes = 0.0
    for ray in geo.rays[king]:
        free = 0
        for cell in ray:
            if board[cell] != ".":
                break
            free += 1
        mobility += free
        if not ray or free != len(ray):
            continue
        if ruleset == "tablut":
            immediate += 1
            continue
        endpoint = ray[-1]
        if endpoint in geo.corners:
            immediate += 1
            continue
        # A distinct first king direction constitutes one independent approach.
        best = 0.0
        exits = 0
        for edge_ray in geo.rays[endpoint]:
            if edge_ray and edge_ray[-1] in geo.corners:
                clear = True
                for cell in edge_ray:
                    if board[cell] != "." and cell != king:
                        clear = False
                        break
                if clear:
                    exits += 1
                    # Contested entry squares are less useful, never a forced win.
                    best = max(best, 0.5 if reaches(board, endpoint, "A", geo) else 1.0)
        routes += best + (2.5 if exits == 2 else 0)
    return immediate, routes, mobility


class DecisionContext:
    def __init__(self, state: State):
        self.state = state
        self.geo = geometry(state.ruleset)
        self.king = state.board.index("K")
        self.routes = escape_routes(state.board, self.king, state.ruleset, self.geo)
        # Only the clear prefix and first blocker can change a royal ray. Once
        # an edge is reachable, its corner approaches and control rays matter.
        self.route_cells: set[int] = set()
        for ray in self.geo.rays[self.king]:
            for cell in ray:
                self.route_cells.add(cell)
                if state.board[cell] != ".":
                    break
            else:
                if ray and state.ruleset != "tablut":
                    endpoint = ray[-1]
                    for edge_ray in self.geo.rays[endpoint]:
                        if edge_ray and edge_ray[-1] in self.geo.corners:
                            self.route_cells.update(edge_ray)
                    for control_ray in self.geo.rays[endpoint]:
                        self.route_cells.update(control_ray)
        self.attackers = state.board.count("A")
        self.defenders = state.board.count("D")
        self.position = sum(-self.geo.position[i] for i, p in enumerate(state.board) if p == "A")
        self.repetitions: dict[str, int] = {}
        for key in state.history:
            self.repetitions[key] = self.repetitions.get(key, 0) + 1

    def score(self, child: State, move: Move) -> float:
        if child.winner:
            return {"defenders": 1_000_000.0, "attackers": -1_000_000.0, "draw": 0.0}[child.winner]
        board = child.board
        ruleset = child.ruleset
        geo = self.geo
        moved = self.state.board[move.source]
        king = move.target if moved == "K" else self.king
        attackers = board.count("A")
        defenders = board.count("D")
        unchanged_routes = (
            moved != "K"
            and attackers == self.attackers
            and defenders == self.defenders
            and move.source not in self.route_cells
            and move.target not in self.route_cells
        )
        immediate, routes, mobility = (
            self.routes if unchanged_routes else escape_routes(board, king, ruleset, geo)
        )
        threatened = capture_threat(board, king, ruleset, geo)
        # Threats are decisive only for the side that can execute them next.
        if child.turn == "attackers" and threatened:
            return -100_000.0 + defenders * 10 - attackers
        if child.turn == "defenders" and immediate:
            return 100_000.0 + immediate * 100 + defenders * 10 - attackers
        material = 180 * defenders - 100 * attackers
        position = self.position
        if moved == "A":
            position += geo.position[move.source] - geo.position[move.target]
        if attackers < self.attackers:
            position += sum(
                geo.position[i]
                for i, p in enumerate(self.state.board)
                if p == "A" and board[i] == "."
            )
        # A visible capture is not automatically a favorable exchange.
        safety = 0.0
        if moved != "K" and capture_threat(board, move.target, ruleset, geo):
            safety = 140.0 if moved == "A" else -240.0
        # A newly placed support can create a real closing capture next turn.
        # Inspect just its neighbors, not every piece or every legal reply.
        opportunities = 0.0
        enemy = "DK" if moved == "A" else "A"
        for cell in geo.adjacent[move.target]:
            victim = board[cell]
            if victim in enemy and victim != "K" and capture_threat(board, cell, ruleset, geo):
                opportunities += 20 if victim == "A" else -36
        adjacent_attackers = sum(board[i] == "A" for i in geo.adjacent[king])
        pressure = (16 if ruleset == "copenhagen" else 30) * adjacent_attackers
        # Active royal movement, independent approaches and safe coordination.
        royal = (
            32 * (child.size - 1 - geo.distance[king])
            + 300 * routes
            + (35 if ruleset == "copenhagen" else 5) * mobility
        )
        royal += 650 * immediate - (180 if threatened else 0)
        protection = 14 * sum(board[i] == "D" for i in geo.adjacent[king])
        score = material + position + safety + opportunities + royal + protection - pressure
        # Repeated positions lose for Copenhagen defenders; discourage cycles in
        # the mover's perspective without changing engine repetition adjudication.
        if self.repetitions.get(child.history[-1], 0):
            score += -24 if self.state.turn == "defenders" else 24
        return score


def choose(
    state: State,
    moves: list[Move],
    rng: random.Random,
    deadline: float,
    *,
    diagnostics: dict | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    context = DecisionContext(state)
    sign = 1 if state.turn == "defenders" else -1
    rng.shuffle(moves)
    best, score = moves[0], -math.inf
    searched = 0
    for move in moves:
        if should_stop and should_stop():
            raise InterruptedError("Search cancelled")
        if searched and time.monotonic() >= deadline:
            break
        child = apply_move(state, move)
        value = sign * context.score(child, move)
        searched += 1
        if value > score:
            best, score = move, value
        # An exact engine win dominates every static/tactical score.
        if score == 1_000_000:
            break
    if diagnostics is not None:
        raw = sign * score
        value = (
            (1.0 if raw > 0 else -1.0)
            if abs(raw) == 1_000_000
            else max(-0.99, min(0.99, math.tanh(raw / 1800)))
        )
        diagnostics.update(
            value=value,
            completed_depth=int(searched == len(moves) or score == 1_000_000),
            searched_moves=searched,
        )
    return best
