"""Spawned CPU search actors with one parent-owned neural inference service.

CUDA/OpenVINO models stay in the parent. CPU actors bind independent read-only
weight snapshots. Each actor waits for its own leaf result before advancing a
dependent simulation. Pools can span train iterations.
"""

from __future__ import annotations

from collections import defaultdict
import multiprocessing as mp
from multiprocessing.connection import Connection, wait
import time
from typing import Any, Callable, Generator, cast

import torch

from .engine import State, apply_move, new_game
from .inference import ExactInferenceCache, InferenceMetrics, policy_value_batch


def _evaluate_game(index, config, context, seed, request, cancelled):
    from .ai import choose_move, mcts_context

    state = new_game(config["ruleset"])
    side = "attackers" if index % 2 == 0 else "defenders"
    pair_seed = seed + 1000 * (index // 2)
    simulations = cutoffs = 0
    for ply in range(config["evaluation_max_plies"]):
        if cancelled():
            raise InterruptedError("Evaluation was cancelled")
        if state.winner:
            break
        candidate = state.turn == side
        settings = dict(context["candidate_config"] if candidate else context["opponent_config"])
        settings["seed"] = pair_seed + ply
        details: dict = {}
        if candidate or context["opponent"] == "champion":
            search = mcts_context(
                state, settings, diagnostics=details, should_stop=cancelled, neural=True
            )
            try:
                pending = next(search)
                while True:
                    pending = search.send(request(0 if candidate else 1, pending))
            except StopIteration as finished:
                policy = finished.value
            finally:
                search.close()
            move = max(policy, key=lambda action: policy[action])
        else:
            move = choose_move(state, settings, diagnostics=details, should_stop=cancelled)
        simulations += details.get("simulations", 0)
        cutoffs += details.get("deadline_cutoffs", 0)
        state = apply_move(state, move)
    return {
        "pair": index // 2,
        "seed": pair_seed,
        "candidate_side": side,
        "winner": state.winner,
        "outcome": "completed" if state.winner else "truncated",
        "score": 0.5 if state.winner == "draw" else float(state.winner == side),
        "plies": state.ply,
        "reason": state.reason or "evaluation_ply_limit",
        "simulations": simulations,
        "search_deadline_cutoffs": cutoffs,
    }


def _actor(connection: Connection, cancel) -> None:
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    local_models: list = []
    local_metrics: list = []
    local_caches: list = []

    def cancelled():
        return cancel.is_set()

    def request(identity, state):
        if local_models:
            return policy_value_batch(
                [state],
                local_models[identity],
                metrics=local_metrics[identity],
                cache=local_caches[identity],
            )[0]
        # Encoding depends on ruleset/board/turn only. Repetition histories stay
        # in actor-owned engine states and never cross IPC for neural inference.
        connection.send(("infer", identity, (state.ruleset, state.board, state.turn)))
        while not connection.poll(0.05):
            if cancelled():
                raise InterruptedError("Search was cancelled")
        return connection.recv()

    try:
        while not cancelled():
            task = connection.recv()
            if task is None:
                return
            if task[0] == "bind":
                from .models import PolicyValueNet

                local_models.clear()
                local_caches.clear()
                for architecture, weights in task[1]:
                    selected = PolicyValueNet(**architecture).eval().requires_grad_(False)
                    selected.load_state_dict(
                        {key: torch.from_numpy(value) for key, value in weights.items()}
                    )
                    local_models.append(selected)
                    local_caches.append(
                        ExactInferenceCache(task[3]) if task[2] == "exact" else None
                    )
                connection.send(("bound",))
                continue
            kind, index, config, context, seed = task
            local_metrics = [InferenceMetrics() for _ in local_models]
            result: Any
            started = time.process_time()
            if kind == "self_play":
                from .self_play import _game_context

                game = _game_context(None, config, seed + index, cancelled)
                try:
                    pending = next(game)
                    while True:
                        pending = game.send(request(0, pending))
                except StopIteration as finished:
                    examples, result = finished.value
                finally:
                    game.close()
                # Plain NumPy payloads avoid shared-tensor file-descriptor
                # lifetimes after an actor has exited or a pool is cancelled.
                for row in examples:
                    row["input"] = row["input"].numpy()
                result["cpu_search_seconds"] = max(
                    0.0,
                    time.process_time()
                    - started
                    - sum(metric.cpu_seconds for metric in local_metrics),
                )
                result = (examples, result)
            else:
                result = _evaluate_game(index, config, context, seed, request, cancelled)
            connection.send(("result", result, [metric.snapshot() for metric in local_metrics]))
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as error:
        if not cancelled():
            connection.send(("error", f"{type(error).__name__}: {error}"))
    finally:
        connection.close()


class SearchPool:
    def __init__(self, workers: int):
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError("search_workers must be between 1 and 32")
        started = time.monotonic()
        context = mp.get_context("spawn")
        self.cancel = context.Event()
        self.connections: list[Connection] = []
        self.processes: list[Any] = []
        self.performance: dict = {}
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
    ) -> Generator[tuple[int, Any], None, None]:
        if self.closed:
            raise RuntimeError("Search pool is closed")
        models = [model] + ([previous] if previous is not None else [])
        for selected in models:
            selected.eval()
        metrics = [
            InferenceMetrics(
                track_cuda=measure_cuda and next(selected.parameters()).device.type == "cuda"
            )
            for selected in models
        ]
        self.inference_metrics = metrics
        caches = [
            ExactInferenceCache(config["inference_cache_capacity"])
            if config.get("inference_cache_mode") == "exact"
            else None
            for _ in models
        ]
        count = config["games"] if kind == "self_play" else config["evaluation_games"]
        limit = (
            config.get("inference_batch_size", 32)
            if kind == "self_play"
            else config.get("evaluation_batch_size", 32)
        )
        busy: dict[Connection, int] = {}
        pending: dict[Connection, tuple[int, State]] = {}
        actor_metrics: dict[Connection, list[dict]] = {}
        started = finished = committed = 0
        completed: set[int] = set()
        concurrency = min(
            len(self.processes),
            count,
            config.get(
                "self_play_concurrency" if kind == "self_play" else "evaluation_concurrency", 16
            ),
        )
        backends = [backend] if kind == "self_play" else [None for _ in models]
        owned = []
        try:
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
                            "self_play_concurrency": len(self.processes),
                            "inference_batch_size": limit,
                        },
                    )
                    owned.append(graph)
                    graph.refresh()
                    backends[index] = graph
            local = all(
                next(selected.parameters()).device.type == "cpu" for selected in models
            ) and all(
                graph is None or type(graph).__name__ == "TorchInference" for graph in backends
            )
            snapshots = (
                [
                    (
                        selected.config,
                        {
                            key: tensor.detach().numpy()
                            for key, tensor in selected.state_dict().items()
                        },
                    )
                    for selected in models
                ]
                if local
                else []
            )
            waiting = set(self.connections[:concurrency])
            for connection in waiting:
                connection.send(
                    (
                        "bind",
                        snapshots,
                        config.get("inference_cache_mode", "off"),
                        config.get("inference_cache_capacity", 4096),
                    )
                )
            while waiting:
                if should_stop and should_stop():
                    raise InterruptedError("Parallel search was cancelled")
                for connection in cast(list[Connection], wait(waiting, timeout=0.05)):
                    if connection.recv()[0] != "bound":
                        raise RuntimeError("Search actor initialization failed")
                    waiting.remove(connection)
                if any(not process.is_alive() for process in self.processes):
                    raise RuntimeError("Search actor exited during initialization")
            while finished < count:
                if should_stop and should_stop():
                    raise InterruptedError("Parallel search was cancelled")
                for connection in self.connections[:concurrency]:
                    if (
                        connection not in busy
                        and started < count
                        and started - committed < 2 * concurrency
                    ):
                        connection.send((kind, started, config, context, seed))
                        busy[connection] = started
                        started += 1
                ready = cast(
                    list[Connection],
                    wait(
                        [connection for connection in busy if connection not in pending],
                        timeout=0.002 if pending else 0.05,
                    ),
                )
                for connection in ready:
                    message = connection.recv()
                    if message[0] == "infer":
                        rule, board, turn = message[2]
                        pending[connection] = (message[1], State(rule, board, turn))
                    elif message[0] == "result":
                        index = busy.pop(connection)
                        result = message[1]
                        if kind == "self_play":
                            for row in result[0]:
                                row["input"] = torch.from_numpy(row["input"])
                        finished += 1
                        actor_metrics[connection] = message[2]
                        for identity, (target, incoming) in enumerate(zip(metrics, message[2])):
                            for field, source in (
                                ("requests", "inference_requests"),
                                ("cache_misses", "inference_cache_misses"),
                                ("cache_hits", "inference_cache_hits"),
                                ("duplicates", "inference_batch_duplicates"),
                                ("cache_lookup_seconds", "inference_cache_lookup_seconds"),
                                ("batches", "inference_batches"),
                                ("evaluations", "neural_evaluations"),
                                ("host_seconds", "inference_time_seconds"),
                                ("cpu_seconds", "inference_cpu_seconds"),
                            ):
                                setattr(target, field, getattr(target, field) + incoming[source])
                            target.cache_entries = sum(
                                rows[identity]["inference_cache_entries"]
                                for rows in actor_metrics.values()
                            )
                            target.cache_peak_entries = sum(
                                rows[identity]["inference_cache_peak_entries"]
                                for rows in actor_metrics.values()
                            )
                            if incoming["inference_batches"]:
                                target.batch_minimum = min(
                                    target.batch_minimum or incoming["inference_batch_size_min"],
                                    incoming["inference_batch_size_min"],
                                )
                            target.batch_maximum = max(
                                target.batch_maximum, incoming["inference_batch_size_max"]
                            )
                            for size, amount in incoming["inference_batch_distribution"].items():
                                target.batch_histogram[int(size)] = (
                                    target.batch_histogram.get(int(size), 0) + amount
                                )
                        yield index, result
                        completed.add(index)
                        while committed in completed:
                            completed.remove(committed)
                            committed += 1
                    else:
                        raise RuntimeError(f"Search actor failed: {message[1]}")
                if any(not process.is_alive() for process in self.processes):
                    raise RuntimeError("Search actor exited unexpectedly")
                groups: dict[int, list] = defaultdict(list)
                for connection, (identity, state) in pending.items():
                    groups[identity].append((connection, state))
                for identity, requests in groups.items():
                    for start in range(0, len(requests), limit):
                        batch = requests[start : start + limit]
                        values = policy_value_batch(
                            [state for _, state in batch],
                            models[identity],
                            mixed_precision=kind == "self_play" and bool(config["mixed_precision"]),
                            metrics=metrics[identity],
                            backend=backends[identity],
                            cache=caches[identity],
                        )
                        for (connection, _), value in zip(batch, values):
                            connection.send(value)
                            del pending[connection]
            self.performance = {
                "search_workers": len(self.processes),
                "search_concurrency": concurrency,
                "inference_location": "actors" if local else "parent",
                "models": [metric.snapshot(finalize_cuda=measure_cuda) for metric in metrics],
            }
        except (EOFError, BrokenPipeError) as error:
            self.close()
            raise RuntimeError("Search actor connection closed") from error
        except BaseException:
            self.close()
            raise
        finally:
            for graph in owned:
                graph.close()


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
        min(config["games"], config["search_workers"], config["self_play_concurrency"]),
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
