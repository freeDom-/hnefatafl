"""Spawned CPU search actors driven by one parent-owned neural inference service.

``search_workers`` sizes a persistent pool of CPU search processes and never
bounds game concurrency. ``self_play_concurrency`` and ``evaluation_concurrency``
do that independently: each actor multiplexes many game contexts through the same
cooperative scheduler the in-process paths use, so a small worker pool still keeps
a large number of games in flight and still fills inference batches.

CUDA/OpenVINO models, tensors, streams and caches stay in the parent. CPU actors
bind immutable read-only weight snapshots and evaluate their own batches, which
keeps every leaf out of inter-process communication unless parent-owned inference
is requested explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
import multiprocessing as mp
from multiprocessing.connection import Connection, wait
import time
from typing import Any, Callable, Generator, cast

import torch

from .cooperative import CooperativeSearch, single_identity
from .engine import State
from .inference import ExactInferenceCache, InferenceMetrics, policy_value_batch


#: Bounded window in which the parent waits for sibling actors to deliver into the
#: same forward pass. It is not a fixed batching delay: the parent waits only while
#: another live actor is still expected to deliver, and stops the moment it does.
#: 8 ms is the measured optimum on a 64-game Brandubh RTX 3060 workload, where a
#: 1 ms window left batch occupancy at 14.5 of 64 and a 20 ms window over-waited.
DEFAULT_FLUSH_SECONDS = 0.008


def plan_batches(
    groups: dict[Any, list[State]], order: list[Any], limit: int
) -> list[tuple[list[State], list[tuple[Any, int]]]]:
    """Split grouped requests into forward passes of at most ``limit`` states.

    Requests keep their own grouping: each returned pass carries the ``(key,
    size)`` spans it covers, so a request that spans several passes still
    receives exactly one reply and no state is ever attributed to another game.
    """
    batches: list[tuple[list[State], list[tuple[Any, int]]]] = []
    batch: list[State] = []
    spans: list[tuple[Any, int]] = []
    for key in order:
        states = groups[key]
        for start in range(0, len(states), limit):
            piece = states[start : start + limit]
            if len(batch) + len(piece) > limit:
                batches.append((batch, spans))
                batch, spans = [], []
            batch.extend(piece)
            spans.append((key, len(piece)))
    if batch:
        batches.append((batch, spans))
    return batches


def _close_owned(owned: ExitStack, error: BaseException | None = None) -> None:
    """Close every resource registered on ``owned``, exactly once.

    A failing close never replaces the error that triggered cleanup: it is
    attached to that error as a note instead, so both the original failure and
    the cleanup failure stay visible. On the success path there is nothing to
    preserve and a failing close propagates normally.
    """
    if error is None:
        owned.close()
        return
    try:
        owned.close()
    except BaseException as cleanup:
        error.add_note(f"Closing an inference backend also failed: {cleanup!r}")


def _game_factory(
    kind: str,
    config: dict,
    context: dict | None,
    seed: int,
    identities: int,
    cancelled: Callable[[], bool],
):
    """Return a per-index factory for the search generators a phase needs."""
    if kind == "self_play":
        from .self_play import _game_context

        def self_play(index: int):
            return single_identity(_game_context(None, config, seed + index, cancelled))

        return self_play

    from .evaluation_play import game_context

    def evaluation(index: int):
        # Actor-owned states only; ``models`` is a placeholder whose length
        # selects whether the opponent also searches neurally.
        return game_context(
            index, config, context or {}, [None] * identities, seed, cancelled, neural=True
        )

    return evaluation


def _search(
    connection: Connection,
    spec: dict,
    snapshots: list,
    caches: list,
    cancelled: Callable[[], bool],
    remote: Callable[[int, list[State]], list],
) -> None:
    """Drive one worker's assigned games and report each completed game."""
    kind = spec["kind"]
    config = spec["config"]
    indices = spec["indices"]
    identities = spec["identities"]
    local = bool(snapshots)
    if not local:
        # Parent-owned inference keeps every cache in the parent; the actor only
        # reports counters it actually produced.
        caches = [None] * identities
    metrics = [InferenceMetrics() for _ in range(identities)]
    reported_entries = [0] * identities
    reported_peak = [0] * identities
    per_game_cpu: dict[int, float] = {}

    def take_metrics() -> list[InferenceMetrics]:
        """Hand over activity since the previous report and start a fresh window."""
        nonlocal metrics
        sent, metrics = metrics, [InferenceMetrics() for _ in range(identities)]
        for identity, metric in enumerate(sent):
            # Cache sizes live on the cache itself, not on a window that may have
            # evaluated nothing; report them as increments so the parent can sum
            # the live caches of every actor without double counting.
            cache = caches[identity]
            current = len(cache.entries) if cache is not None else 0
            increment = current - reported_entries[identity]
            if increment < 0:
                # A model update invalidated the cache; report its new contents.
                increment = current
            reported_entries[identity] += increment
            metric.cache_entries = increment
            peak = max(reported_peak[identity], current)
            metric.cache_peak_entries = peak - reported_peak[identity]
            reported_peak[identity] = peak
        return sent

    def evaluate(identity: int, states: list[State]):
        if local:
            return policy_value_batch(
                states, snapshots[identity], metrics=metrics[identity], cache=caches[identity]
            )
        return remote(identity, states)

    def completed(index: int, value: Any) -> None:
        if kind == "self_play":
            examples, result = value
            # Plain NumPy payloads avoid shared-tensor file-descriptor lifetimes
            # after an actor has exited or a pool has been cancelled.
            for row in examples:
                row["input"] = row["input"].numpy()
            result["cpu_search_seconds"] = per_game_cpu.pop(index, 0.0)
            payload: Any = (examples, result)
        else:
            payload = value
        connection.send(("done", index, payload, take_metrics()))

    def record_traversal(index: int, wall: float, cpu: float) -> None:
        per_game_cpu[index] = per_game_cpu.get(index, 0.0) + cpu

    search = CooperativeSearch(
        evaluate,
        identities=identities,
        batch_size=spec["batch_size"],
        should_stop=cancelled,
        on_advance=record_traversal,
        on_complete=completed,
    )
    create = _game_factory(kind, config, spec.get("context"), spec["seed"], identities, cancelled)
    started = 0
    peak_active = 0

    def refill() -> None:
        nonlocal started, peak_active
        while len(search) < spec["concurrency"] and started < len(indices):
            index = indices[started]
            started += 1
            search.start(index, create(index))
        peak_active = max(peak_active, len(search))

    try:
        search.run(refill)
    finally:
        search.close()
    connection.send(("finished", take_metrics(), peak_active))


def _actor(connection: Connection, cancel) -> None:
    """Own one worker's games; never touch CUDA, which belongs to the parent."""
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    snapshots: list = []
    caches: list = []
    token = 0

    def cancelled() -> bool:
        return cancel.is_set()

    def bind(message) -> None:
        nonlocal snapshots, caches
        from .models import PolicyValueNet

        mode, capacity = message[2], message[3]
        snapshots = []
        caches = []
        for architecture, weights in message[1]:
            selected = PolicyValueNet(**architecture).eval().requires_grad_(False)
            selected.load_state_dict(
                {key: torch.from_numpy(value) for key, value in weights.items()}
            )
            snapshots.append(selected)
            caches.append(ExactInferenceCache(capacity) if mode == "exact" else None)

    def remote(identity: int, states: list[State]):
        nonlocal token
        token += 1
        connection.send(("leaves", token, identity, states))
        while not connection.poll(0.05):
            if cancelled():
                raise InterruptedError("Search was cancelled")
        reply = connection.recv()
        if reply[0] != "values" or reply[1] != token:
            raise RuntimeError("Search actor received a mismatched inference reply")
        return reply[2]

    try:
        while not cancelled():
            task = connection.recv()
            if task is None:
                return
            if task[0] == "bind":
                bind(task)
                connection.send(("bound",))
                continue
            _search(connection, task[1], snapshots, caches, cancelled, remote)
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as error:
        if not cancelled():
            try:
                connection.send(("error", f"{type(error).__name__}: {error}"))
            except (BrokenPipeError, EOFError, OSError):
                pass
    finally:
        connection.close()


class SearchPool:
    """A persistent pool of CPU search actors sharing one inference owner.

    The pool outlives a single phase, so training iterations and evaluation
    phases reuse it and process startup is amortised. ``workers`` bounds CPU
    parallelism only; every call decides its own game concurrency, batch limits
    and per-worker share.
    """

    def __init__(
        self,
        workers: int,
        *,
        inference: str = "auto",
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
    ):
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError("search_workers must be between 1 and 32")
        if inference not in ("auto", "actors", "parent"):
            raise ValueError("Search inference must be auto, actors or parent")
        if not isinstance(flush_seconds, (int, float)) or not 0.0 <= flush_seconds <= 0.5:
            raise ValueError("Search flush window must be between 0 and 0.5 seconds")
        self.inference = inference
        self.flush_seconds = float(flush_seconds)
        started = time.monotonic()
        context = mp.get_context("spawn")
        self.cancel = context.Event()
        self.connections: list[Connection] = []
        self.processes: list[Any] = []
        self.performance: dict = {}
        self.inference_metrics: list[InferenceMetrics] = []
        self.closed = False
        try:
            for _ in range(workers):
                parent, child = context.Pipe()
                process = context.Process(target=_actor, args=(child, self.cancel), daemon=True)
                process.start()
                child.close()
                self.connections.append(parent)
                self.processes.append(process)
        except BaseException:
            self.close()
            raise
        self.initialization_seconds = time.monotonic() - started

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel.set()
        for connection in self.connections:
            connection.close()
        for process in self.processes:
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _open_phase(
        self,
        owned: ExitStack,
        kind: str,
        models: list,
        config: dict,
        backend,
        limit: int,
        concurrency: int,
        count: int,
    ):
        """Resolve the inference owner and register every resource it creates.

        Backends are registered on ``owned`` before they are refreshed, so a
        failure anywhere in this method leaves the caller's stack holding exactly
        the resources that were created and still need closing. The caller's
        phase cleanup closes them once.
        """
        backends: list = [backend if kind == "self_play" else None for _ in models]
        if (
            kind == "evaluation"
            and config.get("evaluation_inference_backend") == "training"
            and config.get("inference_backend") == "openvino"
        ):
            from .inference_backends import create_inference

            for index, selected in enumerate(models):
                graph = create_inference(
                    selected,
                    {
                        **config,
                        "games": count,
                        "self_play_concurrency": concurrency,
                        "inference_batch_size": limit,
                    },
                )
                owned.callback(graph.close)
                graph.refresh()
                backends[index] = graph
        cpu_models = all(next(selected.parameters()).device.type == "cpu" for selected in models)
        torch_backends = all(
            graph is None or type(graph).__name__ == "TorchInference" for graph in backends
        )
        actors = self.inference == "actors" or (
            self.inference == "auto" and cpu_models and torch_backends
        )
        if actors and not (cpu_models and torch_backends):
            raise ValueError(
                "Actor-local search inference needs CPU models without an external backend"
            )
        snapshots = (
            [
                (
                    selected.config,
                    {key: tensor.detach().numpy() for key, tensor in selected.state_dict().items()},
                )
                for selected in models
            ]
            if actors
            else []
        )
        caches = [
            ExactInferenceCache(config["inference_cache_capacity"])
            if config.get("inference_cache_mode") == "exact"
            else None
            for _ in models
        ]
        return backends, snapshots, caches, actors

    def games(
        self,
        kind: str,
        model,
        config: dict,
        *,
        seed: int,
        context=None,
        previous=None,
        backend=None,
        should_stop: Callable | None = None,
        measure_cuda: bool = False,
        committed: Callable[[], int] | None = None,
    ) -> Generator[tuple[int, Any], None, None]:
        """Run one phase, yielding ``(index, result)`` in completion order.

        ``committed`` reports how many leading games the caller has already
        committed. When supplied, the pool hands out new games only while the
        started-but-uncommitted count stays inside the ordered replay window, so
        a slow early game cannot retain an iteration's worth of examples.
        """
        if self.closed:
            raise RuntimeError("Search pool is closed")
        models = [model] + ([previous] if previous is not None else [])
        for selected in models:
            selected.eval()
        count = config["games"] if kind == "self_play" else config["evaluation_games"]
        limit = (
            config.get("inference_batch_size", 32)
            if kind == "self_play"
            else config.get("evaluation_batch_size", 32)
        )
        concurrency = min(
            count,
            config.get(
                "self_play_concurrency" if kind == "self_play" else "evaluation_concurrency", 16
            ),
        )
        workers = min(len(self.processes), count)
        # Hard global bound: split the requested concurrency into a quotient and
        # a remainder so the capacities sum to exactly ``concurrency``. A worker
        # with no slot is never started, which keeps the bound when concurrency
        # is smaller than the pool or does not divide evenly by it.
        base, extra = divmod(concurrency, workers)
        capacities = [base + (1 if worker < extra else 0) for worker in range(workers)]
        active = [worker for worker, capacity in enumerate(capacities) if capacity]
        # Round-robin keeps every worker's game mix representative and, unlike a
        # work-stealing queue, keeps batch composition reproducible across runs.
        assignments = [
            [index for index in range(count) if index % len(active) == slot]
            for slot in range(len(active))
        ]
        window = count if committed is None else 2 * concurrency
        slice_sizes = [max(capacities[worker], -(-window // len(active))) for worker in active]
        used = [self.connections[worker] for worker in active]
        owner = {connection: slot for slot, connection in enumerate(used)}
        cursors = [0] * len(active)
        granted = 0
        peak_active = [0] * len(active)
        owned = ExitStack()
        try:
            backends, snapshots, caches, actors = self._open_phase(
                owned, kind, models, config, backend, limit, concurrency, count
            )
            self.inference_metrics = [
                InferenceMetrics(
                    track_cuda=measure_cuda and next(selected.parameters()).device.type == "cuda"
                )
                for selected in models
            ]
        except BaseException as error:
            # Phase setup owns whatever it created, even if it never reaches the
            # execution loop below.
            _close_owned(owned, error)
            raise
        # Only the running count is kept: a completed game is handed to the
        # caller immediately, so the pool never holds a second copy of every
        # iteration's examples. ``streamed`` is bounded by one flush batch.
        completed = 0
        streamed: list[tuple[int, Any]] = []
        pools: dict[int, list[tuple[Connection, int, list[State]]]] = defaultdict(list)
        awaiting: set[Connection] = set()
        busy: set[Connection] = set()
        idle: set[Connection] = set()

        def merge(deltas: list[InferenceMetrics]) -> None:
            for identity, delta in enumerate(deltas):
                self.inference_metrics[identity].add(delta)

        def handle(connection: Connection, message) -> None:
            nonlocal completed
            if message[0] == "leaves":
                pools[message[2]].append((connection, message[1], message[3]))
                awaiting.add(connection)
            elif message[0] == "done":
                result = message[2]
                if kind == "self_play":
                    for row in result[0]:
                        row["input"] = torch.from_numpy(row["input"])
                completed += 1
                streamed.append((message[1], result))
                merge(message[3])
            elif message[0] == "finished":
                busy.discard(connection)
                idle.add(connection)
                peak_active[owner[connection]] = max(peak_active[owner[connection]], message[2])
                merge(message[1])
            else:
                raise RuntimeError(f"Search actor failed: {message[1]}")

        def drain(connection: Connection) -> None:
            while True:
                try:
                    message = connection.recv()
                except EOFError as error:
                    raise RuntimeError("Search actor connection closed") from error
                handle(connection, message)
                if not connection.poll(0):
                    return

        def flush() -> None:
            """Evaluate every queued request, batching across the worker pool."""
            for identity, items in pools.items():
                if not items:
                    continue
                # Group by requester first: a request split across several
                # forward passes must still receive exactly one reply.
                groups: dict[tuple[Connection, int], list[State]] = {}
                order: list[tuple[Connection, int]] = []
                for connection, token, states in items:
                    key = (connection, token)
                    if key not in groups:
                        groups[key] = []
                        order.append(key)
                    groups[key].extend(states)
                values: dict[tuple[Connection, int], list] = {key: [] for key in order}
                for batch, spans in plan_batches(groups, order, limit):
                    evaluated = policy_value_batch(
                        batch,
                        models[identity],
                        mixed_precision=kind == "self_play" and bool(config["mixed_precision"]),
                        metrics=self.inference_metrics[identity],
                        backend=backends[identity],
                        cache=caches[identity],
                    )
                    offset = 0
                    for key, size in spans:
                        values[key].extend(evaluated[offset : offset + size])
                        offset += size
                for key in order:
                    connection, token = key
                    connection.send(("values", token, values[key]))
                    awaiting.discard(connection)
            pools.clear()

        def grant(connection: Connection) -> bool:
            """Hand out this worker's next slice while the replay window allows."""
            nonlocal granted
            slot = owner[connection]
            share = assignments[slot]
            cursor = cursors[slot]
            if cursor >= len(share):
                return False
            allowance = min(slice_sizes[slot], len(share) - cursor)
            if committed is not None:
                allowance = min(allowance, window - (granted - committed()))
            if allowance <= 0:
                return False
            indices = share[cursor : cursor + allowance]
            cursors[slot] = cursor + allowance
            granted += allowance
            connection.send(
                (
                    "run",
                    {
                        "kind": kind,
                        "config": config,
                        "context": context,
                        "seed": seed,
                        "indices": indices,
                        "identities": len(models),
                        "concurrency": capacities[active[slot]],
                        "batch_size": capacities[active[slot]],
                    },
                )
            )
            busy.add(connection)
            idle.discard(connection)
            return True

        def check_alive() -> None:
            if any(not self.processes[index].is_alive() for index in active):
                raise RuntimeError("Search actor exited unexpectedly")

        def cancelled() -> None:
            if should_stop and should_stop():
                raise InterruptedError("Parallel search was cancelled")

        try:
            for connection in used:
                connection.send(
                    (
                        "bind",
                        snapshots,
                        config.get("inference_cache_mode", "off"),
                        config.get("inference_cache_capacity", 4096),
                    )
                )
            bound = set(used)
            while bound:
                if should_stop and should_stop():
                    raise InterruptedError("Parallel search was cancelled")
                for connection in cast(list[Connection], wait(list(bound), timeout=0.05)):
                    message = connection.recv()
                    if message[0] != "bound":
                        raise RuntimeError(f"Search actor initialization failed: {message[1]}")
                    bound.discard(connection)
                check_alive()
            for connection in used:
                grant(connection)
            while completed < count:
                cancelled()
                # Every result received so far is yielded before the loop body
                # ends, so nothing is buffered here. Committing that prefix may
                # free replay-window capacity for the next slice.
                for connection in list(idle):
                    grant(connection)
                if not busy:
                    if idle:
                        raise RuntimeError("Search pool stalled with games outstanding")
                    raise RuntimeError("Search actors exited before delivering every game")
                ready = cast(list[Connection], wait(list(busy), timeout=0.05))
                if not ready:
                    check_alive()
                    continue
                for connection in ready:
                    drain(connection)
                # Wait out stragglers inside a bounded window so sibling actors
                # keep delivering into the same forward pass. One shared pass
                # costs far less than a second pass that merely fills it later.
                if pools and len(busy) > 1:
                    deadline = time.monotonic() + self.flush_seconds
                    while True:
                        stragglers = [
                            connection for connection in busy if connection not in awaiting
                        ]
                        remaining = deadline - time.monotonic()
                        if not stragglers or remaining <= 0:
                            break
                        joined = cast(list[Connection], wait(stragglers, timeout=remaining))
                        if not joined:
                            break
                        for connection in joined:
                            drain(connection)
                flush()
                check_alive()
                while streamed:
                    yield streamed.pop(0)
                    cancelled()
            while streamed:
                yield streamed.pop(0)
                cancelled()
            # The phase ends as soon as every game is delivered, but an actor may
            # still have its trailing report in flight. Read it here so a reused
            # pool never mistakes it for the next phase's bind reply.
            deadline = time.monotonic() + 5.0
            while busy and time.monotonic() < deadline:
                ready = cast(
                    list[Connection],
                    wait(list(busy), timeout=max(0.0, deadline - time.monotonic())),
                )
                if not ready:
                    break
                for connection in ready:
                    drain(connection)
            if busy:
                raise RuntimeError("Search actors did not finish reporting the phase")
            self.performance = {
                "search_workers": len(self.processes),
                "search_worker_count": len(active),
                "search_concurrency": concurrency,
                "search_games_per_worker": max(capacities),
                "search_worker_capacity": [capacities[worker] for worker in active],
                "search_worker_peak_active": list(peak_active),
                "search_replay_window": window,
                "search_flush_seconds": self.flush_seconds,
                "search_initialization_seconds": self.initialization_seconds,
                "inference_location": "actors" if actors else "parent",
                "models": [
                    metric.snapshot(finalize_cuda=measure_cuda) for metric in self.inference_metrics
                ],
            }
        except (EOFError, BrokenPipeError) as error:
            self.close()
            _close_owned(owned, error)
            raise RuntimeError("Search actor connection closed") from error
        except BaseException as error:
            self.close()
            _close_owned(owned, error)
            raise
        else:
            _close_owned(owned)


def parallel_self_play(
    model,
    config,
    *,
    seed,
    should_stop=None,
    progress=None,
    on_game=None,
    measure_cuda=False,
    retain_examples=True,
    backend=None,
    search_pool=None,
):
    from .self_play import SelfPlayMetrics

    if should_stop and should_stop():
        raise InterruptedError("Training was cancelled")
    metrics = SelfPlayMetrics(
        model,
        "parallel",
        min(config["games"], config["self_play_concurrency"]),
    )
    if backend is not None:
        metrics.backend_info.update(backend.info)
    owned = search_pool is None
    pool = search_pool or SearchPool(config["search_workers"])
    completed = {}
    flushed = 0
    stream = pool.games(
        "self_play",
        model,
        config,
        seed=seed,
        backend=backend,
        should_stop=should_stop,
        measure_cuda=measure_cuda,
        committed=None if retain_examples else (lambda: flushed),
    )

    def performance(finalize=False):
        payload = metrics.snapshot(finalize_cuda=finalize)
        # Actor traversal overlaps across processes and inference. Its elapsed
        # wall contribution cannot be obtained by summing actor durations.
        payload["search_traversal_wall_seconds"] = None
        payload["search_workers"] = config["search_workers"]
        return payload

    try:
        if progress:
            progress({"game": 0, "performance": performance()})
        for index, result in stream:
            metrics.inference = pool.inference_metrics[0]
            completed[index] = result
            metrics.record(result[1])
            metrics.cpu_search_seconds += result[1].get("cpu_search_seconds", 0)
            while flushed in completed:
                examples, outcome = completed[flushed]
                if on_game:
                    on_game(flushed, examples, outcome)
                if not retain_examples:
                    completed[flushed] = ([], outcome)
                flushed += 1
            if progress:
                progress({"game": metrics.games, "performance": performance()})
    finally:
        stream.close()
        if owned:
            pool.close()
    payload = performance(measure_cuda)
    payload.update(pool.performance)
    return [completed[index] for index in range(config["games"])], payload


def parallel_evaluation(
    model, config, context, *, seed, previous=None, should_stop=None, search_pool=None
):
    if should_stop and should_stop():
        raise InterruptedError("Evaluation was cancelled")
    owned = search_pool is None
    pool = search_pool or SearchPool(config["search_workers"])
    stream = pool.games(
        "evaluation",
        model,
        config,
        seed=seed,
        context=context,
        previous=previous,
        should_stop=should_stop,
    )
    outcomes = {}
    try:
        for index, outcome in stream:
            outcomes[index] = outcome
    finally:
        stream.close()
        if owned:
            pool.close()
    return [outcomes[index] for index in range(config["evaluation_games"])], {
        **pool.performance,
        "mode": "parallel",
        "precision": "float32",
    }
