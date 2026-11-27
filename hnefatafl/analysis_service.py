"""Background, position-scoped analysis independent of the gameplay request pool."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from functools import partial
from typing import Callable

from .analysis_policy import AnalysisAdmission

from .storage import ConflictError, Store


DEFAULT_NEURAL_MCTS_ITERATIONS = 1024
MAX_ANALYSIS_TASKS = 16
MAX_REPLAY_REQUESTERS = 2
SYNCHRONOUS_WAIT_SECONDS = 20
MAX_SYNCHRONOUS_WAITERS = 8


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
    def __init__(
        self, store: Store, evaluate: Callable, admission: AnalysisAdmission | None = None
    ):
        self.store = store
        self.evaluate = evaluate
        self.admission = admission
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="board-analysis")
        self.lock = threading.RLock()
        self.requester_lock = threading.RLock()
        self.tasks: dict[tuple, Future] = {}
        self.requesters: dict[tuple, set[str]] = {}
        self.closed = False
        self.latest_advisor: dict[str, tuple] = {}
        self.synchronous_waiters = threading.BoundedSemaphore(MAX_SYNCHRONOUS_WAITERS)

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
        receipt=None,
    ) -> tuple:
        key, _ = self._schedule(game, snapshot, role, agent, settings, state=state, receipt=receipt)
        return key

    def wait(
        self,
        game: dict,
        snapshot: dict,
        role: str,
        agent: dict,
        settings: dict,
        *,
        state=None,
        receipt=None,
    ) -> None:
        """Join the same bounded work as background clients, outside the pool lock.

        A request timeout/disconnect must not cancel work another client needs.
        Only workers wait for inference; no worker submits or waits on this pool.
        Bound HTTP waiters separately so shared searches leave request workers free.
        """
        if not self.synchronous_waiters.acquire(blocking=False):
            revision = settings["revision"] if role == "advisor" else None
            if self._completed((game["id"], snapshot["position_key"], role, revision)):
                return
            raise AnalysisUnavailable(
                "Synchronous analysis capacity is busy. Retry later or use background analysis."
            )
        try:
            key, task = self._schedule(
                game, snapshot, role, agent, settings, state=state, receipt=receipt
            )
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
                if self.closed:
                    raise AnalysisUnavailable("Analysis service is stopping. Retry later.")
                raise SupersededAnalysis(
                    "The analyzed position or advisor changed. Retry analysis."
                )
        finally:
            self.synchronous_waiters.release()

    def _completed(self, key: tuple) -> bool:
        game_id, position_key, role, revision = key
        if role == "advisor":
            cached = self.store.advisor_analysis(game_id, position_key, revision)
            return cached is not None and not cached.get("partial")
        cached = self.store.game_analysis(game_id, position_key)
        return bool(cached and any(item["agent_id"] == role for item in cached["evaluations"]))

    def _observe_receipt(self, receipt, task: Future | None) -> None:
        """Tie one receipt submission to an existing or newly scheduled task."""

        if receipt is None or task is None:
            return
        receipt.track()

        def completed(future: Future) -> None:
            success = False
            try:
                success = future.exception() is None
            except CancelledError:
                success = False
            except BaseException:  # pragma: no cover - defensive future contract
                success = False
            receipt.complete(success)

        task.add_done_callback(completed)

    def _schedule(
        self,
        game: dict,
        snapshot: dict,
        role: str,
        agent: dict,
        settings: dict,
        *,
        state=None,
        receipt=None,
    ) -> tuple[tuple, Future | None]:
        revision = settings["revision"] if role == "advisor" else None
        key = (game["id"], snapshot["position_key"], role, revision)
        requester = self.store.replay_requester(game) if role == "advisor" else None
        with self.lock:
            previous = self.tasks.get(key)
            # Capture activity before reading storage. If this future completes
            # during the cache read, joining it still observes that completion.
            previous_running = previous is not None and not previous.done()
            # Playing-agent polls retain their existing read ordering: their
            # final report read follows the activity check in the HTTP adapter.
            if role != "advisor" and previous_running and not self.closed:
                self._observe_receipt(receipt, previous)
                return key, previous
            # A poll may have read its report just before the preceding worker
            # committed. Recheck under the scheduling lock before starting work.
            if self._completed(key):
                if requester and revision is not None:
                    cached = self.store.advisor_analysis(
                        game["id"], snapshot["position_key"], revision
                    )
                    self.store.record_replay_analysis(game, cached, requester)
                if receipt is not None:
                    receipt.result_available()
                return key, None
            if self.closed:
                raise AnalysisUnavailable("Analysis service is stopping. Retry later.")
            if previous_running:
                if requester:
                    with self.requester_lock:
                        # Completion may have committed after the cache read.
                        # Join its successful receipt rather than losing a late actor.
                        if self._completed(key):
                            cached = self.store.advisor_analysis(
                                game["id"], snapshot["position_key"], settings["revision"]
                            )
                            self.store.record_replay_analysis(game, cached, requester)
                        else:
                            registered = self.requesters.setdefault(key, set())
                            if (
                                requester not in registered
                                and len(registered) >= MAX_REPLAY_REQUESTERS
                            ):
                                raise AnalysisUnavailable(
                                    "Replay requester capacity is busy. Retry later."
                                )
                            registered.add(requester)
                self._observe_receipt(receipt, previous)
                return key, previous
            if len(self.tasks) >= MAX_ANALYSIS_TASKS:
                raise AnalysisUnavailable("Analysis workers are busy. Retry shortly.")
            token: tuple[str, int] | None = None
            account_id = game.get("_analysis_account_id")
            if self.admission is not None and account_id is not None:
                token = self.admission.admit_task(str(account_id))
            if role == "advisor":
                self.latest_advisor[game["id"]] = key
                # Superseded queued work checks this key before evaluation. Keep
                # its slot until drained: Future.cancel() leaves a work item in
                # ThreadPoolExecutor's queue and could otherwise grow it forever.
            if requester:
                with self.requester_lock:
                    self.requesters[key] = {requester}
            try:
                task = self.executor.submit(self._run, game, snapshot, role, agent, settings, state)
            except Exception:
                if token is not None and self.admission is not None:
                    self.admission.release_task(token)
                raise
            self.tasks[key] = task
            task.add_done_callback(partial(self._forget, key, token=token))
            self._observe_receipt(receipt, task)
        return key, task

    def _forget(self, key: tuple, task: Future, token: tuple[str, int] | None = None) -> None:
        with self.lock:
            if self.tasks.get(key) is task:
                self.tasks.pop(key, None)
                with self.requester_lock:
                    self.requesters.pop(key, None)
                if self.latest_advisor.get(key[0]) == key:
                    self.latest_advisor.pop(key[0], None)
        # Admission belongs to this task, not to the key: a replacement task may
        # legitimately reuse the key before this callback runs. Release exactly
        # once regardless of whether a newer task currently occupies the key.
        if token is not None and self.admission is not None:
            self.admission.release_task(token)

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
            # Serialize requester registration with committing the shared report.
            # Late arrivals consume the completed cache and take the receipt fast path.
            with self.requester_lock:
                check()
                key = (game["id"], snapshot["position_key"], role, revision)
                self.store.save_analysis_entry(
                    game,
                    snapshot,
                    entry,
                    advisor_revision=revision,
                    online=bool(game.get("online")),
                    requester_ids=tuple(self.requesters.get(key, ())),
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
