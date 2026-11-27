#!/usr/bin/python3
"""Stable entry point for the installed Hnefatafl updater."""

import fcntl
import hashlib
import json
import os
import re
import runpy
import stat
import sys
from pathlib import Path

LAUNCHER_PROTOCOL = 1


def trusted(path, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        info.st_uid != 0
        or info.st_mode & 0o022
        or not kind(info.st_mode)
        or not directory
        and info.st_nlink != 1
    ):
        raise ValueError(f"Unsafe installed updater path: {path}")
    return info


def launch(lock_path=Path("/run/lock/hnefatafl-update.lock")):
    if os.geteuid() != 0:
        raise ValueError("Run this deployment command as root")
    arguments = sys.argv[1:]
    service_guard = arguments == ["--guard-data-root"]
    read_only = (
        "--check" in arguments
        or "--list" in arguments
        or ("--cleanup" in arguments and "--dry-run" in arguments)
    )
    for directory in reversed(lock_path.parents):
        info = directory.lstat()
        if (
            info.st_uid != 0
            or not stat.S_ISDIR(info.st_mode)
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
        ):
            raise ValueError(f"Unsafe deployment lock directory: {directory}")
    descriptor = None
    flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        try:
            if not service_guard:
                descriptor = os.open(
                    lock_path, flags | (os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT), 0o600
                )
        except FileNotFoundError:
            if not read_only:
                raise
        if descriptor is not None:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise ValueError(f"Unsafe deployment lock: {lock_path}")
            try:
                fcntl.flock(
                    descriptor, (fcntl.LOCK_SH if read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB
                )
            except BlockingIOError:
                raise ValueError("Another installation/update/backup is running") from None
        root = Path(__file__).absolute().parent
        for directory in (*reversed(root.parents), root, root / "generations"):
            trusted(directory, directory=True)
        pointer = root / "active.json"
        trusted(pointer)
        active = json.loads(pointer.read_bytes())
        generation_id = active.get("generation") if isinstance(active, dict) else None
        if not isinstance(generation_id, str) or not re.fullmatch(r"[a-f0-9]{64}", generation_id):
            raise ValueError("Invalid active updater generation")
        generation = root / "generations" / generation_id
        trusted(generation, directory=True)
        manifest_path = generation / "manifest.json"
        trusted(manifest_path)
        manifest = manifest_path.read_bytes()
        if hashlib.sha256(manifest).hexdigest() != generation_id:
            raise ValueError("Installed updater manifest failed verification")
        metadata = json.loads(manifest)
        if metadata.get("abi") != active.get("abi") or active.get("schema") != 1:
            raise ValueError("Installed updater protocol does not match the active generation")
        minimum = metadata.get("minimum_launcher_protocol", 1)
        if type(minimum) is not int or not 1 <= minimum <= LAUNCHER_PROTOCOL:
            raise ValueError("The active generation needs a newer deployment launcher")
        files = metadata["files"]
        if "deployment.py" not in files:
            raise ValueError("Installed updater generation is incomplete")
        for name, digest in files.items():
            relative = Path(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or len(relative.parts) not in (1, 2)
            ):
                raise ValueError("Invalid installed updater path in manifest")
            path = generation / relative
            if path.parent != generation:
                trusted(path.parent, directory=True)
            trusted(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Installed updater file failed verification: {name}")
        sys.dont_write_bytecode = True
        command = runpy.run_path(str(generation / "deployment.py"))
        if service_guard:
            return command["guard_service_start"]()
        return command["main"](
            arguments, installed_generation=generation, deployment_lock=descriptor
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)


if __name__ == "__main__":
    try:
        raise SystemExit(launch())
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as error:
        print(
            f"Deployment launcher: {error}. Use the verified installer to refresh the updater.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
