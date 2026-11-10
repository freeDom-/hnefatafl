"""Background, position-scoped analysis independent of the gameplay request pool."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from typing import Callable

from .storage import ConflictError, Store


DEFAULT_NEURAL_MCTS_ITERATIONS = 1024
MAX_ANALYSIS_TASKS = 16
SYNCHRONOUS_WAIT_SECONDS = 20


def advisor_config(config: dict, profile_id: str | None = None) -> dict:
    """Apply analytics defaults before shared profile/search resolution.

    Explicit budgets, including those in saved advisors, remain authoritative.
    Playing agents and other users of the shared neural profile keep their defaults.
    """
    result = dict(config)
    algorithm = result.get("algorithm", profile_id)
    if (algorithm == "neural" and result.get("search_wrapper", "mcts") == "mcts") or (
        algorithm == "mcts" and result.get("model_id")
    ):
        result.setdefault("iterations", DEFAULT_NEURAL_MCTS_ITERATIONS)
    return result


class SupersededAnalysis(Exception):
    """An advisor was changed or its game was removed during a search."""


class AnalysisUnavailable(Exception):
    """Analysis capacity, shutdown or a finite request wait prevents completion."""


class AnalysisService:
    def __init__(self, store: Store, evaluate: Callable):
        self.store = store
        self.evaluate = evaluate
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="board-analysis")
        self.lock = threading.RLock()
        self.tasks: dict[tuple, Future] = {}
        self.closed = False
        self.latest_advisor: dict[str, tuple] = {}

    def active(self, key: tuple) -> bool:
        with self.lock:
            task = self.tasks.get(key)
            return task is not None and not task.done()

    def schedule(
        self,
        game: dict,
        snapshot: dict,
        role: str,
        agent: dict,
        settings: dict,
        *,
        state=None,
    ) -> tuple:
        key, _ = self._schedule(game, snapshot, role, agent, settings, state=state)
        return key

    def wait(
        self, game: dict, snapshot: dict, role: str, agent: dict, settings: dict, *, state=None
    ) -> None:
        """Join the same bounded work as background clients, outside the pool lock.

        A request timeout/disconnect must not cancel work another client needs.
        Only workers wait for inference; no worker submits or waits on this pool.
        """
        key, task = self._schedule(game, snapshot, role, agent, settings, state=state)
        if task is not None:
            try:
                task.result(timeout=SYNCHRONOUS_WAIT_SECONDS)
            except TimeoutError as exc:
                raise AnalysisUnavailable(
                    "Analysis is still running. Retry this request or use background analysis."
                ) from exc
            except CancelledError as exc:
                raise AnalysisUnavailable("Analysis service is stopping. Retry later.") from exc
        if not self._completed(key):
            raise SupersededAnalysis("The analyzed position or advisor changed. Retry analysis.")

    def _completed(self, key: tuple) -> bool:
        game_id, position_key, role, revision = key
        if role == "advisor":
            cached = self.store.advisor_analysis(game_id, position_key, revision)
            return cached is not None and not cached.get("partial")
        cached = self.store.game_analysis(game_id, position_key)
        return bool(cached and any(item["agent_id"] == role for item in cached["evaluations"]))

    def _schedule(
        self, game: dict, snapshot: dict, role: str, agent: dict, settings: dict, *, state=None
    ) -> tuple[tuple, Future | None]:
        revision = settings["revision"] if role == "advisor" else None
        key = (game["id"], snapshot["position_key"], role, revision)
        with self.lock:
            previous = self.tasks.get(key)
            # Capture activity before reading storage. If this future completes
            # during the cache read, joining it still observes that completion.
            previous_running = previous is not None and not previous.done()
            # Playing-agent polls retain their existing read ordering: their
            # final report read follows the activity check in the HTTP adapter.
            if role != "advisor" and previous_running and not self.closed:
                return key, previous
            # A poll may have read its report just before the preceding worker
            # committed. Recheck under the scheduling lock before starting work.
            if self._completed(key):
                return key, None
            if self.closed:
                raise AnalysisUnavailable("Analysis service is stopping. Retry later.")
            if previous_running:
                return key, previous
            if len(self.tasks) >= MAX_ANALYSIS_TASKS:
                raise AnalysisUnavailable("Analysis workers are busy. Retry shortly.")
            if role == "advisor":
                self.latest_advisor[game["id"]] = key
                # Superseded queued work checks this key before evaluation. Keep
                # its slot until drained: Future.cancel() leaves a work item in
                # ThreadPoolExecutor's queue and could otherwise grow it forever.
            task = self.executor.submit(self._run, game, snapshot, role, agent, settings, state)
            self.tasks[key] = task
            task.add_done_callback(lambda finished: self._forget(key, finished))
        return key, task

    def _forget(self, key: tuple, task: Future) -> None:
        with self.lock:
            if self.tasks.get(key) is task:
                self.tasks.pop(key, None)
                if self.latest_advisor.get(key[0]) == key:
                    self.latest_advisor.pop(key[0], None)

    def _run(self, game: dict, snapshot: dict, role: str, agent: dict, settings: dict, state=None):
        revision = settings["revision"] if role == "advisor" else None

        def check() -> None:
            if self.closed:
                raise SupersededAnalysis()
            if game.get("online"):
                if not self.store.online_game_exists(game["id"]):
                    raise SupersededAnalysis()
            elif self.store.get_game(game["id"]) is None:
                raise SupersededAnalysis()
            if revision is not None:
                key = (game["id"], snapshot["position_key"], role, revision)
                if self.latest_advisor.get(game["id"]) != key:
                    raise SupersededAnalysis()
                current = self.store.analytics(game["id"])
                if (current["revision"] if current else 0) != revision:
                    raise SupersededAnalysis()

        def progress(entry: dict) -> None:
            check()
            if role == "advisor":
                self.store.save_analysis_entry(
                    game,
                    snapshot,
                    entry,
                    advisor_revision=revision,
                    partial=True,
                    online=bool(game.get("online")),
                )

        try:
            check()
            entry = self.evaluate(game, role, agent, settings, progress, state=state)
            check()
            self.store.save_analysis_entry(
                game,
                snapshot,
                entry,
                advisor_revision=revision,
                online=bool(game.get("online")),
            )
        except (ConflictError, SupersededAnalysis):
            # Cancellation is expected: a late opinion must not revive a game,
            # replace a new advisor, or attach itself to a different branch.
            pass
        except Exception:
            logging.getLogger(__name__).exception("Background analysis failed")
            entry = {
                **agent,
                "agent_id": role,
                "role": role,
                "source": "unavailable",
                "win_probability": None,
                "estimated": True,
                "calibrated": False,
                "unavailable_reason": "evaluation_failed",
            }
            try:
                check()
                self.store.save_analysis_entry(
                    game,
                    snapshot,
                    entry,
                    advisor_revision=revision,
                    online=bool(game.get("online")),
                )
            except (ConflictError, SupersededAnalysis):
                pass

    def shutdown(self) -> None:
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
