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


#: Actor progress is batched so the pool never sends one message per simulation.
#: A flush happens when either bound is reached, which keeps the message rate
#: proportional to search work rather than to simulation count.
PROGRESS_DELTAS = 64
PROGRESS_INTERVAL_SECONDS = 0.25


class _ActorProgress:
    """Batch an actor's per-position counters into bounded progress messages.

    The object duck-types the ``ProgressSink`` surface the game contexts already
    use, so an actor records exactly the counters the in-process path records.
    Nothing is accumulated or sent unless the phase asked for progress, which
    keeps monitoring off the hot path when no monitor is attached.
    """

    def __init__(self, connection: Connection, enabled: bool):
        self._connection = connection
        self._enabled = enabled
        self._sequence = 0
        self._deltas: dict[str, float] = {}
        self._active = 0
        self._dirty = False
        self._flushed = time.monotonic()

    def tick(self, **deltas: float) -> None:
        if not self._enabled:
            return
        for key, value in deltas.items():
            self._deltas[key] = self._deltas.get(key, 0) + value
        self._dirty = True
        if (
            sum(self._deltas.values()) >= PROGRESS_DELTAS
            or time.monotonic() - self._flushed >= PROGRESS_INTERVAL_SECONDS
        ):
            self.flush()

    def note_active(self, active: int) -> None:
        """Record how many game contexts this actor is currently searching."""
        if not self._enabled or active == self._active:
            return
        self._active = active
        self._dirty = True

    def flush(self) -> None:
        """Send the accumulated counters once; the parent only ever adds them."""
        if not self._enabled or not self._dirty:
            return
        self._sequence += 1
        self._connection.send(("progress", self._sequence, dict(self._deltas), self._active))
        self._deltas.clear()
        self._dirty = False
        self._flushed = time.monotonic()


def _game_factory(
    kind: str,
    config: dict,
    context: dict | None,
    seed: int,
    identities: int,
    cancelled: Callable[[], bool],
    progress: _ActorProgress | None = None,
):
    """Return a per-index factory for the search generators a phase needs.

    ``progress`` lets a game context record the same per-position counters the
    in-process path records; the actor batches them instead of using a monitor it
    cannot share across processes.
    """
    if kind == "self_play":
        from .self_play import _game_context

        def self_play(index: int):
            return single_identity(_game_context(None, config, seed + index, cancelled, progress))

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
    reported_entries: list[int],
    reported_peak: list[int],
    cancelled: Callable[[], bool],
    remote: Callable[[int, list[State]], list],
) -> None:
    """Drive one worker's assigned games and report each completed game.

    ``reported_entries`` and ``reported_peak`` are the actor's cache-reporting
    baselines. They belong to the bound snapshot generation, not to this slice:
    an actor keeps its cache between slices, so a slice that reset them would
    report every existing entry a second time.
    """
    kind = spec["kind"]
    config = spec["config"]
    indices = spec["indices"]
    identities = spec["identities"]
    local = bool(snapshots)
    if not local:
        # Parent-owned inference keeps every cache in the parent, so the actor
        # has none to report and needs no baseline for them.
        caches = [None] * identities
        reported_entries = [0] * identities
        reported_peak = [0] * identities
    metrics = [InferenceMetrics() for _ in range(identities)]
    per_game_cpu: dict[int, float] = {}
    progress = _ActorProgress(connection, bool(spec.get("progress")))

    def take_metrics() -> list[InferenceMetrics]:
        """Hand over activity since the previous report and start a fresh window."""
        nonlocal metrics
        sent, metrics = metrics, [InferenceMetrics() for _ in range(identities)]
        for identity, metric in enumerate(sent):
            # Cache sizes live on the cache itself, not on a window that may have
            # evaluated nothing, so they are reported as the change since this
            # generation's last report and the parent sums them across actors.
            cache = caches[identity]
            current = len(cache.entries) if cache is not None else 0
            increment = current - reported_entries[identity]
            if increment < 0:
                # Only reachable if a bound snapshot's cache were invalidated
                # mid-generation, which rebinding prevents: report nothing and
                # restart the baseline rather than a negative count.
                increment = 0
            reported_entries[identity] = current
            metric.cache_entries = increment
            peak = max(reported_peak[identity], current)
            metric.cache_peak_entries = peak - reported_peak[identity]
            reported_peak[identity] = peak
        return sent

    def evaluate(identity: int, states: list[State]):
        if kind == "evaluation":
            # One evaluated leaf is one searched position, exactly as the
            # in-process batched evaluator counts it.
            progress.tick(evaluation_plies=len(states))
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
        progress.note_active(len(search))
        progress.flush()
        connection.send(("done", index, payload, take_metrics(), len(search)))

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
    create = _game_factory(
        kind, config, spec.get("context"), spec["seed"], identities, cancelled, progress
    )
    started = 0
    peak_active = 0

    def refill() -> None:
        nonlocal started, peak_active
        while len(search) < spec["concurrency"] and started < len(indices):
            index = indices[started]
            started += 1
            search.start(index, create(index))
        peak_active = max(peak_active, len(search))
        progress.note_active(len(search))
        progress.flush()

    try:
        search.run(refill)
    finally:
        search.close()
    progress.flush()
    connection.send(("finished", take_metrics(), peak_active))


def _actor(connection: Connection, cancel) -> None:
    """Own one worker's games; never touch CUDA, which belongs to the parent."""
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    # The bound snapshots, their caches and the cache-reporting baselines all
    # outlive a single granted slice: an actor keeps its cache between slices, so
    # the counters that turn absolute occupancy into increments must too.
    snapshots: list = []
    caches: list = []
    reported_entries: list[int] = []
    reported_peak: list[int] = []
    token = 0

    def cancelled() -> bool:
        return cancel.is_set()

    def bind(message) -> None:
        nonlocal snapshots, caches, reported_entries, reported_peak
        from .models import PolicyValueNet

        mode, capacity = message[2], message[3]
        snapshots = []
        caches = []
        # A new generation starts with empty caches and empty baselines.
        reported_entries = []
        reported_peak = []
        for architecture, weights in message[1]:
            selected = PolicyValueNet(**architecture).eval().requires_grad_(False)
            selected.load_state_dict(
                {key: torch.from_numpy(value) for key, value in weights.items()}
            )
            snapshots.append(selected)
            caches.append(ExactInferenceCache(capacity) if mode == "exact" else None)
            reported_entries.append(0)
            reported_peak.append(0)

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
            _search(
                connection,
                task[1],
                snapshots,
                caches,
                reported_entries,
                reported_peak,
                cancelled,
                remote,
            )
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
    phases reuse it and process startup is amortised. ``workers`` is the
    configured maximum and never a phase's demand: processes are created on
    demand, only as far as a phase can actually use them, and are kept for later
    phases. Every call decides its own game concurrency, batch limits and
    per-worker share.
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
        self.workers = workers
        self.inference = inference
        self.flush_seconds = float(flush_seconds)
        self.context = mp.get_context("spawn")
        self.cancel = self.context.Event()
        self.connections: list[Connection] = []
        self.processes: list[Any] = []
        self.performance: dict = {}
        self.inference_metrics: list[InferenceMetrics] = []
        self.active_games = 0
        self.closed = False
        self.initialization_seconds = 0.0

    def _ensure(self, workers: int) -> int:
        """Create worker processes on demand, never more than the configured cap.

        Returns the number of processes now available. A worker created here is
        bound with the current model snapshot by the phase that requested it, so
        growth between phases needs no extra handshake.
        """
        if self.closed:
            raise RuntimeError("Search pool is closed")
        target = min(workers, self.workers)
        if target <= len(self.processes):
            return len(self.processes)
        started = time.monotonic()
        try:
            while len(self.processes) < target:
                parent, child = self.context.Pipe()
                process = None
                try:
                    process = self.context.Process(
                        target=_actor, args=(child, self.cancel), daemon=True
                    )
                    process.start()
                except BaseException as error:
                    # This attempt's resources are not registered yet, so
                    # ``self.close()`` cannot reach them.
                    if process is not None and process.pid is not None:
                        try:
                            process.terminate()
                            process.join()
                        except BaseException as cleanup:
                            error.add_note(
                                f"Stopping a partially started search worker failed: {cleanup!r}"
                            )
                    parent.close()
                    child.close()
                    raise
                # The child end belongs to the spawned process from here on.
                child.close()
                self.connections.append(parent)
                self.processes.append(process)
        except BaseException:
            self.close()
            raise
        finally:
            self.initialization_seconds += time.monotonic() - started
        return len(self.processes)

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
        monitor=None,
    ) -> Generator[tuple[int, Any], None, None]:
        """Run one phase, yielding ``(index, result)`` in completion order.

        ``committed`` reports how many leading games the caller has already
        committed. When supplied, the pool hands out new games only while the
        started-but-uncommitted count stays inside the ordered replay window, so
        a slow early game cannot retain an iteration's worth of examples.

        ``monitor`` receives the actors' batched per-position counters and their
        live active-game counts as they arrive, so a long game still advances the
        dashboard instead of waiting for its completion. The completion of a game
        contributes only its own completion counter: positions and simulations
        were already reported incrementally, and a message that arrives twice or
        out of order is dropped by its per-worker sequence number.
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
        # A phase can only use as many workers as it has concurrent game slots,
        # so demand decides how many processes exist; the configured maximum is
        # a cap, never a target. Growth is kept for later phases.
        workers = self._ensure(min(self.workers, concurrency))
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
        active_games: dict[Connection, int] = {}
        progress_sequence: dict[Connection, int] = {}
        progress_messages = 0
        self.active_games = 0

        def merge(deltas: list[InferenceMetrics]) -> None:
            for identity, delta in enumerate(deltas):
                self.inference_metrics[identity].add(delta)

        def handle(connection: Connection, message) -> None:
            nonlocal completed, progress_messages
            if message[0] == "progress":
                sequence = message[1]
                if sequence <= progress_sequence.get(connection, 0):
                    # Duplicate or stale: its deltas were already applied.
                    return
                progress_messages += 1
                progress_sequence[connection] = sequence
                active_games[connection] = message[3]
                self.active_games = sum(active_games.values())
                if monitor is not None:
                    monitor.tick(**message[2])
                    if kind == "self_play":
                        monitor.counts(games_active=self.active_games)
            elif message[0] == "leaves":
                pools[message[2]].append((connection, message[1], message[3]))
                awaiting.add(connection)
            elif message[0] == "done":
                result = message[2]
                if kind == "self_play":
                    for row in result[0]:
                        row["input"] = torch.from_numpy(row["input"])
                completed += 1
                active_games[connection] = message[4]
                self.active_games = sum(active_games.values())
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
                        "progress": monitor is not None,
                    },
                )
            )
            busy.add(connection)
            idle.discard(connection)
            # A granted run starts a fresh actor-side accumulator, so its
            # sequence numbers restart at one.
            progress_sequence[connection] = 0
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
                "search_workers": self.workers,
                "search_processes": len(self.processes),
                "search_worker_count": len(active),
                "search_concurrency": concurrency,
                "search_games_per_worker": max(capacities),
                "search_worker_capacity": [capacities[worker] for worker in active],
                "search_worker_peak_active": list(peak_active),
                "search_replay_window": window,
                "search_flush_seconds": self.flush_seconds,
                "search_initialization_seconds": self.initialization_seconds,
                "search_progress_messages": progress_messages,
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
    monitor=None,
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
    if monitor is not None:
        # Positions and simulations are counted per completed game here: the
        # actors own the per-position counters and cannot share this process's
        # monitor.
        monitor.counts(games_total=config["games"], games_active=0)
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
        monitor=monitor,
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
            if monitor is not None:
                # Positions and simulations already arrived incrementally from
                # the actor; only the completion itself is new.
                monitor.tick(games_completed=1)
                monitor.counts(games_active=pool.active_games)
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
        if monitor is not None:
            # Nothing is searching any more: a cancelled or failed phase must not
            # leave the dashboard showing live games.
            monitor.counts(games_active=0)
        if owned:
            pool.close()
    payload = performance(measure_cuda)
    payload.update(pool.performance)
    return [completed[index] for index in range(config["games"])], payload


def parallel_evaluation(
    model,
    config,
    context,
    *,
    seed,
    previous=None,
    should_stop=None,
    search_pool=None,
    monitor=None,
):
    if should_stop and should_stop():
        raise InterruptedError("Evaluation was cancelled")
    if monitor is not None:
        monitor.counts(
            evaluation_opponent=context["opponent"],
            evaluation_total=config["evaluation_games"],
            evaluation_games=0,
            evaluation_wins=0,
            evaluation_draws=0,
            evaluation_losses=0,
        )
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
        monitor=monitor,
    )
    outcomes = {}
    wins = draws = losses = 0
    try:
        for index, outcome in stream:
            outcomes[index] = outcome
            if monitor is None:
                continue
            side = outcome["candidate_side"]
            winner = outcome["winner"]
            # Truncated games count as neither a win nor a loss, matching scoring.
            wins += int(winner == side)
            draws += int(winner == "draw")
            losses += int(winner not in (None, "draw") and winner != side)
            # Searched positions arrived incrementally; only the completion is new.
            monitor.tick(evaluation_games=1)
            monitor.counts(evaluation_wins=wins, evaluation_draws=draws, evaluation_losses=losses)
    finally:
        stream.close()
        if monitor is not None:
            monitor.counts(games_active=0)
        if owned:
            pool.close()
    return [outcomes[index] for index in range(config["evaluation_games"])], {
        **pool.performance,
        "mode": "parallel",
        "precision": "float32",
    }
