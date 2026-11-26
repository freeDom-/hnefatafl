"""Shared browser engine/source identity.

Used by both production Pyodide bridges (classical analysis and cooperative
neural MCTS). The digest covers the staged engine, encoder and search-driver
sources, so a browser run can be compared with a native run of the same code.
"""

from __future__ import annotations

import hashlib
import inspect

from hnefatafl import ai, encoding, engine, mcts_driver, openings, shield_maiden, symmetry


def engine_identity() -> dict:
    modules = (engine, ai, openings, symmetry, shield_maiden, encoding, mcts_driver)
    digest = hashlib.sha256()
    names = []
    per_module = {}
    try:
        for module in modules:
            names.append(module.__name__)
            source = inspect.getsource(module)
            per_module[module.__name__] = hashlib.sha256(source.encode()).hexdigest()
            digest.update(module.__name__.encode())
            digest.update(b"\0")
            digest.update(source.encode())
            digest.update(b"\0")
    except (OSError, TypeError, IndexError):
        return {"modules": names, "sha256": None, "reason": "source unavailable"}
    return {
        "modules": names,
        "module_sha256": per_module,
        "sha256": digest.hexdigest(),
        "driver_version": mcts_driver.DRIVER_VERSION,
        "encoder_version": encoding.ENCODER_VERSION,
        "search_algorithm": mcts_driver.SEARCH_ALGORITHM,
    }
