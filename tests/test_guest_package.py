"""Pinned runtime packaging: explicit members, unchanged engine, bounded upgrade identity."""

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


def test_runtime_package_integrity_sources_notices_and_update_identity(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_guest.py"
    spec = importlib.util.spec_from_file_location("prepare_guest", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    cache = tmp_path / ".cache/guest-runtime" / module.VERSION
    cache.mkdir(parents=True)
    archive = cache / "pyodide.tgz"
    with tarfile.open(archive, "w:gz") as package:
        for name in (*module.ASSETS, "../../unwanted"):
            content = name.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size = len(content)
            package.addfile(info, io.BytesIO(content))
    monkeypatch.setattr(
        module,
        "INTEGRITY",
        base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode(),
    )
    for name in ("__init__.py", "engine.py", "ai.py", "guest.py"):
        p = tmp_path / "hnefatafl" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("# canonical " + name)
    guest = tmp_path / "frontend/guest"
    (guest / "licenses").mkdir(parents=True)
    (guest / "bridge.py").write_text("__GUEST_ENGINE_VERSION__")
    (guest / "worker.js").write_text("/* worker */")
    (guest / "licenses/NOTICE.md").write_text("required notices")
    module.prepare()
    public = tmp_path / "frontend/public"
    manifest = json.loads((public / "assets/guest-manifest.json").read_text())
    output = public / manifest["base"].lstrip("/")
    for name in ("engine.py", "ai.py"):
        assert (output / "python" / name).read_bytes() == (
            tmp_path / "hnefatafl" / name
        ).read_bytes()
    assert (output / "licenses/NOTICE.md").read_text() == "required notices"
    assert "unwanted" not in str(list(tmp_path.rglob("*")))
    assert (output / "bridge.py").read_text() == manifest["engine_version"]
    # Presentation changes rotate assets, not the replay compatibility identity.
    (guest / "worker.js").write_text("/* updated worker */")
    module.prepare()
    changed = json.loads((public / "assets/guest-manifest.json").read_text())
    assert changed["base"] != manifest["base"] and not output.exists()
    assert changed["engine_version"] == manifest["engine_version"]
    (tmp_path / "hnefatafl/engine.py").write_text("# changed engine")
    module.prepare()
    assert (
        json.loads((public / "assets/guest-manifest.json").read_text())["engine_version"]
        != changed["engine_version"]
    )
    archive.write_bytes(b"corrupt cached upstream archive")
    with pytest.raises(ValueError, match="integrity mismatch"):
        module.prepare()
