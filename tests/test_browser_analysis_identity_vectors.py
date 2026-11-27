"""Python side of the shared browser canonical-identity contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_browser_identity_vectors_match_python_serialization():
    path = Path(__file__).resolve().parents[1] / "frontend/src/browserAnalysis/identityVectors.json"
    payload = json.loads(path.read_text())
    assert payload["schema"] == 1
    assert payload["cases"]
    for case in payload["cases"]:
        if case["kind"] == "jarl":
            identity = {
                "state": case["state"],
                "settings": case["settings"],
                "protocol": 1,
            }
        else:
            model = case["model"]
            identity = {
                "state": case["state"],
                "settings": case["settings"],
                "model": {
                    "sha256": model["onnxSha256"],
                    "ruleset": model["ruleset"],
                    "size": model["boardSize"],
                },
                "provider": case["provider"],
                "protocol": 1,
            }
        rendered = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(rendered.encode()).hexdigest() == case["sha256"], case["label"]
