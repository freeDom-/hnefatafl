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
from fixtures import search_vectors, vectors  # noqa: E402

VERSION = "0.27.7"
ASSETS = (
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)

# Explicit stdlib-only closure of the current classical search engine. Optional
# tensor/model modules are deliberately excluded, and neural requests are refused.
SOURCE_MODULES = (
    "__init__.py",
    "engine.py",
    "ai.py",
    "openings.py",
    "symmetry.py",
    "tournament_statistics.py",
    "shield_maiden.py",
)
# Digests already recorded by the original pinned-runtime measurement in the repo.
RUNTIME_SHA256 = {
    "pyodide.js": "b4cb23a53aba19c221659b9fb40a2f18281d685691dc06647fb0afd0681baf98",
    "pyodide.asm.js": "6b4c90de5b7172873f04f21884d0e9d2274e305fe32116558fe3e4fbe3618d51",
    "pyodide.asm.wasm": "a50dd1843f805a0b7c45b61037ee0d7b26dfe85efe0e18ef95a34ad24e401f5f",
    "python_stdlib.zip": "16611534726e5d8ac2bd8f926410b2dcb8d6f49aa24913463533b457a2115c16",
    "pyodide-lock.json": "9c45b916001a750f4102fc287494f3eab215909c7535c626a20db80fd6333e2c",
}


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
    for name in ("index.html", "app.js", "worker.js", "analysis-client.js", "bridge.py", "sw.js"):
        shutil.copyfile(Path(__file__).parent / name, output / name)
    package = output / "python/hnefatafl"
    package.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_MODULES:
        shutil.copyfile(ROOT / "hnefatafl" / name, package / name)
    (output / "fixtures.json").write_text(json.dumps(vectors(), separators=(",", ":")))
    (output / "search-fixtures.json").write_text(
        json.dumps(search_vectors(), separators=(",", ":"))
    )
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
            if hashlib.sha256(target.read_bytes()).hexdigest() != RUNTIME_SHA256[name]:
                raise ValueError(f"Pinned runtime digest mismatch: {name}")
    files = [
        "index.html",
        "app.js",
        "worker.js",
        "analysis-client.js",
        "bridge.py",
        "sw.js",
        "fixtures.json",
        "search-fixtures.json",
        *(f"python/hnefatafl/{name}" for name in SOURCE_MODULES),
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
        "python_modules": list(SOURCE_MODULES),
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
