"""Stage a standalone spike under work/; never install into the production app."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fixtures import vectors  # noqa: E402

VERSION = "0.27.7"
ASSETS = (
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/phase0/offline")
    parser.add_argument("--runtime-dir", type=Path, help="Previously downloaded, trusted runtime")
    parser.add_argument(
        "--shell-only", action="store_true", help="No runtime; error-path checks only"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or (ROOT in output.parents and not output.is_relative_to(ROOT / "work")):
        parser.error("Stage inside work/ or outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "app.js", "worker.js", "bridge.py", "sw.js"):
        shutil.copyfile(Path(__file__).parent / name, output / name)
    package = output / "python/hnefatafl"
    package.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "engine.py", "ai.py"):
        shutil.copyfile(ROOT / "hnefatafl" / name, package / name)
    (output / "fixtures.json").write_text(json.dumps(vectors(), separators=(",", ":")))
    if not args.shell_only:
        runtime = output / "runtime"
        runtime.mkdir(exist_ok=True)
        for name in ASSETS:
            target = runtime / name
            if args.runtime_dir:
                shutil.copyfile(args.runtime_dir / name, target)
            else:
                url = f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/{name}"
                print(f"Downloading {url}", flush=True)
                with urllib.request.urlopen(url, timeout=30) as response:
                    target.write_bytes(response.read())
    files = [
        "index.html",
        "app.js",
        "worker.js",
        "bridge.py",
        "sw.js",
        "fixtures.json",
        "python/hnefatafl/__init__.py",
        "python/hnefatafl/engine.py",
        "python/hnefatafl/ai.py",
    ]
    if not args.shell_only:
        files.extend(f"runtime/{name}" for name in ASSETS)
    inventory = []
    for name in files:
        data = (output / name).read_bytes()
        inventory.append(
            {
                "path": name,
                "bytes": len(data),
                "gzip_bytes": len(gzip.compress(data, mtime=0)),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    bundle = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()[:16]
    sw = (output / "sw.js").read_text().replace("__BUNDLE_ID__", bundle)
    (output / "sw.js").write_text(sw)
    # Recompute the service-worker digest after substituting the cache identity.
    for item in inventory:
        if item["path"] == "sw.js":
            data = sw.encode()
            item.update(
                bytes=len(data),
                gzip_bytes=len(gzip.compress(data, mtime=0)),
                sha256=hashlib.sha256(data).hexdigest(),
            )
    manifest = {
        "pyodide": VERSION,
        "runtime_included": not args.shell_only,
        "bundle_id": bundle,
        "files": files + ["assets.json"],
        "inventory": inventory,
    }
    (output / "assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "runtime_included": not args.shell_only,
                "files": len(files),
                "raw_bytes": sum(item["bytes"] for item in inventory),
            }
        )
    )


if __name__ == "__main__":
    main()
