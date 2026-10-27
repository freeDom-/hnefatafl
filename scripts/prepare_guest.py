"""Build a pinned, self-hosted, content-addressed guest runtime (no service worker).

Only explicitly named members of the SHA-512-pinned upstream archive are read.
Downloaded binaries/public build output are ignored, never repository source.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.27.7"
INTEGRITY = (
    "RUSVJlhQdfWfgO9hVHCiXoG+nVZQRS5D9FzgpLJ/VcgGBLSAKoPL8kTiOikxbHQm1kRISeWUBdulEgO26qpSRA=="
)
ASSETS = (
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)
# Engine modules the guest worker loads into the Pyodide filesystem. The set must
# cover every import the packaged modules perform at load time, so a new engine
# dependency is shipped instead of failing inside the browser worker.
GUEST_MODULES = ("__init__.py", "engine.py", "ai.py", "guest.py", "symmetry.py")


def prepare():
    cache = ROOT / ".cache/guest-runtime" / VERSION
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "pyodide.tgz"
    if not archive.exists():
        with urllib.request.urlopen(
            f"https://registry.npmjs.org/pyodide/-/pyodide-{VERSION}.tgz", timeout=60
        ) as response:
            data = response.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("Runtime archive exceeds its size limit")
        if base64.b64encode(hashlib.sha512(data).digest()).decode() != INTEGRITY:
            raise ValueError("Runtime archive integrity mismatch")
        archive.write_bytes(data)
    data = archive.read_bytes()
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != INTEGRITY:
        raise ValueError("Cached runtime integrity mismatch; remove only the corrupt cache file")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as package:
        for name in ASSETS:
            item = package.extractfile("package/" + name)
            if item is None:
                raise ValueError("Incomplete runtime archive")
            files["runtime/" + name] = item.read()
    python = {name: (ROOT / "hnefatafl" / name).read_bytes() for name in GUEST_MODULES}
    bridge = (ROOT / "frontend/guest/bridge.py").read_bytes()
    engine_version = hashlib.sha256(
        VERSION.encode() + b"".join(python.values()) + bridge
    ).hexdigest()
    files.update({"python/" + k: v for k, v in python.items()})
    files["bridge.py"] = bridge.replace(b"__GUEST_ENGINE_VERSION__", engine_version.encode())
    files["worker.js"] = (ROOT / "frontend/guest/worker.js").read_bytes()
    for source in (ROOT / "frontend/guest/licenses").iterdir():
        if source.is_file():
            files["licenses/" + source.name] = source.read_bytes()
    digest = hashlib.sha256(
        b"".join(k.encode() + hashlib.sha256(v).digest() for k, v in sorted(files.items()))
    ).hexdigest()
    public = ROOT / "frontend/public/assets"
    public.mkdir(parents=True, exist_ok=True)
    output = public / ("guest-" + digest)
    if not output.exists():
        staging = public / "guest-staging"
        shutil.rmtree(staging, ignore_errors=True)
        for name, content in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        staging.rename(output)
    # A build contains one package. Existing clients fail closed on an old asset
    # URL and may reload the current UI; no old service-worker cache is retained.
    for old in public.glob("guest-*"):
        if old != output and old.is_dir():
            shutil.rmtree(old)
    manifest = {
        "schema": 1,
        "base": "/assets/" + output.name + "/",
        "engine_version": engine_version,
        "pyodide": VERSION,
        "raw_bytes": sum(map(len, files.values())),
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    (public / "guest-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"Guest runtime {VERSION}: {manifest['raw_bytes']} raw bytes, {len(files)} files; {digest[:12]}"
    )


if __name__ == "__main__":
    prepare()
