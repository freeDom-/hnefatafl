"""Cooperative multi-game search with batched leaf inference.

One scheduler owns every independent search of a phase. A search is a generator
that yields ``(model_identity, state)`` and receives the matching evaluation, so
no simulation ever advances before its own leaf result has arrived. The caller
supplies the evaluation callback, which lets the same loop drive in-process
inference, actor-local CPU inference and parent-owned GPU inference with
identical scheduling semantics.
"""

from __future__ import annotations

from collections import deque
import time
from typing import Any, Callable, Generator, Iterable

from .engine import State


Evaluation = tuple[dict, float]
Search = Generator[tuple[int, State], Evaluation, Any]
Evaluate = Callable[[int, list[State]], list[Evaluation]]
AdvanceHook = Callable[[int, float, float], None]
CompleteHook = Callable[[int, Any], None]


def single_identity(search: Generator[State, Evaluation, Any]) -> Search:
    """Adapt a single-model search to the scheduler's ``(identity, state)`` protocol."""
    try:
        state = next(search)
        while True:
            evaluation = yield (0, state)
            state = search.send(evaluation)
    except StopIteration as finished:
        return finished.value
    finally:
        search.close()


class CooperativeSearch:
    """Interleave independent searches and batch their inference leaves.

    ``batch_size`` bounds the leaves handed to one evaluation call for a single
    model identity. The scheduler is deliberately synchronous: it evaluates
    whatever is already queued and never waits for a batch to fill, so callers
    keep control of latency. ``refill`` is invoked after each identity's batch,
    which is where a caller admits newly started games.
    """

    def __init__(
        self,
        evaluate: Evaluate,
        *,
        identities: int = 1,
        batch_size: int = 1,
        should_stop: Callable[[], bool] | None = None,
        on_advance: AdvanceHook | None = None,
        on_complete: CompleteHook | None = None,
    ):
        if type(identities) is not int or identities < 1:
            raise ValueError("Cooperative search needs at least one model identity")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("Cooperative search batch size must be a positive integer")
        self._evaluate = evaluate
        self._should_stop = should_stop
        self._on_advance = on_advance
        self._on_complete = on_complete
        self._queues: list[deque[tuple[int, State]]] = [deque() for _ in range(identities)]
        self._active: dict[int, Search] = {}
        self.batch_size = batch_size
        self.traversal_seconds = 0.0
        self.traversal_cpu_seconds = 0.0

    def __len__(self) -> int:
        return len(self._active)

    def __bool__(self) -> bool:
        return bool(self._active)

    def cancelled(self) -> None:
        if self._should_stop is not None and self._should_stop():
            raise InterruptedError("Search was cancelled")

    def start(self, index: int, search: Search) -> None:
        """Register a search and advance it to its first inference request."""
        if index in self._active:
            raise ValueError(f"Search {index} is already active")
        self._active[index] = search
        self._advance(index, None)

    def step(self, refill: Callable[[], None] | None = None) -> None:
        """Evaluate one round of queued leaves for every model identity."""
        self.cancelled()
        for identity, queue in enumerate(self._queues):
            if not queue:
                continue
            requests = [queue.popleft() for _ in range(min(self.batch_size, len(queue)))]
            values = self._evaluate(identity, [state for _, state in requests])
            for (index, _), value in zip(requests, values):
                self._advance(index, value)
            if refill is not None:
                refill()

    def run(self, refill: Callable[[], None]) -> None:
        """Drive every search to completion, admitting new games through ``refill``."""
        refill()
        while self._active:
            self.step(refill)

    def close(self) -> None:
        for search in self._active.values():
            search.close()
        self._active.clear()
        for queue in self._queues:
            queue.clear()

    def _advance(self, index: int, evaluation: Evaluation | None) -> None:
        search = self._active[index]
        self.cancelled()
        wall_started = time.monotonic()
        cpu_started = time.process_time()
        finished = False
        outcome: Any = None
        try:
            identity, state = next(search) if evaluation is None else search.send(evaluation)
        except StopIteration as stopped:
            finished = True
            outcome = stopped.value
        finally:
            wall = time.monotonic() - wall_started
            cpu = time.process_time() - cpu_started
            self.traversal_seconds += wall
            self.traversal_cpu_seconds += cpu
            if self._on_advance is not None:
                self._on_advance(index, wall, cpu)
        if finished:
            del self._active[index]
            if self._on_complete is not None:
                self._on_complete(index, outcome)
        else:
            self._queues[identity].append((index, state))


def start_all(search: CooperativeSearch, games: Iterable[tuple[int, Search]], *, limit: int) -> int:
    """Start queued games while the scheduler has spare concurrency capacity."""
    started = 0
    for index, game in games:
        if len(search) >= limit:
            break
        search.start(index, game)
        started += 1
    return started
