"""Disposable fixtures for deployment recovery tests."""

import json

from hnefatafl.deployment import atomic_json

TEST_RELEASE_DIGEST = "sha256:" + "ab" * 32


def legacy_snapshot(deployment):
    """Create the manifest-only snapshot format used by released updaters."""
    snapshot = deployment.snapshot()
    (snapshot / "snapshot.json").unlink()
    manifest = snapshot / "manifest.json"
    entries = json.loads(manifest.read_text())
    del entries["snapshot.json"]
    atomic_json(manifest, entries)
    return snapshot


def fault_process(source, *arguments):
    """Run a transition in its own interpreter so SIGKILL cannot unwind it."""
    import subprocess
    import sys
    from pathlib import Path

    return subprocess.run(
        [sys.executable, "-c", source, *(str(arg) for arg in arguments)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
