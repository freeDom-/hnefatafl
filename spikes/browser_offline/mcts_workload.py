"""Versioned workload definitions shared by the native/browser harnesses.

A workload records where its positions came from, the exact serialized states
(including repetition history), an independently re-derivable expected name list
and a canonical digest over the ordered positions. Built-in workloads are
reconstructed from ``fixtures.jarl_positions``; custom workloads come from a
caller-supplied serialized position file and are validated by content, never by
name similarity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hnefatafl.engine import RULESETS, State

WORKLOAD_SCHEMA = 1
FIXTURES_SOURCE = "fixtures.jarl_positions"
FIXTURES_VERSION = 1
SOURCES = ("fixtures", "custom", "embedded")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_positions(raw: Any) -> list[dict]:
    """Validate and normalize a serialized position list (order preserved)."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("workload positions must be a non-empty list")
    positions = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("position entry must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("position entry has no name")
        if name in seen:
            raise ValueError(f"duplicate position identifier {name!r}")
        seen.add(name)
        state_raw = entry.get("state")
        if not isinstance(state_raw, dict):
            raise ValueError(f"{name}: position has no serialized state")
        try:
            state = State.from_dict(state_raw)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"{name}: invalid serialized state ({error})") from None
        ruleset = entry.get("ruleset", state.ruleset)
        if ruleset != state.ruleset:
            raise ValueError(f"{name}: ruleset does not match the serialized state")
        kind = entry.get("kind", "custom")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"{name}: invalid position kind")
        positions.append(
            {
                "name": name,
                "ruleset": ruleset,
                "kind": kind,
                "state": state.to_dict(),
            }
        )
    return positions


def positions_sha256(positions: list[dict]) -> str:
    return sha256_text(canonical_json([[entry["name"], entry["state"]] for entry in positions]))


def selection_positions(selection: Any) -> list[dict]:
    """Re-derive built-in positions from a validated fixture selection."""
    if not isinstance(selection, dict):
        raise ValueError("fixture selection must be an object")
    if selection.get("source") != FIXTURES_SOURCE:
        raise ValueError("unsupported fixture selection source")
    version = selection.get("version", FIXTURES_VERSION)
    if version != FIXTURES_VERSION:
        raise ValueError(f"unsupported fixture selection version {version!r}")
    from fixtures import jarl_positions

    rulesets = selection.get("rulesets")
    if (
        not isinstance(rulesets, list)
        or not rulesets
        or any(name not in RULESETS for name in rulesets)
    ):
        raise ValueError("fixture selection has invalid rulesets")
    kinds = selection.get("kinds")
    if kinds is not None and (
        not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) for k in kinds)
    ):
        raise ValueError("fixture selection has invalid kinds")
    limit = selection.get("limit")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("fixture selection has an invalid limit")
    selected = [
        case
        for case in jarl_positions()
        if case["ruleset"] in rulesets and (kinds is None or case["kind"] in kinds)
    ]
    if limit is not None:
        counted: dict[str, int] = {}
        limited = []
        for case in selected:
            if counted.get(case["ruleset"], 0) >= limit:
                continue
            counted[case["ruleset"]] = counted.get(case["ruleset"], 0) + 1
            limited.append(case)
        selected = limited
    return normalize_positions(selected)


def build_fixtures_workload(
    rulesets: list[str], kinds: list[str] | None, limit: int | None
) -> dict:
    selection = {
        "source": FIXTURES_SOURCE,
        "version": FIXTURES_VERSION,
        "rulesets": list(rulesets),
        "kinds": list(kinds) if kinds else None,
        "limit": limit,
    }
    positions = selection_positions(selection)
    return {
        "schema": WORKLOAD_SCHEMA,
        "source": {
            "kind": "fixtures",
            "version": FIXTURES_VERSION,
            "fixtures": FIXTURES_SOURCE,
        },
        "selection": selection,
        "positions": positions,
        "expected_names": [entry["name"] for entry in positions],
        "positions_sha256": positions_sha256(positions),
    }


def build_custom_workload(raw_positions: Any, file_sha256: str | None = None) -> dict:
    """Build a file-backed or intentionally embedded custom workload.

    A file-backed source records the mandatory original-file SHA-256. Callers that
    intentionally embed positions without a file (for example generated fixtures in
    tests) explicitly declare ``embedded`` instead of pretending file provenance.
    """
    positions = normalize_positions(raw_positions)
    if file_sha256 is None:
        source = {"kind": "embedded", "version": WORKLOAD_SCHEMA}
    else:
        source = {
            "kind": "custom",
            "version": WORKLOAD_SCHEMA,
            "file_sha256": _validate_digest(file_sha256, "custom workload file digest"),
        }
    return {
        "schema": WORKLOAD_SCHEMA,
        "source": source,
        "selection": None,
        "positions": positions,
        "expected_names": [entry["name"] for entry in positions],
        "positions_sha256": positions_sha256(positions),
    }


def _validate_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} is malformed")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} is malformed")
    return value


def validate_workload(
    workload: Any,
    *,
    file_positions: list[dict] | None = None,
    file_sha256: str | None = None,
) -> dict:
    """Validate a declared workload and return its normalized positions.

    Built-in workloads are fully reconstructed from the fixture source. Custom
    workloads are validated by content and digest; when the caller supplies the
    original serialized file, the declared positions must match it exactly.
    """
    if not isinstance(workload, dict):
        raise ValueError("workload must be an object")
    if workload.get("schema") != WORKLOAD_SCHEMA:
        raise ValueError(f"unsupported workload schema {workload.get('schema')!r}")
    source = workload.get("source")
    if not isinstance(source, dict) or source.get("kind") not in SOURCES:
        raise ValueError("workload source kind must be fixtures, custom or embedded")
    positions = normalize_positions(workload.get("positions"))
    declared_digest = workload.get("positions_sha256")
    actual_digest = positions_sha256(positions)
    if declared_digest != actual_digest:
        raise ValueError("workload position digest does not match the declared positions")
    expected_names = workload.get("expected_names")
    if (
        not isinstance(expected_names, list)
        or not expected_names
        or any(not isinstance(name, str) for name in expected_names)
        or len(set(expected_names)) != len(expected_names)
    ):
        raise ValueError("workload expected_names must be a non-empty unique list")
    if expected_names != [entry["name"] for entry in positions]:
        raise ValueError("workload expected_names do not match the declared positions")
    if source["kind"] == "fixtures":
        selection = workload.get("selection")
        expected = selection_positions(selection)
        if positions != expected:
            raise ValueError("declared fixture positions do not match the canonical fixture states")
        if source.get("version") != FIXTURES_VERSION:
            raise ValueError("fixture source version does not match")
        if source.get("fixtures") != FIXTURES_SOURCE:
            raise ValueError("fixture source identifier does not match")
    else:
        if workload.get("selection") is not None:
            raise ValueError("custom workload must not declare a fixture selection")
        if source.get("version") != WORKLOAD_SCHEMA:
            raise ValueError("custom workload source version does not match")
        if source["kind"] == "custom":
            declared = _validate_digest(source.get("file_sha256"), "custom workload file digest")
            if file_sha256 is not None:
                _validate_digest(file_sha256, "custom workload file digest")
                if declared != file_sha256:
                    raise ValueError("custom workload file digest does not match the supplied file")
            if file_positions is not None:
                expected = normalize_positions(file_positions)
                if positions != expected:
                    raise ValueError(
                        "custom workload positions do not match the supplied position file"
                    )
        else:
            if source.get("file_sha256") is not None:
                raise ValueError("embedded workload must not claim a file digest")
            if file_positions is not None or file_sha256 is not None:
                raise ValueError("an embedded workload cannot be verified against a position file")
    return {
        "positions": positions,
        "expected_names": expected_names,
        "positions_sha256": actual_digest,
        "source": source,
    }


def load_custom_positions(path: str | Path) -> tuple[list[dict], str]:
    """Load and validate a custom serialized-position file and its digest."""
    data = Path(path).read_bytes()
    try:
        raw = json.loads(data)
    except (TypeError, ValueError) as error:
        raise ValueError(f"custom position file is not valid JSON: {error}") from None
    positions = normalize_positions(raw)
    return positions, hashlib.sha256(data).hexdigest()
