"""Regression coverage for PR #58's summary, snapshot and archive admission review."""

import json
from pathlib import Path

import pytest
from auth_support import AuthenticatedClient as TestClient
from legacy_archive_support import historical_export
from test_job_archives import export, job_record, upload

from hnefatafl.models import ModelRegistry
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as session:
        yield session


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
@pytest.mark.parametrize(
    "kind", ["identical", "older", "missing", "result_only", "result_without_progress"]
)
@pytest.mark.parametrize("version", [1, 2, 3])
def test_imported_navigator_matches_file_reports(
    client, tmp_path, monkeypatch, status, kind, version
):
    with TestClient(create_app(tmp_path / "source")) as source:
        report = {
            "iteration": 1000,
            "history": [{"iteration": i} for i in range(1000)],
            "self_play_games": {"version": 1, "job": 123, "cumulative": 456},
            "metrics": {"policy_loss": 0.123},
        }
        stored = report if kind == "identical" else {"iteration": 1, "history": [{"iteration": 1}]}
        extra = {"progress": stored}
        if kind in {"missing", "result_without_progress"}:
            extra = {}
        job, directory = job_record(source, status=status, **extra)
        if kind in {"missing", "result_without_progress"}:
            job.pop("progress", None)
            source.app.state.store.save_job(job)
        filename = "result.json" if kind.startswith("result_") else "progress.json"
        (directory / filename).write_text(json.dumps(report))
        content = (
            export(source, job)
            if version == 3
            else historical_export(
                job,
                directory,
                ModelRegistry(source.app.state.data_dir / "models"),
                tmp_path / "legacy.zip",
                version,
            )
        )
    response = upload(client, content)
    assert response.status_code == 201, response.text
    restored = response.json()
    path = client.app.state.data_dir / "jobs" / restored["id"]
    full = client.get(f"/api/training/jobs/{restored['id']}").json()
    assert full[filename[:-5]]["history"] == report["history"]
    assert restored[filename[:-5]]["iteration"] == full[filename[:-5]]["iteration"]
    if kind == "result_only":
        expected_iteration = stored["iteration"]
    else:
        expected_iteration = report["iteration"]
    original_open = Path.open

    def no_report_reads(file, *args, **kwargs):
        if file.parent == path and file.name in {"progress.json", "result.json"}:
            raise AssertionError("Navigator must not deserialize archived reports")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_report_reads)
    for _ in range(2):
        summary = next(
            j
            for j in client.get("/api/training/jobs?summary=true").json()
            if j["id"] == restored["id"]
        )
        assert summary["status"] == status
        assert summary["progress"]["iteration"] == expected_iteration
        assert summary["progress"]["self_play_games"]["job"] == 123
        assert "history" not in summary["progress"]
        assert "_import_reports" not in summary
    retained = client.app.state.store.get_job(restored["id"])
    if version == 3:
        if kind not in {"missing", "identical", "result_without_progress"}:
            assert retained["progress"]["history"] == stored["history"]
        assert "history" not in retained.get("_import_reports", {}).get("progress", {})


def test_snapshot_only_touches_referenced_models(client, tmp_path, monkeypatch):
    import os
    from test_job_archives import register_model
    from hnefatafl import job_archives

    root = client.app.state.data_dir
    registry = ModelRegistry(root / "models")
    required = [register_model(registry, root / f"model-{i}.pt", seed=i + 20) for i in range(4)]
    entries = registry._read_index()
    for i in range(3000):
        identifier = f"{i + 10000:024x}"
        os.link(required[0]["path"], registry.root / f"{identifier}.pt")
        entries.append({**entries[0], "id": identifier, "name": f"Unrelated {i}"})
    registry._write_index(entries)
    deleted = "f" * 24
    job, directory = job_record(
        client,
        progress={"model": {"id": required[0]["id"]}},
        uploaded_models={"best-2": required[3]},
    )
    (directory / "metrics.json").write_text(json.dumps({"model_id": required[1]["id"]}))
    from hnefatafl.models import PolicyValueNet, save_checkpoint

    save_checkpoint(
        directory / "last.pt",
        PolicyValueNet("brandubh", 4, 0),
        {},
        extra={"best_models": [{"model_id": required[2]["id"]}, {"model_id": deleted}]},
    )
    touched = []
    link = job_archives.os.link

    def tracked(source, target, *args, **kwargs):
        if Path(source).parent == registry.root:
            touched.append(Path(source).stem)
        return link(source, target, *args, **kwargs)

    monkeypatch.setattr(job_archives.os, "link", tracked)
    monkeypatch.setattr(
        ModelRegistry,
        "list",
        lambda self: pytest.fail("Snapshot must not list/pin the whole catalog"),
    )
    from test_job_tar_archives import members

    files = members(export(client, job))
    manifest = json.loads(files["manifest.json"])
    expected = {entry["id"] for entry in required}
    assert set(touched) == expected and len(touched) == 4
    assert {entry["original_id"] for entry in manifest["models"]} == expected
    assert not any(deleted in name for name in files)


def test_cross_filesystem_copy_is_deferred_and_survives_source_deletion(
    client, tmp_path, monkeypatch
):
    import errno
    import shutil
    from hnefatafl import job_archives

    job, directory = job_record(client)
    content = {"history": [{"iteration": 1, "loss": 0.125}]}
    (directory / "history.json").write_text(json.dumps(content))
    original_copy = shutil.copyfileobj
    held = False
    copies = []

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross filesystem")

    def copy(src, target, *args):
        assert not held, "Snapshot fallback must not copy file contents under global locks"
        copies.append(src)
        return original_copy(src, target, *args)

    monkeypatch.setattr(job_archives.os, "link", cross_device)
    monkeypatch.setattr(job_archives.shutil, "copyfileobj", copy)
    held = True
    snapshot = job_archives.snapshot_job(
        job, directory, ModelRegistry(client.app.state.data_dir / "models"), tmp_path / "pins"
    )
    assert not copies and snapshot.pending
    shutil.rmtree(directory)
    held = False
    with snapshot:
        job_archives.export_snapshot(snapshot, tmp_path / "job.tar.gz", "0.36.0")
    assert copies and all(handle.closed for handle in copies)
    from test_job_tar_archives import members, document

    assert document(members((tmp_path / "job.tar.gz").read_bytes()), "history.json") == content


def test_busy_archives_reject_before_upload_staging_and_keep_api_responsive(client, monkeypatch):
    import concurrent.futures
    import threading
    import tempfile
    from hnefatafl import job_archives

    job, _ = job_record(client)
    content = export(client, job)
    entered, release = threading.Event(), threading.Event()
    extract = job_archives.extract

    def pause(*args):
        entered.set()
        assert release.wait(30)
        return extract(*args)

    staged = []
    tempdir = tempfile.TemporaryDirectory

    def counted(*args, **kwargs):
        if kwargs.get("prefix") == "hnefatafl-upload-":
            staged.append(1)
        return tempdir(*args, **kwargs)

    monkeypatch.setattr(job_archives, "extract", pause)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", counted)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        first = pool.submit(upload, client, content)
        try:
            assert entered.wait(10)
            responses = list(pool.map(lambda _: upload(client, b"x" * 1024 * 1024), range(16)))
            assert all(r.status_code == 409 and r.headers["retry-after"] == "1" for r in responses)
            assert len(staged) == 1
            assert (
                pool.submit(client.get, "/api/training/jobs?summary=true").result(2).status_code
                == 200
            )
            assert client.get(f"/api/training/jobs/{job['id']}/export").status_code == 409
            from fastapi.testclient import TestClient as RawClient

            with RawClient(client.app) as anonymous:
                assert (
                    anonymous.post(
                        "/api/training/jobs/import", files={"file": ("x", b"x")}
                    ).status_code
                    == 401
                )
        finally:
            release.set()
        assert first.result(10).status_code == 201
    assert not list(Path(tempfile.gettempdir()).glob("hnefatafl-upload-*"))
    monkeypatch.setattr(job_archives, "extract", extract)
    assert export(client, job)


def test_admission_never_reads_a_busy_request_body():
    import asyncio
    import threading
    from hnefatafl.job_archives import JobUploadLimit

    lock = threading.Lock()
    lock.acquire()

    async def app(*args):
        raise AssertionError("A busy archive request must not enter parsing or a worker")

    async def receive():
        raise AssertionError("A busy archive request must not read/spool its body")

    messages = []

    async def send(message):
        messages.append(message)

    middleware = JobUploadLimit(app, lock)
    scope = {"type": "http", "method": "POST", "path": "/api/training/jobs/import", "headers": []}
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 409 and lock.locked()
    lock.release()


@pytest.mark.parametrize("failure", ["disconnect", "cancel", "exception"])
def test_early_admission_releases_on_asgi_failure(failure):
    import asyncio
    import threading
    from starlette.requests import ClientDisconnect
    from hnefatafl.job_archives import JobUploadLimit

    lock = threading.Lock()

    async def app(scope, receive, send):
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "disconnect":
            raise ClientDisconnect()
        raise RuntimeError("Unexpected failure before parsing")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    middleware = JobUploadLimit(app, lock)
    scope = {"type": "http", "method": "POST", "path": "/api/training/jobs/import", "headers": []}
    with pytest.raises(BaseException):
        asyncio.run(middleware(scope, receive, send))
    assert not lock.locked()


def test_cancelled_running_worker_keeps_capacity_until_cleanup(tmp_path):
    import threading
    from hnefatafl.job_archives import ArchiveAdmission

    lock = threading.Lock()
    lock.acquire()
    lease = ArchiveAdmission(lock)
    output = tmp_path / "cancelled.tar.gz"
    output.write_bytes(b"partial")
    lease.output = output
    with lease.worker():
        lease.request_finished()
        assert lock.locked() and output.exists()
        from starlette.exceptions import HTTPException

        with pytest.raises(HTTPException, match="ended before publication"):
            lease.ensure_open()
    assert not lock.locked() and not output.exists()
    lease.request_finished()
    assert not lock.locked()
    with pytest.raises(Exception):
        with lease.worker():
            pytest.fail("An abandoned late worker must not begin archive work")


@pytest.mark.parametrize("failure", ["disconnect", "cancel"])
def test_multipart_disconnect_closes_spooled_files_and_releases_capacity(
    client, monkeypatch, failure
):
    import asyncio
    import starlette.formparsers
    from starlette.requests import ClientDisconnect

    files = []
    spooled = starlette.formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        result = spooled(*args, **kwargs)
        files.append(result)
        return result

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracked)
    headers = [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=BOUND")]
    headers.extend(
        (key.encode(), value.encode())
        for key, value in {
            "cookie": "; ".join(f"{key}={value}" for key, value in client.cookies.items()),
            "origin": "http://testserver",
            "x-csrf-token": client.headers["X-CSRF-Token"],
        }.items()
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/training/jobs/import",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "http_version": "1.1",
        "query_string": b"",
        "root_path": "",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }
    part = (
        b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="job.tar.gz"\r\nContent-Type: application/gzip\r\n\r\n'
        + b"x" * (2 * 1024 * 1024)
    )

    async def scenario():
        waiting = asyncio.Event()
        count = 0

        async def receive():
            nonlocal count
            count += 1
            if count == 1:
                return {"type": "http.request", "body": part, "more_body": True}
            waiting.set()
            if failure == "disconnect":
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()

        async def send(message):
            pass

        task = asyncio.create_task(client.app(scope, receive, send))
        await asyncio.wait_for(waiting.wait(), 5)
        if failure == "cancel":
            task.cancel()
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            try:
                await task
            except ClientDisconnect:
                pass  # Framework versions may translate a disconnected body to HTTP 400.

    asyncio.run(scenario())
    assert files and all(file.closed for file in files)
    job, _ = job_record(client)
    assert export(client, job)


def test_large_file_history_keeps_navigator_small_and_database_report_distinct(
    client, tmp_path, monkeypatch
):
    from hnefatafl import job_archives

    vector = [1 / (i + 3) for i in range(1024)]
    report = {
        "iteration": 1000,
        "self_play_games": {"version": 1, "job": 2000, "cumulative": 2000},
        "history": [{"iteration": i, "timings": vector} for i in range(1000)],
    }
    with TestClient(create_app(tmp_path / "source")) as source:
        job, directory = job_record(
            source, progress={"iteration": 1, "history": [{"iteration": 1}]}
        )
        path = directory / "progress.json"
        path.write_text(json.dumps(report))
        assert path.stat().st_size > job_archives.MAX_JSON_BYTES
        response = upload(client, export(source, job))
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["progress"]["history"] == report["history"]
    retained = client.app.state.store.get_job(imported["id"])
    assert retained["progress"]["iteration"] == 1
    assert len(json.dumps(retained)) < 20000
    assert retained["_import_reports"]["progress"]["iteration"] == 1000
    assert "history" not in retained["_import_reports"]["progress"]
    detail = client.get(f"/api/training/jobs/{imported['id']}").json()
    assert detail["progress"]["history"] == report["history"]
    original_loads = json.loads

    def no_large_history(value, *args, **kwargs):
        assert len(value) < job_archives.MAX_JSON_BYTES, "Navigator decoded a full history"
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", no_large_history)
    for _ in range(3):
        response = client.get("/api/training/jobs?summary=true")
        assert len(response.content) < 20000
        assert response.json()[0]["progress"]["iteration"] == 1000


def golden_archive(version, scenario):
    import gzip
    import hashlib

    root = Path(__file__).parent / "fixtures" / "job_archives"
    name = f"schema{version}-{scenario}.zip.gz"
    content = gzip.decompress((root / name).read_bytes())
    provenance = json.loads((root / "provenance.json").read_text())[name]
    assert hashlib.sha256(content).hexdigest() == provenance["zip_sha256"]
    return content


def test_frozen_study_snapshot_survives_deleted_source_and_pins_only_dependencies(
    client, tmp_path, monkeypatch
):
    import errno
    import os
    import shutil
    from hnefatafl import job_archives
    from test_job_tar_archives import members

    restored = upload(client, golden_archive(2, "frozen_study"))
    assert restored.status_code == 201, restored.text
    job = client.app.state.store.get_job(restored.json()["id"])
    root = client.app.state.data_dir
    directory = root / "jobs" / job["id"]
    registry = ModelRegistry(root / "models")
    entries = registry._read_index()
    model_id = job["config"]["opponents"][0]["model_id"]
    source = registry.root / f"{model_id}.pt"
    for i in range(1000):
        identifier = f"{i + 10000:024x}"
        os.link(source, registry.root / f"{identifier}.pt")
        entries.append({**entries[0], "id": identifier})
    registry._write_index(entries)
    pinned_models = []

    def cross_device(source, target):
        if Path(source).parent == registry.root:
            pinned_models.append(Path(source).stem)
        raise OSError(errno.EXDEV, "cross filesystem")

    monkeypatch.setattr(job_archives.os, "link", cross_device)
    with job_archives.snapshot_job(job, directory, registry, tmp_path / "snapshot") as snapshot:
        assert snapshot.pending
        shutil.rmtree(directory)
        output = tmp_path / "study.tar.gz"
        job_archives.export_snapshot(snapshot, output, "0.36.0")
    manifest = json.loads(members(output.read_bytes())["manifest.json"])
    assert pinned_models == [model_id]
    assert [entry["original_id"] for entry in manifest["models"]] == [model_id]
    imported = upload(client, output.read_bytes())
    assert imported.status_code == 201, imported.text
    assert imported.json()["config"]["opponents"][0]["model_id"] == model_id


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("scenario", ["train", "study", "missing_optional", "frozen_study"])
def test_complete_historical_application_archives(client, tmp_path, version, scenario):
    import io
    import time
    import zipfile
    import torch
    from hnefatafl.models import load_checkpoint

    content = golden_archive(version, scenario)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        original = json.loads(archive.read("job.json"))
        assert manifest["format_version"] == version
        response = upload(client, content)
        if version == 1 and scenario == "frozen_study":
            # This genuine old version saved no verifiable frozen-opponent pins.
            # Do not weaken dependency security to invent missing evidence.
            assert response.status_code == 400 and "pin" in response.json()["detail"]
            assert not client.app.state.store.jobs()
            assert not ModelRegistry(client.app.state.data_dir / "models").list()
            return
        assert response.status_code == 201, response.text
        restored = response.json()
        assert restored["status"] == original["status"]
        assert restored["provenance"]["archive_app_version"] == manifest["app_version"]
        if scenario == "missing_optional":
            assert not restored["resumable"]
            return
        full_name = (
            "last.pt" if scenario == "train" else original["result"]["best_trial_checkpoint"][6:]
        )
        before_path = tmp_path / "before.pt"
        before_path.write_bytes(archive.read(full_name))
        after_path = client.app.state.data_dir / "jobs" / restored["id"] / full_name
        before_model, before = load_checkpoint(before_path)
        after_model, after = load_checkpoint(after_path)
        assert all(
            torch.equal(tensor, after_model.state_dict()[key])
            for key, tensor in before_model.state_dict().items()
        )
        assert before["optimizer_state"] is not None and after["optimizer_state"] is not None
        for parameter, state in before["optimizer_state"]["state"].items():
            for key, value in state.items():
                actual = after["optimizer_state"]["state"][parameter][key]
                assert (
                    torch.equal(value, actual)
                    if isinstance(value, torch.Tensor)
                    else value == actual
                )
        assert before["extra"]["python_rng"] == after["extra"]["python_rng"]
        assert torch.equal(before["extra"]["torch_rng"], after["extra"]["torch_rng"])
        assert before["extra"]["history"] == after["extra"]["history"]
        if scenario == "frozen_study":
            import optuna

            model_id = restored["config"]["opponents"][0]["model_id"]
            assert ModelRegistry(client.app.state.data_dir / "models").get(model_id)
            study = optuna.load_study(
                study_name=restored["config"]["study_name"], storage=restored["config"]["storage"]
            )
            assert (
                len(study.trials) == 1 and study.trials[0].value == restored["result"]["best_score"]
            )
            again = upload(client, content)
            assert again.status_code == 201
            assert again.json()["config"]["opponents"][0]["model_id"] == model_id
        if scenario == "train":
            from hnefatafl.ai import choose_move
            from hnefatafl.engine import new_game, legal_moves

            entry = ModelRegistry(client.app.state.data_dir / "models").get(restored["model_id"])
            model, _ = load_checkpoint(entry["path"])
            state = new_game("brandubh")
            assert choose_move(
                state, {"algorithm": "neural", "_model": model, "search_wrapper": "none"}
            ) in legal_moves(state)
            resumed = client.post(
                f"/api/training/jobs/{restored['id']}/resume",
                json={"iterations": 2, "device": "cpu"},
            )
            assert resumed.status_code == 201, resumed.text
            identifier = resumed.json()["id"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                next_job = client.get(f"/api/training/jobs/{identifier}").json()
                if next_job["status"] not in {"running", "cancelling"}:
                    break
                time.sleep(0.05)
            assert next_job["status"] == "completed", next_job
            _, final = load_checkpoint(client.app.state.data_dir / "jobs" / identifier / "last.pt")
            assert final["extra"]["iteration"] == 2


@pytest.mark.parametrize("version", [1, 2])
def test_corrupted_authentic_legacy_archives_are_rejected_without_publication(client, version):
    import io
    import zipfile

    content = golden_archive(version, "missing_optional")
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as old, zipfile.ZipFile(target, "w") as corrupted:
        for name in old.namelist():
            corrupted.writestr(name, b"corrupt" if name == "config.json" else old.read(name))
    result = upload(client, target.getvalue())
    assert result.status_code == 400 and "checksum" in result.json()["detail"]
    assert not client.app.state.store.jobs()
    assert not list((client.app.state.data_dir / "jobs").iterdir())
