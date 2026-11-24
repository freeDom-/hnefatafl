"""External watchdog probe: parent exit / SIGTERM-ignoring descendant cleanup."""

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
import signal
import subprocess
import sys
import time

marker = sys.argv[1]
mode = sys.argv[2]
if not os.path.exists(marker):
    with open(marker, "w") as handle:
        handle.write("1")
    descendant_code = (
        "import signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(300)\n"
    )
    descendant = subprocess.Popen([sys.executable, "-c", descendant_code])
    with open(marker + ".descendant", "w") as handle:
        handle.write(str(descendant.pid))
    if mode == "exit-immediately":
        sys.exit(0)
    if mode == "sigterm-ignore":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        time.sleep(300)
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


def _group_members(pgid: int) -> list[int]:
    if not Path("/proc").is_dir():
        return []
    members = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            text = Path(f"/proc/{name}/stat").read_text()
            tail = text[text.rfind(")") + 2 :].split()
            if int(tail[2]) == pgid:
                members.append(int(name))
        except (OSError, IndexError, ValueError):
            continue
    return members


def main(mode: str) -> int:
    result = {"ok": False, "mode": mode, "failures": []}
    root = Path(tempfile.mkdtemp(prefix="tree-probe-"))
    child = root / "child.py"
    child.write_text(CHILD)
    marker = root / "marker"
    agent = tournament.JsonProcessAgent(
        [sys.executable, str(child), str(marker), mode], timeout=0.6
    )
    state = new_game("brandubh")
    try:
        started = time.monotonic()
        try:
            agent(state, {"pad": "x" * 131072}, 7)
            result["failures"].append("request unexpectedly completed")
        except tournament.AgentTimeoutError:
            pass
        elapsed = time.monotonic() - started
        result["elapsed_s"] = elapsed
        if elapsed > 0.6 + tournament.CLEANUP_ALLOWANCE + 2.0:
            result["failures"].append("cleanup exceeded its bounded allowance")
        group = agent.last_process_group
        result["process_group"] = group
        if group is not None:
            deadline = time.monotonic() + 2.0
            while _group_members(group) and time.monotonic() < deadline:
                time.sleep(0.05)
            result["owned_group_members"] = _group_members(group)
            if result["owned_group_members"]:
                result["failures"].append("owned process-group members survived")
        descendant_file = root / "marker.descendant"
        if descendant_file.is_file():
            pid = int(descendant_file.read_text())
            result["descendant_alive"] = _alive(pid)
            if result["descendant_alive"]:
                result["failures"].append("SIGTERM-ignoring descendant survived")
        else:
            result["failures"].append("probe did not create a descendant")
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
    sys.exit(main(sys.argv[1]))
