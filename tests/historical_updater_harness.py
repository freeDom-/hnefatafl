"""Run a historical updater against disposable releases with real app processes.

The script is executed with PYTHONPATH pointing at the extracted historical tree,
so ``hnefatafl.deployment`` is the actual historical updater. Only systemd and
privilege/ownership orchestration are replaced; the deployment gate, probe
transport, migrations, snapshots and application processes are real.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from hnefatafl.deployment import Deployment


def load_env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if separator:
                values[name] = value
    return values


class Harness(Deployment):
    def __init__(self, config: dict):
        super().__init__(
            config["deployment"],
            base=Path(config["base"]),
            data=Path(config["data"]),
            etc=Path(config["etc"]),
            unit_file=Path(config["unit_file"]),
        )
        self.actions = []
        self.process = None
        self.log_handle = None
        self.log_path = Path(config["log"])
        self.fail_readiness = config.get("fail_readiness")
        self.last_pid = None
        self.last_exit_code = None

    def run(self, args, *, cwd=None, user=False, extra_env=None):
        command = [str(value) for value in args]
        if command and command[0] == "runuser":
            command = command[command.index("--") + 1 :]
        if command and command[0] == "systemctl":
            self.actions.append("systemctl " + " ".join(command[1:]))
            return
        if command and command[0] in {"chown", "chmod"}:
            self.actions.append(command[0])
            return
        env = {**self.env, **(extra_env or {}), "HNEFATAFL_DATA_DIR": str(self.data)}
        subprocess.run(command, cwd=cwd, env=env, check=True)

    def _app_env(self) -> dict[str, str]:
        env = {
            **os.environ,
            **load_env(self.etc / "application.env"),
            **self.env,
            "HNEFATAFL_DATA_DIR": str(self.data),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # The harness itself imports historical updater code through PYTHONPATH;
        # the activated release must import its own application code from cwd.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return env

    def service(self, action):
        self.actions.append("service " + action)
        if action == "stop":
            self._stop()
        elif action == "start":
            self._start()

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        current = self.current.resolve() if self.current.is_symlink() else None
        if current is None:
            raise RuntimeError("No active release to start")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("ab")
        port = str(int(self.config["port"]))
        self.process = subprocess.Popen(
            [
                str(current / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "hnefatafl.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                port,
                "--workers",
                "1",
            ],
            cwd=current,
            env=self._app_env(),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.last_pid = self.process.pid
        self._write_pid_file(self.process.pid)
        if self.fail_readiness == "remove_admin":
            self._inject_remove_admin()

    @staticmethod
    def _process_identity(pid: int) -> dict:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return {"pid": pid, "start": fields[19], "pgrp": int(fields[2])}
        except (OSError, IndexError, ValueError):
            return {"pid": pid}

    def _write_pid_file(self, pid: int) -> None:
        self.log_path.with_suffix(".pid").write_text(
            json.dumps(self._process_identity(pid), sort_keys=True) + "\n"
        )

    def _inject_remove_admin(self) -> None:
        base = f"http://127.0.0.1:{int(self.config['port'])}"
        host = urllib.parse.urlparse(self.config["public_url"]).netloc
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(base + "/api/health", headers={"Host": host}),
                    timeout=2,
                ) as response:
                    if json.load(response) == {"status": "ok"}:
                        break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Target application was not healthy for fault injection")
        with sqlite3.connect(self.data / "hnefatafl.sqlite3") as c:
            c.execute("UPDATE accounts SET active=0 WHERE role='ADMIN'")
            c.commit()
        self.fail_readiness = None

    def _stop(self):
        process = self.process
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                except ProcessLookupError:
                    pass
            self.last_exit_code = process.poll()
        self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        self.log_path.with_suffix(".pid").unlink(missing_ok=True)

    def close(self):
        self._stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    result_path = Path(config["result"])
    harness = Harness(config)
    result = {"ok": False, "actions": harness.actions}
    try:
        action = config["action"]
        if action == "deploy":
            harness.deploy(Path(config["target"]), initialize=False)
        elif action == "recover":
            harness.recover()
        elif action == "rollback":
            harness.recover(rollback=True)
        else:
            raise ValueError("Unsupported harness action")
        result["ok"] = True
    except BaseException as error:
        result.update(
            {
                "error_type": type(error).__name__,
                "error": str(error) if isinstance(error, ValueError) else "",
                "log": harness.log_path.read_text(errors="replace")[-4000:]
                if harness.log_path.is_file()
                else "",
            }
        )
    finally:
        harness.close()
        result["actions"] = harness.actions
        result["application_pid"] = harness.last_pid
        result["application_exit_code"] = harness.last_exit_code
        result["application_stopped"] = harness.process is None
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
