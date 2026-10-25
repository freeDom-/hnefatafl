"""Explicit feature/policy gates; no implicit approval of Phase 0 proposals."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class OnlineSettings:
    enabled: bool = False
    # Owner-approved in Phase 2: explicit/random sides and two-game matches.
    creation_policy_approved: bool = True
    retain_seat_without_verification: bool | None = True
    timeout_policy_approved: bool = False
    sweep_seconds: int = 10
    sweep_batch: int = 50

    def __post_init__(self):
        if type(self.sweep_seconds) is not int or not 1 <= self.sweep_seconds <= 300:
            raise ValueError("Online sweep interval must be 1–300 seconds")
        if type(self.sweep_batch) is not int or not 1 <= self.sweep_batch <= 256:
            raise ValueError("Online sweep batch must be 1–256 games")

    @classmethod
    def environment(cls):
        return cls(
            enabled=os.environ.get("HNEFATAFL_ONLINE_ENABLED") == "true",
            # Approval must refer to a separately approved outage procedure.
            # This phase leaves it absent/false in all deployment examples.
            timeout_policy_approved=os.environ.get("HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED")
            == "true",
            sweep_seconds=int(os.environ.get("HNEFATAFL_ONLINE_SWEEP_SECONDS", "10")),
            sweep_batch=int(os.environ.get("HNEFATAFL_ONLINE_SWEEP_BATCH", "50")),
        )
