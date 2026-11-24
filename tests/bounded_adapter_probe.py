"""External watchdog probe for blocked-write cleanup (not a pytest module).

Runs in a subprocess with an external timeout so a shutdown regression cannot
hang CI. Prints one JSON result object.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import tournament  # noqa: E402
from hnefatafl.engine import Move, new_game  # noqa: E402

CHILD = r"""
import json
import os
import subprocess
import sys
import time

marker = sys.argv[1]
if not os.path.exists(marker):
    with open(marker, "w") as handle:
        handle.write("1")
    descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    with open(marker + ".descendant", "w") as handle:
        handle.write(str(descendant.pid))
    time.sleep(300)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"id": request["id"], "move": {"source": 0, "target": 1}}), flush=True)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    result = {"ok": False, "failures": []}
    root = Path(tempfile.mkdtemp(prefix="adapter-probe-"))
    child = root / "child.py"
    child.write_text(CHILD)
    marker = root / "marker"
    agent = tournament.JsonProcessAgent([sys.executable, str(child), str(marker)], timeout=0.5)
    timeout = 0.5
    allowance = tournament.CLEANUP_ALLOWANCE
    try:
        state = new_game("brandubh")
        started = time.monotonic()
        try:
            agent(state, {"pad": "x" * 262144}, 7)
            result["failures"].append("blocked write unexpectedly succeeded")
        except tournament.AgentTimeoutError:
            pass
        elapsed = time.monotonic() - started
        result["elapsed_s"] = elapsed
        if elapsed > timeout + allowance + 1.5:
            result["failures"].append("cleanup exceeded timeout plus cleanup allowance")
        if agent.process is not None:
            result["failures"].append("blocked child was not reaped")
        descendant_file = root / "marker.descendant"
        if not descendant_file.is_file():
            result["failures"].append("probe child did not create a descendant")
        else:
            pid = int(descendant_file.read_text())
            deadline = time.monotonic() + 2.0
            while _alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            result["descendant_pid"] = pid
            result["descendant_alive"] = _alive(pid)
            if result["descendant_alive"]:
                result["failures"].append("owned descendant survived cleanup")
        leaked = [
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("agent-") and thread.is_alive()
        ]
        result["leaked_threads"] = leaked
        if leaked:
            result["failures"].append("agent writer/reader threads leaked")
        move = agent(state, {"iterations": 1}, 7)
        result["recovered_move"] = move.to_dict()
        if move != Move(0, 1):
            result["failures"].append("same adapter instance did not recover")
        result["cleanup_failures"] = agent.diagnostics()["cleanup_failures"]
    finally:
        agent.close()
    result["process_after_close"] = agent.process is not None
    if agent.process is not None:
        result["failures"].append("close left the process attached")
    result["ok"] = not result["failures"]
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
