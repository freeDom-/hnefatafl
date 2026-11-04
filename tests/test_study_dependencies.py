"""Retained Optuna references protect checkpoints without loading tensor data."""

import hashlib
import sqlite3

import optuna
import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def job(client, identifier, *, kind="train", status="completed", config=None):
    directory = client.app.state.data_dir / "jobs" / identifier
    directory.mkdir(exist_ok=True)
    saved = dict(
        id=identifier, kind=kind, status=status, config=config or {}, created_at="2026-01-01"
    )
    client.app.state.store.save_job(saved)
    return saved, directory


def tree(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def retained(client, status="completed"):
    source, source_dir = job(client, "1" * 32)
    (source_dir / "last.pt").write_bytes(b"source full checkpoint")
    config = {"study_name": "Retained search", "training": {"resume": str(source_dir / "last.pt")}}
    owner, directory = job(client, "2" * 32, kind="tune", status=status, config=config)
    storage = "sqlite:///" + str(directory / "study.sqlite3")
    study = optuna.create_study(
        storage=storage, study_name=config["study_name"], direction="maximize"
    )
    contributors = []
    for number, identifier in enumerate(["3" * 32, "4" * 32]):
        contributor, artifact_dir = job(
            client, identifier, kind="tune", config={**config, "storage": storage}
        )
        trial_dir = artifact_dir / f"trial-{number}"
        trial_dir.mkdir()
        for name in ["last.pt", "objective.pt", "best.pt"]:
            (trial_dir / name).write_bytes(f"{identifier}/{name}".encode())
        study.add_trial(
            optuna.trial.create_trial(
                value=number / 2,
                user_attrs={
                    "checkpoint": str(trial_dir / "objective.pt"),
                    "trial_checkpoint": str(trial_dir / "objective.pt"),
                    "last_checkpoint": str(trial_dir / "last.pt"),
                },
            )
        )
        contributors.append(contributor)
    return source, owner, contributors, study


@pytest.mark.parametrize("target", ["source", "owner", "contributor", "winner"])
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_retained_studies_block_deletion_without_mutation(client, target, status, monkeypatch):
    source, owner, contributors, _ = retained(client, status)
    selected = {
        "source": source,
        "owner": owner,
        "contributor": contributors[0],
        "winner": contributors[1],
    }[target]
    root = client.app.state.data_dir / "jobs"
    before_files = tree(root)
    before_jobs = client.app.state.store.jobs()
    monkeypatch.setattr(
        "hnefatafl.models.load_checkpoint",
        lambda *args, **kwargs: pytest.fail("Deletion must read metadata only"),
    )
    response = client.delete(f"/api/training/jobs/{selected['id']}")
    assert response.status_code == 409, response.text
    assert "Retained search" in response.text and "job" in response.text
    assert tree(root) == before_files
    assert client.app.state.store.jobs() == before_jobs


def test_source_deletion_succeeds_after_its_retained_study_is_removed(client):
    source, source_dir = job(client, "1" * 32)
    (source_dir / "last.pt").write_bytes(b"source")
    owner, directory = job(
        client,
        "2" * 32,
        kind="tune",
        config={
            "study_name": "Source dependency",
            "source_checkpoint": f"{source['id']}:last.pt",
        },
    )
    optuna.create_study(
        storage="sqlite:///" + str(directory / "study.sqlite3"), study_name="Source dependency"
    )
    assert client.delete(f"/api/training/jobs/{source['id']}").status_code == 409
    assert client.delete(f"/api/training/jobs/{owner['id']}").status_code == 200
    assert client.delete(f"/api/training/jobs/{source['id']}").status_code == 200
    assert not source_dir.exists()


def test_unrelated_deletion_reads_shared_database_once(client, monkeypatch):
    retained(client)
    unrelated, _ = job(client, "5" * 32)
    from hnefatafl import job_studies

    read = job_studies.study_attributes
    calls = []

    def record(path, name):
        calls.append(path)
        return read(path, name)

    monkeypatch.setattr(job_studies, "study_attributes", record)
    assert client.delete(f"/api/training/jobs/{unrelated['id']}").status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("section", ["progress", "result"])
def test_missing_study_database_with_recorded_history_blocks_unrelated_deletion(client, section):
    study, _ = job(client, "1" * 32, kind="tune")
    study[section] = {"completed_trials": 1}
    client.app.state.store.save_job(study)
    unrelated, directory = job(client, "2" * 32)
    before = client.app.state.store.jobs()
    response = client.delete(f"/api/training/jobs/{unrelated['id']}")
    assert response.status_code == 409, response.text
    assert study["id"] in response.text and "missing" in response.text
    assert directory.is_dir() and client.app.state.store.jobs() == before


@pytest.mark.parametrize(
    "invalid",
    [
        "external",
        "traversal",
        "symlink",
        "corrupt",
        "missing_database",
        "missing_checkpoint",
        "malformed_config",
        "invalid_type",
    ],
)
def test_unknown_or_missing_study_references_fail_safely(client, tmp_path, invalid):
    _, owner, contributors, study = retained(client)
    target = contributors[0]
    root = client.app.state.data_dir / "jobs"
    database = root / owner["id"] / "study.sqlite3"
    path = root / target["id"] / "trial-0" / "objective.pt"
    if invalid == "malformed_config":
        owner["config"] = []
        client.app.state.store.save_job(owner)
    elif invalid == "corrupt":
        database.write_bytes(b"not a SQLite database")
    elif invalid == "missing_database":
        database.unlink()
        owner["config"]["storage"] = "sqlite:///" + str(database)
        client.app.state.store.save_job(owner)
    elif invalid == "missing_checkpoint":
        path.unlink()
    elif invalid == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "outside.pt")
    else:
        value = (
            {}
            if invalid == "invalid_type"
            else (
                str(tmp_path / "outside.pt")
                if invalid == "external"
                else str(path.parent / ".." / "trial-0" / "objective.pt")
            )
        )
        import json

        with sqlite3.connect(database) as db:
            db.execute(
                "UPDATE trial_user_attributes SET value_json=? WHERE key='checkpoint' AND trial_id=(SELECT trial_id FROM trials WHERE number=0)",
                (json.dumps(value),),
            )
    before = tree(root)
    rows = client.app.state.store.jobs()
    response = client.delete(f"/api/training/jobs/{target['id']}")
    assert response.status_code == 409, response.text
    assert tree(root) == before and client.app.state.store.jobs() == rows
