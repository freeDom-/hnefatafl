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

    def train(self) -> None:
        if self.schedule_free:
            self.optimizer.train()  # type: ignore[attr-defined]

    def inference(self) -> None:
        if self.schedule_free:
            self.optimizer.eval()  # type: ignore[attr-defined]

    @contextmanager
    def training_parameters(self, model):
        model.train()
        self.train()
        try:
            yield
        finally:
            # Also applies on cancellation, nonfinite gradients and pruned trials.
            # Ordinary optimizer/model mode behavior stays with existing callers.
            if self.schedule_free:
                self.inference()
                model.eval()

    def base_learning_rate(self) -> float:
        return float(self.optimizer.param_groups[0]["lr"])

    def set_base_learning_rate(self, value: float) -> None:
        if not math.isfinite(value) or not 1e-7 <= value <= 1:
            raise ValueError("learning_rate must be between 1e-7 and 1.0")
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
    except (ImportError, RuntimeError, AttributeError, TypeError):
        return False
