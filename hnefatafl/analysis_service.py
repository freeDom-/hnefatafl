"""Background, position-scoped analysis independent of the gameplay request pool."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from .storage import ConflictError, Store


class SupersededAnalysis(Exception):
    """An advisor was changed or its game was removed during a search."""


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
        revision = settings["revision"] if role == "advisor" else None
        key = (game["id"], snapshot["position_key"], role, revision)
        with self.lock:
            previous = self.tasks.get(key)
            if self.closed or (previous is not None and not previous.done()):
                return key
            # A poll may have read its report just before the preceding worker
            # committed. Recheck under the scheduling lock before starting work.
            if role == "advisor":
                assert revision is not None
                cached = self.store.advisor_analysis(game["id"], snapshot["position_key"], revision)
                if cached is not None and not cached.get("partial"):
                    return key
            else:
                cached = self.store.game_analysis(game["id"], snapshot["position_key"])
                if cached and any(item["agent_id"] == role for item in cached["evaluations"]):
                    return key
            if role == "advisor":
                self.latest_advisor[game["id"]] = key
                for previous_key, task in list(self.tasks.items()):
                    if (
                        previous_key[0] == game["id"]
                        and previous_key[2] == "advisor"
                        and previous_key != key
                    ):
                        task.cancel()
            task = self.executor.submit(self._run, game, snapshot, role, agent, settings, state)
            self.tasks[key] = task
            task.add_done_callback(lambda finished: self._forget(key, finished))
        return key

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
                self.store.save_analysis_entry(
                    game,
                    snapshot,
                    entry,
                    advisor_revision=revision,
                    online=bool(game.get("online")),
                )
            except ConflictError:
                pass

    def shutdown(self) -> None:
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
