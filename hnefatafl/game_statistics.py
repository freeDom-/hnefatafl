"""Recorded self-play counters, independent of training budgets and tensor formats."""

from typing import Any


def recorded_count(value: Any) -> int | None:
    """JSON/browser-safe counters distinguish unknown from a genuine zero."""
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def self_play_statistics(job: int, baseline: int | None) -> dict:
    return {
        "version": 1,
        "job": job,
        "baseline": baseline,
        "cumulative": baseline + job if baseline is not None else None,
    }


def checkpoint_cumulative(payload: dict) -> int | None:
    """An old full checkpoint's actual counter is a valid resume baseline.

    An inference export without this metadata cannot establish a baseline.
    Never reconstruct counters from iteration history or configured game budgets.
    """
    statistics = payload.get("metadata", {}).get("self_play_games")
    if isinstance(statistics, dict):
        return recorded_count(statistics.get("cumulative"))
    return recorded_count(payload.get("extra", {}).get("games_generated"))


def checkpoint_statistics(payload: dict) -> dict | None:
    statistics = payload.get("metadata", {}).get("self_play_games")
    if isinstance(statistics, dict):
        return dict(statistics)
    cumulative = checkpoint_cumulative(payload)
    if cumulative is not None:
        # Old checkpoints record lineage totals, but not the producing job's baseline.
        return {"version": 1, "job": None, "baseline": None, "cumulative": cumulative}
    return None


def validate_statistics(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or set(value) != {"version", "job", "baseline", "cumulative"}
        or any(
            value[key] is not None and recorded_count(value[key]) is None
            for key in ("job", "baseline", "cumulative")
        )
    ):
        raise ValueError("Invalid self-play game statistics")
    if value["job"] is not None and value["baseline"] is not None:
        if value["cumulative"] != value["job"] + value["baseline"]:
            raise ValueError("Inconsistent self-play game statistics")
