"""Portable job archives are isolated from existing jobs, catalogs and runtime state."""

import concurrent.futures
import hashlib
import io
import json
import stat
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
import torch
from auth_support import AuthenticatedClient as TestClient

from hnefatafl import job_archives
from hnefatafl.ai import choose_move
from hnefatafl.engine import legal_moves, new_game
from hnefatafl.models import ModelRegistry, PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import train


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as client:
        yield client


def job_record(client, *, identifier="a" * 32, status="completed", **extra):
    directory = client.app.state.data_dir / "jobs" / identifier
    directory.mkdir(exist_ok=True)
    job = {
        "id": identifier,
        "kind": "train",
        "status": status,
        "config": {"preset": "smoke", "ruleset": "brandubh"},
        "progress": {},
        "created_at": "2026-01-02T03:04:05+00:00",
        **extra,
    }
    client.app.state.store.save_job(job)
    return job, directory


def upload(client, content):
    return client.post(
        "/api/training/jobs/import", files={"file": ("job.zip", content, "application/zip")}
    )


def export(client, job):
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 200, response.text
    return response.content


@pytest.fixture(scope="module")
def trained_archive(tmp_path_factory):
    root = tmp_path_factory.mktemp("job-source")
    with TestClient(create_app(root)) as source:
        job, directory = job_record(source)
        result = train(
            {
                "preset": "smoke",
                "seed": 17,
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 6,
                "evaluation_max_plies": 2,
                "mcts_time_limit": 5,
                "save_all_best": True,
                "output_dir": str(directory),
                "registry_dir": str(root / "models"),
            }
        )
        _, payload = load_checkpoint(directory / "last.pt")
        job.update(
            config=payload["metadata"]["training_config"],
            progress=result,
            result=result,
            model_id=result["model"]["id"],
            ended_at="2026-01-02T03:05:06+00:00",
            execution="remote",
            worker_id="foreign-worker",
            pid=12345,
            lease_token="secret-lease",
            worker_credentials={"token": "secret-token"},
        )
        source.app.state.store.save_job(job)
        (directory / "config.json").write_text(json.dumps(job["config"]))
        (directory / "worker.log").write_text(
            f"Completed training at {directory}\nAuthorization: Bearer secret-lease\n"
        )
        return export(source, job), job, result


def test_export_inactive_metadata_optional_files_and_no_mutation(client):
    software = {
        "worker": {"package_version": "0.25.0", "git_revision": None},
        "server": {"package_version": "0.26.0", "git_revision": "a" * 40},
        "version_override_active": True,
    }
    job, directory = job_record(
        client, pid=12345, lease_token="must-not-leak", execution="remote", software=software
    )
    before = client.app.state.store.get_job(job["id"])
    (directory / "credentials.json").write_text('{"token":"must-not-leak"}')
    data = export(client, job)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "job.json",
            "config.json",
            "progress.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format_version"] == 1
        assert manifest["original_job_id"] == job["id"]
        for name, info in manifest["files"].items():
            assert info["size"] == len(archive.read(name))
            assert info["sha256"] == hashlib.sha256(archive.read(name)).hexdigest()
        record = json.loads(archive.read("job.json"))
        assert record["provenance"]["original_execution"] == "remote"
        assert record["software"] == software
        assert b"must-not-leak" not in archive.read("job.json")
        assert "pid" not in record and "worker_id" not in record
    assert client.app.state.store.get_job(job["id"]) == before


@pytest.mark.parametrize("status", ["running", "pending", "queued", "cancelling"])
def test_export_rejects_active_without_reconciling(client, status):
    job, _ = job_record(client, status=status)
    assert client.get(f"/api/training/jobs/{job['id']}/export").status_code == 409
    assert client.app.state.store.get_job(job["id"])["status"] == status


def test_real_cpu_roundtrip_resumes_optimizer_and_plays(client, trained_archive):
    content, original, result = trained_archive
    existing, _ = job_record(client)  # The original ID is already occupied.
    imported = upload(client, content)
    assert imported.status_code == 201, imported.text
    job = imported.json()
    root = client.app.state.data_dir
    directory = root / "jobs" / job["id"]
    assert job["id"] != original["id"]
    assert job["status"] == "completed" and job["execution"] == "local"
    assert job["provenance"]["original_job_id"] == original["id"]
    assert job["provenance"]["original_execution"] == "remote"
    assert job["created_at"] == original["created_at"] and job["ended_at"] == original["ended_at"]
    assert not {"pid", "worker_id", "lease_token"} & job.keys()
    assert job["progress"]["history"] == result["history"]
    assert job["progress"]["metrics"] == result["metrics"]
    assert job["progress"]["self_play_games"] == result["self_play_games"]
    assert job["result"]["model"]["self_play_games"] == result["model"]["self_play_games"]
    assert job["resumable"] is True
    assert client.app.state.store.get_job(existing["id"]) == existing
    discovered = client.get("/api/training/jobs").json()
    saved = next(entry for entry in discovered if entry["id"] == job["id"])
    assert saved["model_id"] == job["model_id"]
    assert saved["result"]["model"]["id"] == saved["model_id"]
    assert root.as_posix() in saved["result"]["checkpoint"]
    _, payload = load_checkpoint(directory / "last.pt")
    assert payload["extra"]["best_models"][0]["model_id"] in {
        m["id"] for m in client.get("/api/models").json()
    }
    info = client.get(f"/api/training/jobs/{job['id']}/resume").json()
    assert info["can_resume"] is True and info["completed_iteration"] == 1
    for key in ("games", "batch_size", "learning_rate", "evaluation_games", "seed"):
        assert info["config"][key] == original["config"][key]
    before = job_archives.sha256(directory / "last.pt")
    response = client.post(
        f"/api/training/jobs/{job['id']}/resume", json={"iterations": 2, "device": "cpu"}
    )
    assert response.status_code == 201, response.text
    next_id = response.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        resumed = client.get(f"/api/training/jobs/{next_id}").json()
        if resumed["status"] not in {"running", "cancelling"}:
            break
        time.sleep(0.05)
    assert resumed["status"] == "completed", resumed
    model, next_payload = load_checkpoint(root / "jobs" / next_id / "last.pt")
    assert next_payload["extra"]["iteration"] == 2
    assert next_payload["metadata"]["self_play_games"] == {
        "version": 1,
        "job": 1,
        "baseline": 1,
        "cumulative": 2,
    }
    assert [point["iteration"] for point in next_payload["extra"]["history"]] == [1, 2]
    assert max(v["step"].item() for v in next_payload["optimizer_state"]["state"].values()) > max(
        v["step"].item() for v in payload["optimizer_state"]["state"].values()
    )
    assert any(
        not torch.equal(value, payload["state_dict"][key])
        for key, value in model.state_dict().items()
    )
    assert job_archives.sha256(directory / "last.pt") == before
    registered = ModelRegistry(root / "models").get(job["model_id"])
    inference, _ = load_checkpoint(registered["path"])
    state = new_game("brandubh")
    move = choose_move(
        state, {"algorithm": "neural", "_model": inference, "search_wrapper": "none"}
    )
    assert move in legal_moves(state)


def test_model_deduplication_reimport_and_existing_catalog(client, trained_archive, tmp_path):
    content, _, _ = trained_archive
    first = upload(client, content)
    assert first.status_code == 201, first.text
    registry = ModelRegistry(client.app.state.data_dir / "models")
    first_models = registry.list()
    second = upload(client, content)
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["model_id"] == second.json()["model_id"]
    assert registry.list() == first_models
    # Exporting an imported job must still be portable.
    exported_again = export(client, first.json())
    with TestClient(create_app(tmp_path / "third")) as third:
        response = upload(third, exported_again)
        assert response.status_code == 201, response.text
        assert response.json()["resumable"] is True


def test_export_import_preserves_training_symmetry_mode(client, tmp_path):
    # A real augmented job: the portable configuration must survive export/import.
    created = client.post(
        "/api/training/jobs",
        json={
            "preset": "smoke",
            "ruleset": "brandubh",
            "device": "cpu",
            "cpu_threads": 1,
            "max_game_plies": 4,
            "evaluation_max_plies": 2,
            "mcts_time_limit": 5,
            "seed": 0,
            "training_symmetry_mode": "random_d4",
        },
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = client.get(f"/api/training/jobs/{identifier}").json()
        if job["status"] not in {"running", "cancelling"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    assert job["config"]["training_symmetry_mode"] == "random_d4"
    assert job["progress"]["metrics"]["augmentation"]["exposures"] > 0
    exported = export(client, job)
    with TestClient(create_app(tmp_path / "symmetry")) as target:
        response = upload(target, exported)
        assert response.status_code == 201, response.text
        imported = target.get(f"/api/training/jobs/{response.json()['id']}").json()
        assert imported["config"]["training_symmetry_mode"] == "random_d4"
        assert imported["resumable"] is True


def test_archive_contains_checkpoints_history_models_and_sanitized_logs(trained_archive):
    content, original, _ = trained_archive
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        assert {"last.pt", "best.pt", "inference.pt", "worker.log", "result.json"} <= names
        assert any(name.startswith("best/") for name in names)
        assert any(name.startswith("models/") for name in names)
        for name in names:
            if name.endswith((".json", ".log")):
                assert b"secret-lease" not in archive.read(name)
                assert str(Path(original["config"]["output_dir"])).encode() not in archive.read(
                    name
                )


def mutate(content, change):
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    change(files)
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = {
        name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in files.items()
        if name != "manifest.json"
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize(
    "case",
    [
        "checksum",
        "missing",
        "version",
        "config",
        "checkpoint",
        "architecture",
        "inventory",
        "metadata",
        "nonterminal",
    ],
)
def test_invalid_archive_rejected_without_artifacts(client, case):
    job, _ = job_record(client)
    content = export(client, job)

    def change(files):
        if case == "checksum":
            return
        if case == "missing":
            del files["config.json"]
        elif case == "version":
            manifest = json.loads(files["manifest.json"])
            manifest["format_version"] = 999
            files["manifest.json"] = json.dumps(manifest).encode()
        elif case == "config":
            files["config.json"] = b'{"channels":9999}'
        elif case == "checkpoint":
            files["last.pt"] = b"pickle is not allowed"
        elif case == "architecture":
            path = client.app.state.data_dir / "incompatible.pt"
            save_checkpoint(path, PolicyValueNet("tablut", 4, 0), {})
            files["last.pt"] = path.read_bytes()
        elif case == "inventory":
            files["credentials.txt"] = b"should not be accepted"
        else:
            record = json.loads(files["job.json"])
            record["progress" if case == "metadata" else "status"] = (
                [] if case == "metadata" else "queued"
            )
            files["job.json"] = json.dumps(record).encode()

    content = mutate(content, change)
    if case == "checksum":
        with zipfile.ZipFile(io.BytesIO(content)) as src:
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as dst:
                for name in src.namelist():
                    dst.writestr(name, b"{}" if name == "config.json" else src.read(name))
        content = out.getvalue()
    response = upload(client, content)
    assert response.status_code == 400, response.text
    assert len(client.app.state.store.jobs()) == 1
    assert set((client.app.state.data_dir / "jobs").iterdir()) == {
        client.app.state.data_dir / "jobs" / job["id"]
    }
    assert not list(client.app.state.data_dir.glob(".job-import-*"))


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "C:/absolute", "best/../escape", "a\\b", "./config.json"]
)
def test_traversal_rejected(client, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"unsafe")
    response = upload(client, buffer.getvalue())
    assert response.status_code == 400 and "path" in response.json()["detail"]


@pytest.mark.parametrize(
    "case", ["symlink", "duplicate", "conflict", "ratio", "size", "count", "upload"]
)
def test_zip_resource_and_member_limits(client, monkeypatch, case):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if case == "symlink":
            entry = zipfile.ZipInfo("config.json")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, b"/etc/passwd")
        elif case == "duplicate":
            archive.writestr("config.json", b"{}")
            archive.writestr("CONFIG.JSON", b"{}")
        elif case == "conflict":
            archive.writestr("best", b"{}")
            archive.writestr("best/one.pt", b"{}")
        elif case == "ratio":
            archive.writestr("worker.log", b"0" * 100000)
        else:
            archive.writestr("config.json", b"{}")
            if case == "size":
                monkeypatch.setattr(job_archives, "MAX_EXTRACTED_BYTES", 1)
            elif case == "count":
                monkeypatch.setattr(job_archives, "MAX_FILES", 0)
            else:
                monkeypatch.setattr(job_archives, "MAX_ARCHIVE_BYTES", 1)
    response = upload(client, buffer.getvalue())
    assert response.status_code == (413 if case == "upload" else 400)
    assert client.app.state.store.jobs() == []


@pytest.mark.parametrize("failure", ["model", "database", "filesystem"])
def test_partial_import_rolls_back_new_models_files_and_rows(
    client, trained_archive, monkeypatch, failure
):
    content, _, _ = trained_archive
    registry = ModelRegistry(client.app.state.data_dir / "models")
    original = save_checkpoint(
        client.app.state.data_dir / "existing.pt",
        PolicyValueNet("brandubh", 4, 0),
        {"name": "Keep"},
    )
    registry.import_model(original)
    before = {path.name: path.read_bytes() for path in registry.root.glob("*.pt")}
    before_index = registry.index_path.read_bytes()
    if failure == "model":
        method = ModelRegistry.import_model

        def fail_after_publish(self, *args, **kwargs):
            method(self, *args, **kwargs)
            raise ValueError("Injected model publication failure")

        monkeypatch.setattr(ModelRegistry, "import_model", fail_after_publish)
    elif failure == "database":
        connection = client.app.state.store.connection

        @contextmanager
        def fail_commit():
            with connection() as db:
                yield db
                if db.in_transaction:
                    raise OSError("Injected database commit failure")

        monkeypatch.setattr(client.app.state.store, "connection", fail_commit)
    else:
        method = Path.rename

        def fail_move(self, target):
            if ".job-import-" in str(self):
                raise OSError("Injected move failure")
            return method(self, target)

        monkeypatch.setattr(Path, "rename", fail_move)
    response = upload(client, content)
    assert response.status_code in {400, 500}, response.text
    monkeypatch.undo()
    assert client.app.state.store.jobs() == []
    assert list((client.app.state.data_dir / "jobs").iterdir()) == []
    assert {path.name: path.read_bytes() for path in registry.root.glob("*.pt")} == before
    assert registry.index_path.read_bytes() == before_index


def test_concurrent_imports_allocate_distinct_records(client):
    job, _ = job_record(client)
    content = export(client, job)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: upload(client, content), range(2)))
    assert all(result.status_code == 201 for result in results)
    assert len({result.json()["id"] for result in results}) == 2
    assert len(client.app.state.store.jobs()) == 3


def test_inference_only_and_gpu_metadata_import_on_cpu(client, tmp_path):
    job, directory = job_record(client)
    config = {**job["config"], "device": "cuda"}
    for name in ("last.pt", "inference.pt"):
        save_checkpoint(
            directory / name, PolicyValueNet("brandubh", 4, 0), {"training_config": config}
        )
    job["config"] = config
    client.app.state.store.save_job(job)
    response = upload(client, export(client, job))
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["resumable"] is False
    assert "checkpoint" not in imported
    assert imported["model_id"]
    read = client.get(f"/api/training/jobs/{imported['id']}").json()
    assert "checkpoint" not in read
    assert client.get(f"/api/training/jobs/{imported['id']}/resume").json()["can_resume"] is False


def test_optimization_jobs_rejected_with_explicit_explanation(client):
    job, _ = job_record(
        client, kind="tune", config={"storage": "sqlite:////elsewhere/study.sqlite3"}
    )
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 400
    assert "shared or external" in response.json()["detail"]


def test_owned_optimization_study_roundtrip_and_resume(client, tmp_path):
    import optuna
    from hnefatafl.training import tune

    root = tmp_path / "optimization-source"
    with TestClient(create_app(root)) as source:
        job, directory = job_record(source, kind="tune")
        config = {
            "trials": 1,
            "study_name": "Portable study",
            "seed": 17,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": {
                "preset": "smoke",
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
                "registry_dir": str(root / "models"),
            },
            "output_dir": str(directory),
        }
        result = tune(config)
        optuna.load_study(study_name=config["study_name"], storage=result["storage"]).set_user_attr(
            "worker_token", "must-not-export-study-secret"
        )
        job.update(config=config, result=result, progress=result)
        source.app.state.store.save_job(job)
        (directory / "config.json").write_text(json.dumps(config))
        # Trial inference artifacts can restore associations even after source
        # registry copies were deleted independently of the training data.
        registry = ModelRegistry(root / "models")
        for entry in registry.list():
            registry.delete(entry["id"])
        content = export(source, job)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            database_bytes = archive.read("study.sqlite3")
            assert b"must-not-export-study-secret" not in database_bytes
            assert str(root).encode() not in database_bytes
    response = upload(client, content)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["kind"] == "tune" and imported["status"] == "completed"
    destination = client.app.state.data_dir / "jobs" / imported["id"]
    storage = "sqlite:///" + str(destination / "study.sqlite3")
    assert imported["config"]["storage"] == storage
    assert imported["result"]["storage"] == storage
    study = optuna.load_study(study_name=config["study_name"], storage=storage)
    assert len(study.trials) == 1
    assert study.best_value == result["best_score"]
    assert str(destination) in study.best_trial.user_attrs["checkpoint"]
    assert Path(study.best_trial.user_attrs["checkpoint"]).is_file()
    assert imported["result"]["trials"] == result["trials"]
    models = client.get("/api/models").json()
    assert models
    trial_result = json.loads((destination / "trial-0" / "result.json").read_text())
    assert trial_result["model"]["id"] in {entry["id"] for entry in models}
    resumed = client.post(
        "/api/training/jobs",
        json={
            "kind": "tune",
            "trials": 1,
            "study_name": config["study_name"],
            "search_space": config["search_space"],
            "training": config["training"]
            | {"registry_dir": str(client.app.state.data_dir / "models")},
            "resume_study": imported["id"],
        },
    )
    # Web-managed resumes never accept arbitrary registry paths.
    assert resumed.status_code == 400
    body = {
        "kind": "tune",
        "trials": 1,
        "study_name": config["study_name"],
        "search_space": config["search_space"],
        "training": {k: v for k, v in config["training"].items() if k != "registry_dir"},
        "resume_study": imported["id"],
    }
    resumed = client.post("/api/training/jobs", json=body)
    assert resumed.status_code == 201, resumed.text
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = client.get(f"/api/training/jobs/{resumed.json()['id']}").json()
        if state["status"] not in {"running", "cancelling"}:
            break
        time.sleep(0.05)
    assert state["status"] == "completed", state
    assert len(optuna.load_study(study_name=config["study_name"], storage=storage).trials) == 2
    # The resumed run now shares the source study, so it cannot be exported alone.
    rejected = client.get(f"/api/training/jobs/{state['id']}/export")
    assert rejected.status_code == 400 and "shared or external" in rejected.json()["detail"]


def test_optimization_archive_without_study_is_rejected(client):
    job, _ = job_record(client)

    def change(files):
        config = {"training": {"preset": "smoke"}, "trials": 1}
        record = json.loads(files["job.json"])
        record.update(kind="tune", config=config)
        files["job.json"] = json.dumps(record).encode()
        files["config.json"] = json.dumps(config).encode()
        manifest = json.loads(files["manifest.json"])
        manifest["job_type"] = "tune"
        files["manifest.json"] = json.dumps(manifest).encode()

    response = upload(client, mutate(export(client, job), change))
    assert response.status_code == 400 and "missing its owned study" in response.json()["detail"]


def test_existing_original_model_is_deduplicated_after_path_scrubbing(client, trained_archive):
    content, original, _ = trained_archive
    source_registry = ModelRegistry(Path(original["config"]["registry_dir"]))
    destination_registry = ModelRegistry(client.app.state.data_dir / "models")
    for entry in source_registry.list():
        destination_registry.import_model(entry["path"], "Locally named")
    before = destination_registry.list()
    response = upload(client, content)
    assert response.status_code == 201, response.text
    assert destination_registry.list() == before
    assert response.json()["model_id"] == original["model_id"]


@pytest.mark.parametrize(
    "case", ["running", "multiple", "external", "missing", "schema", "name", "executable"]
)
def test_unsafe_optimization_structures_are_rejected(client, tmp_path, case):
    import optuna
    import sqlite3

    job, directory = job_record(
        client, kind="tune", config={"study_name": "Contained", "training": {"preset": "smoke"}}
    )
    database = directory / "study.sqlite3"
    storage = "sqlite:///" + str(database)
    study = optuna.create_study(storage=storage, study_name="Contained")
    if case == "running":
        study.ask()
    elif case == "multiple":
        optuna.create_study(storage=storage, study_name="Other")
    elif case in {"external", "missing"}:
        checkpoint = (
            tmp_path / "external.pt" if case == "external" else directory / "trial-0" / "best.pt"
        )
        study.add_trial(
            optuna.trial.create_trial(value=0.5, user_attrs={"checkpoint": str(checkpoint)})
        )
    elif case == "name":
        job["config"]["study_name"] = "Different"
        client.app.state.store.save_job(job)
    else:
        with sqlite3.connect(database) as connection:
            if case == "schema":
                connection.execute("UPDATE version_info SET schema_version=999")
            else:
                connection.execute(
                    "CREATE TRIGGER unsafe AFTER INSERT ON trials BEGIN DELETE FROM studies; END"
                )
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 400, response.text
    assert client.app.state.store.get_job(job["id"])["status"] == "completed"


def test_upload_limit_is_enforced_before_multipart_spooling(client, monkeypatch):
    monkeypatch.setattr(job_archives, "MAX_ARCHIVE_BYTES", 1)
    response = client.post(
        "/api/training/jobs/import", content=b"x", headers={"Content-Length": str(2 * 1024 * 1024)}
    )
    assert response.status_code == 413
    response = client.post(
        "/api/training/jobs/import",
        content=iter([b"x" * (600 * 1024)] * 2),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert response.status_code == 413
    assert client.app.state.store.jobs() == []


def test_remote_registry_artifacts_survive_without_a_final_worker_report(client, tmp_path):
    root = client.app.state.data_dir
    path = save_checkpoint(
        root / "inference.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Remote raven"}
    )
    registered = ModelRegistry(root / "models").import_model(path)
    job, _ = job_record(
        client,
        status="failed",
        execution="remote",
        uploaded_models={"inference": registered, "best-1": registered},
    )
    content = export(client, job)
    with TestClient(create_app(tmp_path / "fresh")) as destination:
        response = upload(destination, content)
        assert response.status_code == 201, response.text
        imported = response.json()
        assert imported["status"] == "failed" and imported["execution"] == "local"
        assert imported["model_id"] == imported["progress"]["model"]["id"]
        assert imported["progress"]["best_models"][0]["model_id"] == imported["model_id"]
        assert len(destination.get("/api/models").json()) == 1
        assert "uploaded_models" not in imported


def test_deleted_optional_model_associations_remain_inactive_and_reexportable(client):
    job, _ = job_record(
        client, model_id="b" * 24, result={"model": {"id": "b" * 24, "name": "Deleted model"}}
    )
    response = upload(client, export(client, job))
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["model_id"] is None
    assert imported["result"]["model"] is None
    assert client.get(f"/api/training/jobs/{imported['id']}").status_code == 200
    assert export(client, imported)


def test_archive_preserves_champion_and_benchmark_state(client, tmp_path):
    with TestClient(create_app(tmp_path / "champion-source")) as source:
        job, directory = job_record(source)
        first = train(
            {
                "preset": "smoke",
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
                "output_dir": str(directory),
                "registry_dir": str(source.app.state.data_dir / "models"),
            }
        )
        second_dir = directory.parent / ("b" * 32)
        result = train(
            {
                "resume": first["checkpoint"],
                "iterations": 2,
                "evaluation_opponent": "champion",
                "output_dir": str(second_dir),
            }
        )
        _, saved = load_checkpoint(second_dir / "last.pt")
        job.update(
            id="b" * 32,
            config=saved["metadata"]["training_config"],
            progress=result,
            result=result,
            model_id=result["model"]["id"],
        )
        source.app.state.store.save_job(job)
        content = export(source, job)
    response = upload(client, content)
    assert response.status_code == 201, response.text
    imported = response.json()
    path = client.app.state.data_dir / "jobs" / imported["id"] / "last.pt"
    _, payload = load_checkpoint(path)
    actual = payload["extra"]["evaluation_state"]
    expected = saved["extra"]["evaluation_state"]
    assert actual["active_identity"] == expected["active_identity"]
    assert actual["champion"]["iteration"] == 1
    assert actual["promotions"] == expected["promotions"] == []
    assert set(actual["benchmarks"]) == set(expected["benchmarks"])
    for key, tensor in expected["champion"]["snapshot"]["state_dict"].items():
        assert torch.equal(actual["champion"]["snapshot"]["state_dict"][key], tensor)
    info = client.get(f"/api/training/jobs/{imported['id']}/resume").json()
    assert info["can_resume"] and info["config"]["evaluation_opponent"] == "champion"
