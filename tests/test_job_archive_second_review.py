"""Snapshot lifecycle races, compact import responses and bounded source pins."""

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from auth_support import AuthenticatedClient as TestClient
from test_job_archives import export, job_record, register_model, upload
from test_job_tar_archives import members

from hnefatafl.models import ModelRegistry
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as session:
        yield session


@pytest.mark.parametrize(
    "text,expected",
    [
        ("name=password=hidden", "name=[redacted]"),
        ("name=Bearer hidden", "name=[redacted]"),
        ('{"access_token":"hidden","mode":"train"}', '{"[redacted]","mode":"train"}'),
        ("--my-password='hidden' visible", "[redacted]' visible"),
        ("password=", "password="),
        ("password=" * 3, "[redacted]"),
    ],
)
def test_redaction_preserves_nested_credentials_and_nonsecret_text(text, expected):
    from hnefatafl.job_archives import clean_text

    assert clean_text(text) == expected


def test_redaction_handles_long_diagnostic_words_without_backtracking():
    script = (
        "from hnefatafl.job_archives import clean_text; "
        "word='password'*100000+'='; "
        "assert clean_text(word)==word; "
        "assert clean_text(word+'hidden')=='[redacted]'"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=20)


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("kind", ["train", "frozen_study"])
def test_deleting_referenced_models_never_silently_omits_them(client, monkeypatch, phase, kind):
    registry = ModelRegistry(client.app.state.data_dir / "models")
    if kind == "frozen_study":
        from test_job_archive_review import golden_archive

        restored = upload(client, golden_archive(2, "frozen_study"))
        assert restored.status_code == 201, restored.text
        job = restored.json()
        identifiers = {job["config"]["opponents"][0]["model_id"]}
    else:
        models = [
            register_model(registry, client.app.state.data_dir / f"seed-{i}.pt", seed=i + 10)
            for i in range(3)
        ]
        identifiers = {model["id"] for model in models}
        job, _ = job_record(
            client,
            progress={"best_models": [{"model_id": identifier} for identifier in identifiers]},
        )
    entered, release = threading.Event(), threading.Event()
    original = ModelRegistry.pin_entries

    def paused(self, *args, **kwargs):
        result = original(self, *args, **kwargs) if phase == "after" else None
        entered.set()
        assert release.wait(20)
        return result if phase == "after" else original(self, *args, **kwargs)

    monkeypatch.setattr(ModelRegistry, "pin_entries", paused)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        future = pool.submit(client.get, f"/api/training/jobs/{job['id']}/export")
        try:
            assert entered.wait(10)
            for identifier in identifiers:
                deleted = pool.submit(client.delete, f"/api/models/{identifier}").result(3)
                assert deleted.status_code == 200, deleted.text
            if phase == "after":
                assert (
                    pool.submit(client.delete, f"/api/training/jobs/{job['id']}")
                    .result(3)
                    .status_code
                    == 200
                )
        finally:
            release.set()
        response = future.result(20)
    if phase == "after":
        assert response.status_code == 200, response.text
        manifest = json.loads(members(response.content)["manifest.json"])
        assert {entry["original_id"] for entry in manifest["models"]} == identifiers
        assert not list(client.app.state.data_dir.glob(".job-snapshot-*"))
        assert not list(client.app.state.data_dir.rglob(".archive-pins-*"))
        return
    assert response.status_code == 409, "A model present at snapshot time was silently omitted"
    assert "retry" in response.json()["detail"].lower()
    assert not list(client.app.state.data_dir.glob(".job-snapshot-*"))
    # A fresh snapshot can still export the now-historical, deleted association.
    if kind == "train":
        manifest = json.loads(members(export(client, job))["manifest.json"])
        assert not manifest["models"]
    else:
        retry = client.get(f"/api/training/jobs/{job['id']}/export")
        assert retry.status_code == 400 and "frozen" in retry.json()["detail"].lower()


def test_model_registration_after_snapshot_does_not_change_captured_catalog(client, monkeypatch):
    registry = ModelRegistry(client.app.state.data_dir / "models")
    present = register_model(registry, client.app.state.data_dir / "present.pt", seed=10)
    late_path = client.app.state.data_dir / "late.pt"
    late = register_model(registry, late_path, seed=11)
    registry.delete(late["id"])
    job, _ = job_record(
        client, progress={"best_models": [{"model_id": present["id"]}, {"model_id": late["id"]}]}
    )
    entered, release = threading.Event(), threading.Event()
    original = ModelRegistry.pin_entries

    def paused(self, *args, **kwargs):
        entered.set()
        assert release.wait(20)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ModelRegistry, "pin_entries", paused)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        future = pool.submit(export, client, job)
        try:
            assert entered.wait(10)
            restored = pool.submit(registry.import_model, late_path).result(3)
            assert restored["id"] == late["id"]
        finally:
            release.set()
        manifest = json.loads(members(future.result(20))["manifest.json"])
    assert [entry["original_id"] for entry in manifest["models"]] == [present["id"]]


def test_explicit_compact_import_avoids_reloading_histories(client, monkeypatch):
    from hnefatafl import job_archives

    history = [{"iteration": i, "loss": 1 / (i + 1)} for i in range(1000)]
    job, directory = job_record(client, progress={"iteration": 1000, "history": history})
    (directory / "progress.json").write_text(json.dumps(job["progress"]))
    content = export(client, job)
    read = Path.read_text

    def no_published_reports(path, *args, **kwargs):
        assert not (
            path.name in {"progress.json", "result.json"} and path.parent.parent.name == "jobs"
        ), "The import response reopened an already restored history"
        return read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", no_published_reports)
        response = client.post(
            "/api/training/jobs/import?summary=true", files={"file": ("job.tar.gz", content)}
        )
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["summary"] is True and imported["id"] != job["id"]
    assert imported["progress"]["iteration"] == 1000
    assert "history" not in imported["progress"] and "result" not in imported
    assert len(response.content) < 20000
    detail = client.get(f"/api/training/jobs/{imported['id']}").json()
    assert detail["progress"]["history"] == history
    assert any(
        item["id"] == imported["id"]
        for item in client.get("/api/training/jobs?summary=true").json()
    )
    assert job_archives.FORMAT_VERSION == 3


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_import_summary_retains_resumable_history(client, version):
    from test_job_archive_review import golden_archive

    response = client.post(
        "/api/training/jobs/import?summary=true",
        files={"file": ("legacy.zip", golden_archive(version, "train"))},
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["summary"] is True and summary["resumable"] is True
    assert "history" not in summary["progress"]
    detail = client.get(f"/api/training/jobs/{summary['id']}").json()
    assert len(detail["progress"]["history"]) == 1
    assert detail["model_id"] == summary["model_id"]
    resume = client.get(f"/api/training/jobs/{summary['id']}/resume").json()
    assert resume["can_resume"] and resume["completed_iteration"] == 1


def test_large_metadata_stays_out_of_import_and_navigator_summaries(client, monkeypatch):
    from hnefatafl import job_archives

    metadata = {"history": [{"iteration": i, "note": "x " * 9000} for i in range(1000)]}
    assert len(json.dumps(metadata)) > job_archives.MAX_JSON_BYTES
    job, _ = job_record(client, metrics=metadata)
    response = client.post(
        "/api/training/jobs/import?summary=true",
        files={"file": ("job.tar.gz", export(client, job))},
    )
    assert response.status_code == 201, response.text[:1000]
    summary = response.json()
    assert len(response.content) < 20000 and "history" not in summary["metrics"]
    detail = client.get(f"/api/training/jobs/{summary['id']}").json()
    assert detail["metrics"] == metadata
    # The original job is intentionally large; query only the imported row to
    # prove SQLite replaces imported documents before Python deserialization.
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 200
    loads = json.loads

    def bounded(value, *args, **kwargs):
        assert len(value) < job_archives.MAX_JSON_BYTES
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", bounded)
    listing = client.get("/api/training/jobs?summary=true")
    assert listing.status_code == 200 and len(listing.content) < 20000
    assert listing.json()[0]["id"] == summary["id"]


@pytest.mark.parametrize("failure", ["disk_full", "cancelled"])
def test_cross_device_copy_failure_closes_resources_and_removes_output(
    client, tmp_path, monkeypatch, failure
):
    import asyncio
    import errno
    from hnefatafl import job_archives

    job, directory = job_record(client)
    (directory / "history.json").write_text('{"history":[{"iteration":1}]}')
    (directory / "metrics.json").write_text('{"policy_loss":0.125}')
    link = os.link

    def cross_device(source, destination, *args, **kwargs):
        if Path(destination).parent.name.startswith(".archive-pins-"):
            return link(source, destination, *args, **kwargs)
        raise OSError(errno.EXDEV, "simulated cross-device target")

    opened = []
    count = 0
    copy = job_archives.shutil.copyfileobj

    def interrupted(source, target, *args, **kwargs):
        nonlocal count
        opened.extend((source, target))
        count += 1
        if count == 2:
            target.write(b"partial")
            if failure == "disk_full":
                raise OSError(errno.ENOSPC, "disk full")
            raise asyncio.CancelledError()
        return copy(source, target, *args, **kwargs)

    monkeypatch.setattr(job_archives.os, "link", cross_device)
    monkeypatch.setattr(job_archives.shutil, "copyfileobj", interrupted)
    output = tmp_path / "job.tar.gz"
    with pytest.raises((OSError, asyncio.CancelledError)):
        job_archives.export_job(
            job, directory, ModelRegistry(client.app.state.data_dir / "models"), output, "0.36.0"
        )
    assert all(file.closed for file in opened)
    assert not output.exists()
    assert not list(client.app.state.data_dir.rglob(".archive-pins-*"))
    assert not list(directory.parent.glob("job-snapshot-*"))
    assert json.loads((directory / "history.json").read_text())["history"]


def test_unsupported_source_pinning_fails_explicitly_and_releases_capacity(client, monkeypatch):
    import errno
    from hnefatafl import job_archives

    job, directory = job_record(client)
    (directory / "history.json").write_text('{"history":[{"iteration":1}]}')
    link = os.link

    def unavailable(*args, **kwargs):
        raise OSError(errno.EXDEV, "source cannot establish hard links")

    monkeypatch.setattr(job_archives.os, "link", unavailable)
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 400
    assert "retry" in response.json()["detail"].lower()
    assert not list(client.app.state.data_dir.glob(".job-snapshot-*"))
    assert not list(client.app.state.data_dir.rglob(".archive-pins-*"))
    monkeypatch.setattr(job_archives.os, "link", link)
    assert export(client, job)


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="Linux descriptor-limit reproduction"
)
def test_many_cross_device_artifacts_with_low_descriptor_limit(tmp_path):
    script = r"""
import errno, json, os, resource, shutil, sys
from pathlib import Path
from hnefatafl.job_archives import snapshot_job, export_snapshot, import_job
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.storage import Store
root = Path(sys.argv[1])
directory = root / "jobs" / ("a" * 32)
(directory / "best").mkdir(parents=True)
seed = save_checkpoint(root / "seed.pt", PolicyValueNet("brandubh", 4, 0), {})
for i in range(1200):
    os.link(seed, directory / "best" / f"iteration-{i:06d}.pt")
target = root / "snapshot"
link = os.link
def cross_device(source, destination, *a, **kw):
    if Path(destination).is_relative_to(target):
        raise OSError(errno.EXDEV, "simulated cross-device target")
    return link(source, destination, *a, **kw)
os.link = cross_device
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (min(64, hard), hard))
before = len(list(Path("/proc/self/fd").iterdir()))
job = {"id": "a" * 32, "kind": "train", "status": "completed",
       "config": {"preset": "smoke", "ruleset": "brandubh", "channels": 4, "blocks": 0},
       "created_at": "2026-01-02T03:04:05+00:00"}
with snapshot_job(job, directory, ModelRegistry(root / "models"), target) as snapshot:
    captured = len(list(Path("/proc/self/fd").iterdir()))
    assert captured <= before + 8, (before, captured)
    shutil.rmtree(directory)
    snapshot.materialize()
    assert len(list((target / "best").iterdir())) == 1200
    assert (target / "best/iteration-001199.pt").read_bytes() == seed.read_bytes()
    output = root / "job.tar.gz"
    export_snapshot(snapshot, output, "0.36.0")
assert len(list(Path("/proc/self/fd").iterdir())) <= before
assert not list(root.rglob(".archive-pins-*"))
destination = root / "destination"
(destination / "jobs").mkdir(parents=True)
restored = import_job(output, destination, Store(destination / "jobs.sqlite3"),
                     resume_info=lambda *args: {"can_resume": False})
assert len(list((destination / "jobs" / restored["id"] / "best").iterdir())) == 1200
print(json.dumps({"artifacts": 1200, "before_fds": before, "snapshot_fds": captured}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=90
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
