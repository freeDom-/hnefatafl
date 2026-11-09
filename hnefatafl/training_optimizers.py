"""Small lifecycle boundary for optimizers with separate inference parameters.

No schedulefree import is needed for ordinary optimizers or API-only installs.
The published optimizer owns its sequences, counters, warm-up and averaging;
this adapter only controls when they are used and reports scalar diagnostics.
"""

from contextlib import contextmanager
import math

import torch


SCHEDULE_FREE = "adamw_schedulefree"
SCHEDULE_FREE_ADAPTATION_ERROR = (
    "Schedule-Free AdamW with HyperController is not supported yet; set adaptation to off."
)
SCHEDULE_FREE_DEPENDENCY_ERROR = (
    "Schedule-Free AdamW requires schedulefree>=1.4.1,<2; install hnefatafl[train] on this worker."
)


class OptimizerLifecycle:
    def __init__(self, optimizer: torch.optim.Optimizer):
        self.optimizer = optimizer
        self.schedule_free = "train_mode" in optimizer.defaults

    @property
    def mode_failure(self) -> str | None:
        # Transient process state, deliberately outside the serialized optimizer
        # groups/buffers. Only loading a valid checkpoint into a fresh optimizer
        # can recover from an interrupted in-place upstream mode transition.
        return getattr(self.optimizer, "_hnefatafl_mode_failure", None)

    def _mode_failed(self, error: BaseException, action: str) -> None:
        reason = (
            f"Schedule-Free AdamW could not {action}. Evaluation parameters cannot be guaranteed; "
            "checkpoint writing is blocked. Resume from the last valid checkpoint."
        )
        self.optimizer._hnefatafl_mode_failure = reason  # type: ignore[attr-defined]
        error.add_note(reason)

    def _require_valid_mode(self) -> None:
        if self.mode_failure is not None:
            raise RuntimeError(self.mode_failure)

    def train(self) -> None:
        if self.schedule_free:
            self._require_valid_mode()
            try:
                self.optimizer.train()  # type: ignore[attr-defined]
            except BaseException as error:
                self._mode_failed(error, "enter training mode")
                raise

    def inference(self) -> None:
        if self.schedule_free:
            self._require_valid_mode()
            try:
                self.optimizer.eval()  # type: ignore[attr-defined]
            except BaseException as error:
                self._mode_failed(error, "enter evaluation mode")
                raise

    @contextmanager
    def training_parameters(self, model):
        failure = None
        try:
            model.train()
            self.train()
            yield
        except BaseException as error:
            failure = error
            raise
        finally:
            if self.schedule_free:
                self._finish_training(model, failure)

    def _finish_training(self, model, failure: BaseException | None) -> None:
        original = failure
        # Upstream sets train_mode only after switching a whole parameter group.
        # If entry raised halfway, eval() can silently do nothing for that group;
        # it cannot prove restoration. Block further use instead of retrying it.
        restore = (model.eval,) if self.mode_failure else (self.inference, model.eval)
        for operation in restore:
            try:
                operation()
            except BaseException as error:
                if self.mode_failure is None:
                    self._mode_failed(error, "restore model evaluation mode")
                if failure is None:
                    failure = error
                else:
                    failure.add_note(
                        f"Evaluation cleanup also failed: {type(error).__name__}: {error}"
                    )
                    if self.mode_failure not in getattr(failure, "__notes__", ()):
                        failure.add_note(str(self.mode_failure))
        if failure is not None and original is None:
            raise failure

    def base_learning_rate(self) -> float:
        return float(self.optimizer.param_groups[0]["lr"])

    def set_base_learning_rate(self, value: float) -> None:
        if not math.isfinite(value) or not 1e-7 <= value <= 1:
            raise ValueError("learning_rate must be between 1e-7 and 1.0")
        if self.schedule_free:
            self._require_valid_mode()
        # Keep z, moments, lr_max and weight_sum, including the rate history.
        for group in self.optimizer.param_groups:
            group["lr"] = float(value)

    def completed_steps(self) -> int:
        return int(self.optimizer.param_groups[0].get("k", 0))

    def diagnostics(self) -> dict:
        if not self.schedule_free:
            return {}
        group = self.optimizer.param_groups[0]
        steps, warmup = self.completed_steps(), int(group["warmup_steps"])
        return {
            "name": SCHEDULE_FREE,
            "base_learning_rate": self.base_learning_rate(),
            # The rate used by the last completed update, zero before any update.
            # It is not an estimate of parameter-dependent update magnitude.
            "warmup_learning_rate": float(group["scheduled_lr"]),
            "completed_steps": steps,
            "warmup_steps": warmup,
            "warmup_progress": min(1.0, steps / warmup) if warmup else 1.0,
        }


def schedule_free_available() -> bool:
    """Advertise execution support, not just an importable distribution name."""
    try:
        from schedulefree import AdamWScheduleFree

        parameter = torch.nn.Parameter(torch.ones(1))
        optimizer = AdamWScheduleFree([parameter], lr=0.001)
        optimizer.train()
        parameter.grad = torch.ones_like(parameter)
        optimizer.step()
        optimizer.eval()
        return bool(torch.isfinite(parameter).all()) and optimizer.param_groups[0]["k"] == 1
    except Exception:
        # An installed but unusable optional optimizer must not take down the
        # API/worker or be advertised as executable (including upstream errors).
        return False
