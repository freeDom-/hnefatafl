"""Stable presentation identities, separate from game rules and model weights."""

from __future__ import annotations

import hashlib
import json


RUNES = "ᚠᚢᚦᚨᚱᚲᚷᚹᚺᚾᛁᛃᛇᛈᛉᛊᛏᛒᛖᛗᛚᛜᛞᛟ"


def rune_for_ordinal(ordinal: int) -> str:
    """Bijective base 24: single runes first, then unique rune combinations."""
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("Rune ordinal must be a nonnegative integer")
    number = ordinal + 1
    result = ""
    while number:
        number, digit = divmod(number - 1, len(RUNES))
        result = RUNES[digit] + result
    return result


def effective_ai_config(configuration: dict, profile_id: str | None = None) -> dict:
    """Resolve the effective search settings, including historical defaults."""
    from .ai import ai_profiles

    raw = dict(configuration)
    if profile_id and "algorithm" not in raw:
        profile = next((p for p in ai_profiles() if p["id"] == profile_id), None)
        if profile:
            raw = {**profile["parameters"], **raw, "algorithm": profile["algorithm"]}
    algorithm = raw.get("algorithm", "baseline")
    if algorithm == "alphabeta":
        algorithm = "alpha_beta"
    result = {
        "algorithm": algorithm,
        "seed": int(raw.get("seed", 0)),
        "time_limit": float(raw.get("time_limit", 2.0)),
        "evaluation": "heuristic",
    }
    if algorithm == "alpha_beta":
        result["depth"] = int(raw.get("depth", 2))
    elif algorithm in {"mcts", "neural"}:
        result.update(
            iterations=int(raw.get("iterations", 80)),
            rollout_depth=int(raw.get("rollout_depth", 8)),
            exploration=float(raw.get("exploration", 1.4)),
            temperature=float(raw.get("temperature", 0.0)),
        )
    if algorithm in {"alpha_beta", "mcts", "neural"} and raw.get("model_id"):
        result.update(model_id=raw["model_id"], evaluation="neural")
    return result


def agent_identity(agent: dict) -> str:
    """Use a model or human identity; ordinary search agents ignore game seeds.

    API callers supply normalized effective AI settings. Display names never
    replace a known model/player identity and do not split an equivalent agent.
    """
    if agent.get("kind") == "human":
        if agent.get("player_id"):
            return f"player:{agent['player_id']}"
        # Legacy records without profile IDs retain a stable historical label.
        name = str(agent.get("name", "")).strip().casefold()
        return "legacy-human:" + hashlib.sha256(name.encode()).hexdigest()
    config = effective_ai_config(agent.get("config") or {}, agent.get("profile_id"))
    if config.get("model_id"):
        return f"model:{config['model_id']}"
    effective = {key: value for key, value in config.items() if key != "seed"}
    encoded = json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "agent:" + hashlib.sha256(encoded.encode()).hexdigest()
