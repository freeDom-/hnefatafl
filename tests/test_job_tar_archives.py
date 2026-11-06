"""Schema-3 integrity, scalable histories, hostile TARs and unlocked snapshots."""

import concurrent.futures
import errno
import gzip
import hashlib
import io
import json
import math
import tarfile
import threading

import pytest
from auth_support import AuthenticatedClient as TestClient
from test_job_archives import export, job_record, upload

from hnefatafl import job_archives
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as instance:
        yield instance


def members(content):
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        return {item.name: archive.extractfile(item).read() for item in archive}


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    return output.getvalue()


def document(files, name):
    manifest = json.loads(files["manifest.json"])
    return json.loads(b"".join(files[part] for part in manifest["documents"][name]["chunks"]))


@pytest.mark.parametrize("iterations", [0, 1, 100, 1000, 1500])
def test_complete_history_roundtrip(client, tmp_path, iterations):
    history = [
        {
            "iteration": i,
            "policy_loss": 1 / (i + 1),
            "evaluation": {"wins": i, "score": 1 / 3, "losses": i + 1},
            "performance": {"games_per_hour": i * 1.2345678901234567},
        }
        for i in range(1, iterations + 1)
    ]
    report = {"iteration": iterations, "history": history, "metrics": {"loss": 1 / 7}}
    job, directory = job_record(client, progress=report, result=report, metrics=report["metrics"])
    for name in ("progress.json", "result.json", "history.json", "evaluation.json", "metrics.json"):
        (directory / name).write_text(json.dumps(report))
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/gzip"
    assert f"hnefatafl-job-{job['id']}.tar.gz" in response.headers["content-disposition"]
    files = members(response.content)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format_version"] == 3
    assert len(files["job.json"]) < 4096 and len(files["manifest.json"]) < 16384
    assert not {"progress", "result", "config"} & json.loads(files["job.json"]).keys()
    assert manifest["documents"]["progress.json"] == manifest["documents"]["result.json"]
    for name, details in manifest["files"].items():
        assert details == {
            "size": len(files[name]),
            "sha256": hashlib.sha256(files[name]).hexdigest(),
        }
    with TestClient(create_app(tmp_path / "restored")) as destination:
        restored = upload(destination, response.content)
        assert restored.status_code == 201, restored.text
        record = restored.json()
        assert record["progress"] == report and record["result"] == report
        assert record["metrics"] == report["metrics"]
        assert record["config"]["ruleset"] == "brandubh"
        restored_dir = destination.app.state.data_dir / "jobs" / record["id"]
        for name in (
            "progress.json",
            "result.json",
            "history.json",
            "evaluation.json",
            "metrics.json",
        ):
            assert json.loads((restored_dir / name).read_text())["history"] == history
        assert not (restored_dir / "data").exists()
        assert not (restored_dir / "metadata").exists()


def test_thousand_iteration_documents_exceed_old_limit_without_losing_data(client, tmp_path):
    # Deliberately exceed the actual 16 MiB guard in EACH report, not just job.json.
    history = [
        {"iteration": i, "evaluation_score": i / 1001, "diagnostic": f"sample {i} " * 2100}
        for i in range(1000)
    ]
    report = {"iteration": 1000, "history": history}
    serialized = json.dumps(report)
    assert len(serialized.encode()) > job_archives.MAX_JSON_BYTES
    job, directory = job_record(client, progress=report, result=report)
    (directory / "progress.json").write_text(serialized)
    (directory / "result.json").write_text(serialized)
    with pytest.raises(job_archives.ArchiveError, match="metadata size limit"):
        job_archives.read_json(directory / "progress.json")
    content = export(client, job)
    files = members(content)
    manifest = json.loads(files["manifest.json"])
    assert len(files["manifest.json"]) < 16384
    assert all(
        len(data) <= job_archives.JSON_CHUNK_BYTES
        for name, data in files.items()
        if name.startswith("data/")
    )
    documents = manifest["documents"]
    assert documents["metadata/progress.json"] == documents["progress.json"]
    assert (
        sum(len(data) for name, data in files.items() if name.startswith("data/"))
        < len(serialized.encode()) + 1024
    )
    del files, serialized
    with TestClient(create_app(tmp_path / "restored")) as target:
        response = upload(target, content)
        assert response.status_code == 201, response.text
        record = response.json()
        assert record["progress"] == report and record["result"] == report
        path = target.app.state.data_dir / "jobs" / record["id"] / "progress.json"
        assert json.loads(path.read_text())["history"] == history


def test_distinct_database_and_disk_histories_are_preserved(client):
    job, directory = job_record(client, progress={"history": [{"iteration": 1}]})
    disk = {"history": [{"iteration": 1}, {"iteration": 2}]}
    (directory / "progress.json").write_text(json.dumps(disk))
    restored = upload(client, export(client, job))
    assert restored.status_code == 201, restored.text
    record = restored.json()
    assert record["progress"] == job["progress"]
    path = client.app.state.data_dir / "jobs" / record["id"] / "progress.json"
    assert json.loads(path.read_text())["history"] == disk["history"]


def test_deterministic_archives_and_cross_filesystem_snapshot_fallback(client, monkeypatch):
    job, directory = job_record(client, progress={"history": [{"iteration": 1}]})
    (directory / "history.json").write_text('{"history":[{"iteration":1}]}')
    original = (directory / "history.json").read_bytes()
    expected = export(client, job)
    assert export(client, job) == expected

    def cross_device(*args):
        raise OSError(errno.EXDEV, "Injected cross-filesystem snapshot")

    monkeypatch.setattr(job_archives.os, "link", cross_device)
    assert export(client, job) == expected
    assert (directory / "history.json").read_bytes() == original


def test_unicode_chunk_boundaries_and_numeric_precision(client, monkeypatch):
    monkeypatch.setattr(job_archives, "JSON_CHUNK_BYTES", 17)
    report = {"text": "雪☀️" * 40, "fraction": 0.12345678901234568, "zero": -0.0, "count": 2**63 - 1}
    job, _ = job_record(client, metrics=report)
    response = upload(client, export(client, job))
    assert response.status_code == 201, response.text
    metrics = response.json()["metrics"]
    assert metrics == report
    assert math.copysign(1, metrics["zero"]) == -1


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/escape",
        "C:/escape",
        "a/../escape",
        "a\\b",
        "./job.json",
        "bad\x00name",
        "a/.. /escape",
        "CON",
        "NUL.json",
        "last.pt.",
    ],
)
def test_tar_traversal_rejected(client, name):
    if "\x00" in name:
        # NUL is a TAR field terminator; paths after it cannot be represented.
        with pytest.raises(job_archives.ArchiveError):
            job_archives.safe_name(name)
        return
    response = upload(client, tar_bytes({name: b"unsafe"}))
    assert response.status_code == 400 and "path" in response.json()["detail"]


@pytest.mark.parametrize(
    "kind",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.DIRTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
        tarfile.GNUTYPE_SPARSE,
        tarfile.XHDTYPE,
        tarfile.GNUTYPE_LONGNAME,
    ],
)
def test_tar_rejects_links_special_and_extended_headers_before_payload_read(client, kind):
    item = tarfile.TarInfo("unsafe")
    item.type = kind
    item.linkname = "../escape"
    item.size = 1 << 40
    content = gzip.compress(item.tobuf(format=tarfile.GNU_FORMAT))
    response = upload(client, content)
    assert response.status_code == 400
    assert "regular files" in response.json()["detail"]
    assert client.app.state.store.jobs() == []


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "conflict",
        "file-size",
        "total-size",
        "count",
        "padding",
        "truncated",
        "crc",
        "format",
        "header",
    ],
)
def test_tar_resource_limits_and_corruption(client, monkeypatch, case):
    content = tar_bytes({"manifest.json": b"{}"})
    if case in {"duplicate", "conflict"}:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name in (
                ["manifest.json", "MANIFEST.JSON"]
                if case == "duplicate"
                else ["best", "best/one.pt"]
            ):
                item = tarfile.TarInfo(name)
                archive.addfile(item, io.BytesIO())
        content = output.getvalue()
    elif case == "file-size":
        monkeypatch.setattr(job_archives, "MAX_FILE_BYTES", 1)
    elif case == "total-size":
        monkeypatch.setattr(job_archives, "MAX_EXTRACTED_BYTES", 1)
    elif case == "count":
        monkeypatch.setattr(job_archives, "MAX_FILES", 0)
    elif case == "padding":
        monkeypatch.setattr(job_archives, "MAX_EXTRACTED_BYTES", 1)
        monkeypatch.setattr(job_archives, "MAX_FILES", 1)
        content = gzip.compress(b"\0" * 100000)
    elif case == "truncated":
        content = content[:-7]
    elif case == "crc":
        content = content[:-8] + b"\xff" * 8
    elif case == "format":
        content = b"not an archive"
    elif case == "header":
        content = gzip.compress(b"invalid TAR header" * 100)
    response = upload(client, content)
    assert response.status_code == 400, response.text
    assert client.app.state.store.jobs() == []
    assert not list(client.app.state.data_dir.glob(".job-import-*"))


@pytest.mark.parametrize(
    "case",
    [
        "missing-chunk",
        "hash",
        "unused",
        "size",
        "reconstructed-size",
        "unsafe-document",
        "conflict",
        "field",
        "version",
        "nonfinite",
        "duplicate-json",
        "nesting",
    ],
)
def test_schema_three_invalid_documents_are_rejected(client, monkeypatch, case):
    job, _ = job_record(
        client, progress={"sample": "x" * 20000} if case == "reconstructed-size" else {}
    )
    files = members(export(client, job))
    manifest = json.loads(files["manifest.json"])
    details = manifest["documents"]["progress.json"]
    if case == "missing-chunk":
        details["chunks"] = ["data/" + "0" * 64 + ".json-part"]
    elif case == "hash":
        files[details["chunks"][0]] = b"wrong"
    elif case == "unused":
        del manifest["documents"]["progress.json"]
        del manifest["documents"]["metadata/progress.json"]
        del manifest["job_fields"]["progress"]
    elif case == "size":
        details["size"] += 1
    elif case == "reconstructed-size":
        # Physical deduplication must not bypass the logical data limit.
        monkeypatch.setattr(job_archives, "MAX_EXTRACTED_BYTES", 30000)
        assert sum(len(value) for value in files.values()) < 30000
    elif case == "unsafe-document":
        manifest["documents"]["../escape.json"] = details
    elif case == "conflict":
        manifest["documents"]["job.json"] = details
    elif case == "field":
        manifest["job_fields"]["pid"] = "metadata/progress.json"
    elif case == "version":
        manifest["format_version"] = 999
    else:
        invalid = {
            "nonfinite": b'{"loss":NaN}',
            "duplicate-json": b'{"loss":1,"loss":2}',
            "nesting": b'{"a":' * 27 + b"{}" + b"}" * 27,
        }[case]
        name = f"data/{hashlib.sha256(invalid).hexdigest()}.json-part"
        files[name] = invalid
        for document_name in ("progress.json", "metadata/progress.json"):
            manifest["documents"][document_name] = {"size": len(invalid), "chunks": [name]}
    used = {name for doc in manifest["documents"].values() for name in doc["chunks"]}
    files = {
        name: value
        for name, value in files.items()
        if not name.startswith("data/") or name in used or case == "unused"
    }
    if case != "hash":
        manifest["files"] = {
            name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for name, value in files.items()
            if name != "manifest.json"
        }
    files["manifest.json"] = json.dumps(manifest).encode()
    response = upload(client, tar_bytes(files))
    assert response.status_code == 400, response.text
    if case == "reconstructed-size":
        assert "reconstructed size" in response.json()["detail"]
    assert len(client.app.state.store.jobs()) == 1


def test_export_compression_releases_locks_and_pins_deleted_files(client, monkeypatch):
    job, directory = job_record(client, progress={"history": [{"iteration": 123}]})
    (directory / "history.json").write_text('{"history":[{"iteration":123}]}')
    entered, release = threading.Event(), threading.Event()
    writer = job_archives.write_tar

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return writer(*args)

    monkeypatch.setattr(job_archives, "write_tar", pause)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        future = executor.submit(export, client, job)
        try:
            assert entered.wait(10)
            assert (
                executor.submit(client.get, "/api/training/jobs?summary=true").result(2).status_code
                == 200
            )
            assert (
                executor.submit(client.get, f"/api/training/jobs/{job['id']}/export")
                .result(2)
                .status_code
                == 409
            )
            assert (
                executor.submit(client.delete, f"/api/training/jobs/{job['id']}")
                .result(2)
                .status_code
                == 200
            )
        finally:
            release.set()
        content = future.result(10)
    assert document(members(content), "history.json") == {"history": [{"iteration": 123}]}
    assert not list(client.app.state.data_dir.glob(".job-snapshot-*"))


def test_export_failure_removes_output_and_releases_slot(client, monkeypatch, tmp_path):
    job, _ = job_record(client)
    output = tmp_path / "partial.tar.gz"
    original = job_archives.write_tar

    def fail(path, *args):
        path.write_bytes(b"partial")
        raise OSError("Injected compression failure")

    monkeypatch.setattr(job_archives, "write_tar", fail)
    monkeypatch.setattr(job_archives.tempfile, "tempdir", str(tmp_path))
    assert client.get(f"/api/training/jobs/{job['id']}/export").status_code == 500
    assert not list(tmp_path.glob("hnefatafl-job-*.tar.gz"))
    monkeypatch.setattr(job_archives, "write_tar", original)
    assert export(client, job)
    assert not output.exists()


def test_import_decompression_does_not_hold_lifecycle_lock(client, monkeypatch):
    job, _ = job_record(client)
    content = export(client, job)
    entered, release = threading.Event(), threading.Event()
    extract = job_archives.extract

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return extract(*args)

    monkeypatch.setattr(job_archives, "extract", pause)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        future = executor.submit(upload, client, content)
        try:
            assert entered.wait(10)
            assert (
                executor.submit(client.get, "/api/training/jobs?summary=true").result(2).status_code
                == 200
            )
        finally:
            release.set()
        assert future.result(10).status_code == 201
