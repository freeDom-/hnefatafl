"""Small, optional provenance snapshots in the full training history.

Each recorded row references the iteration of its applicable snapshot. This
prevents an older worker resuming a new checkpoint from silently carrying old
settings into its new rows. An empty version-1 snapshot is an explicit unknown
boundary, never a delta. Learning rates remain per-iteration observations.
"""

import math
from typing import Any


PARAMETER_VERSION = 1
# Numeric fields only: (minimum, maximum, integer). Match normalized trainer
# bounds, and never copy an arbitrary configuration, path or credential here.
PARAMETER_FIELDS = {
    "batch_size": (1, 2048, True),
    "epochs": (1, 100, True),
    "mcts_iterations": (1, 100000, True),
    "mcts_time_limit": (0.001, 300, False),
    "games": (1, 10000, True),
    "replay_buffer_size": (1, 20000, True),
    "weight_decay": (0, 1, False),
    "exploration": (0, 20, False),
    "temperature": (0.01, 10, False),
    "dirichlet_alpha": (0.001, 100, False),
    "dirichlet_fraction": (0, 1, False),
    "max_game_plies": (2, 5000, True),
}


def validated_parameters(value: Any) -> dict:
    """Keep supported, valid fields; malformed/future events break provenance.

    Optional provenance must not prevent an otherwise valid old checkpoint from
    resuming. Keeping an unknown boundary stops a reader from carrying earlier
    settings through a corrupt event. Missing fields are never filled in.
    """
    result: dict = {"version": PARAMETER_VERSION}
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != PARAMETER_VERSION
    ):
        return result
    for key, (minimum, maximum, integer) in PARAMETER_FIELDS.items():
        number = value.get(key)
        if (
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or (integer and type(number) is not int)
        ):
            continue
        if minimum <= number <= maximum and math.isfinite(number):
            result[key] = number
    return result


def parameter_snapshot(config: dict) -> dict:
    """Read the effective normalized configuration after optimizer restoration."""
    snapshot = validated_parameters(
        {"version": PARAMETER_VERSION, **{key: config.get(key) for key in PARAMETER_FIELDS}}
    )
    if len(snapshot) != len(PARAMETER_FIELDS) + 1:
        raise ValueError("Effective training parameters are invalid")
    return snapshot


def restore_parameter_history(value: Any) -> list[dict]:
    """Sanitize optional events in memory, without rewriting the source file."""
    if not isinstance(value, list):
        return []
    history = []
    for row in value:
        if not isinstance(row, dict):
            continue
        if "training_parameters" in row:
            row = {**row, "training_parameters": validated_parameters(row["training_parameters"])}
        if "training_parameters_iteration" in row:
            reference = row["training_parameters_iteration"]
            if type(reference) is not int or not 1 <= reference <= 100000:
                row = {**row, "training_parameters_iteration": None}
        history.append(row)
    return history
