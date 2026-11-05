"""Real Optuna/API deletion groups, failure rollback and restart recovery."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import optuna
import pytest
from auth_support import AuthenticatedClient as TestClient
from fastapi.testclient import TestClient as RawClient
from test_study_dependencies import job, tree

from hnefatafl.server import create_app
from hnefatafl.training_identity import study_lease


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def group(client, count=3):
    source, directory = job(client, "1" * 32)
    (directory / "last.pt").write_bytes(b"preserved source")
    config = {"study_name": "Shared search", "training": {"resume": str(directory / "last.pt")}}
    owner, directory = job(client, "2" * 32, kind="tune", config=config)
    database = directory / "study.sqlite3"
    storage = "sqlite:///" + str(database)
    study = optuna.create_study(storage=storage, study_name="Shared search", direction="maximize")
    study.set_user_attr("hnefatafl_identity", {"version": 2, "source": "fixed source"})
    jobs = [owner]
    for number in range(count - 1):
        contributor, path = job(
            client, f"{number + 3:x}" * 32, kind="tune", config={**config, "storage": storage}
        )
        trial = path / f"trial-{number}"
        trial.mkdir()
        for name in ["objective.pt", "last.pt", "best.pt"]:
            (trial / name).write_bytes(f"{number}/{name}".encode())
        study.add_trial(
            optuna.trial.create_trial(
                value=number / 10,
                user_attrs={
                    "checkpoint": str(trial / "objective.pt"),
                    "trial_checkpoint": str(trial / "objective.pt"),
                    "last_checkpoint": str(trial / "last.pt"),
                },
            )
        )
        jobs.append(contributor)
    # Normal tuning leaves this operational lock in place. Establish it before
    # byte-preservation assertions; it is not a neural checkpoint.
    with study_lease({"storage": storage, "output_dir": str(directory)}):
        pass
    return source, jobs, database


def preview(client, identifier):
    response = client.get(f"/api/training/jobs/{identifier}/study-deletion-preview")
    assert response.status_code == 200, response.text
    return response.json()


def remove(client, identifier, token):
    return client.delete(
        f"/api/training/jobs/{identifier}/study", params={"confirmation_token": token}
    )


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("start", [0, -1])
def test_entire_group_deletes_from_any_member_and_keeps_other_resources(
    client, count, start, monkeypatch
):
    source, members, _ = group(client, count)
    root, store = client.app.state.data_dir, client.app.state.store
    unrelated, directory = job(client, "9" * 32)
    (directory / "last.pt").write_bytes(b"unrelated")
    model = root / "models" / ("a" * 24 + ".pt")
    model.write_bytes(b"registered inference copy")
    with store.connection() as db:
        db.execute(
            "INSERT INTO games(id,version,created_at,data) VALUES('saved-game',0,'now','{}')"
        )
        db.execute(
            "INSERT INTO tournaments(id,created_at,data) VALUES('saved-tournament','now',?)",
            (json.dumps({"id": "saved-tournament", "status": "completed"}),),
        )
    monkeypatch.setattr(
        "hnefatafl.models.load_checkpoint",
        lambda *args, **kwargs: pytest.fail("Deletion must not load weights"),
    )
    if count > 1:
        for member in members:
            assert client.delete(f"/api/training/jobs/{member['id']}").status_code == 409
    target = members[start]["id"]
    before = tree(root / "jobs")
    records = store.jobs()
    report = preview(client, target)
    assert report["can_delete"] and report["study_identity"]
    assert report["owner_job_id"] == members[0]["id"]
    assert {item["id"] for item in report["jobs"]} == {member["id"] for member in members}
    assert report["preserved_source_job_ids"] == [source["id"]]
    assert report["external_dependencies"] == []
    assert tree(root / "jobs") == before and store.jobs() == records
    response = remove(client, target, report["confirmation_token"])
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_pending"] is False
    assert set(response.json()["job_ids"]) == {member["id"] for member in members}
    assert store.get_job(source["id"]) == source and store.get_job(unrelated["id"]) == unrelated
    assert (root / "jobs" / source["id"] / "last.pt").read_bytes() == b"preserved source"
    assert (
        directory / "last.pt"
    ).read_bytes() == b"unrelated" and model.read_bytes() == b"registered inference copy"
    for member in members:
        assert store.get_job(member["id"]) is None and not (root / "jobs" / member["id"]).exists()
    with store.connection() as db:
        assert db.execute("SELECT data FROM games WHERE id='saved-game'").fetchone()[0] == "{}"
        assert json.loads(
            db.execute("SELECT data FROM tournaments WHERE id='saved-tournament'").fetchone()[0]
        ) == {"id": "saved-tournament", "status": "completed"}
    assert remove(client, target, report["confirmation_token"]).status_code == 404


@pytest.mark.parametrize("kind", ["train", "tune"])
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "running"])
def test_external_jobs_block_group_deletion_without_mutation(client, kind, status):
    _, members, _ = group(client)
    root = client.app.state.data_dir / "jobs"
    checkpoint = root / members[-1]["id"] / "trial-1" / "objective.pt"
    config = (
        {"resume": str(checkpoint)}
        if kind == "train"
        else {"study_name": "Other study", "training": {"resume": str(checkpoint)}}
    )
    dependent, _ = job(client, "a" * 32, kind=kind, status=status, config=config)
    before, records = tree(root), client.app.state.store.jobs()
    report = preview(client, members[0]["id"])
    assert not report["can_delete"] and report["external_dependencies"][0]["id"] == dependent["id"]
    response = remove(client, members[-1]["id"], report["confirmation_token"])
    assert response.status_code == 409 and dependent["id"] in response.text
    assert tree(root) == before and client.app.state.store.jobs() == records


@pytest.mark.parametrize("status", ["pending", "queued", "running", "cancelling"])
def test_active_members_block_without_reconciling_or_stopping_them(client, status):
    _, members, _ = group(client)
    member = members[-1]
    member["status"] = status
    client.app.state.store.save_job(member)
    before = client.app.state.store.jobs()
    report = preview(client, members[0]["id"])
    assert not report["can_delete"]
    response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 409 and member["id"] in response.text
    assert client.app.state.store.jobs() == before


def test_actual_live_local_process_blocks_even_with_terminal_status(client):
    _, members, _ = group(client)
    member = members[-1]
    config = client.app.state.data_dir / "jobs" / member["id"] / "config.json"
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "hnefatafl.cli", str(config)]
    )
    try:
        member["pid"] = process.pid
        client.app.state.store.save_job(member)
        report = preview(client, members[0]["id"])
        assert not report["can_delete"] and report["jobs"][-1]["worker_alive"]
        assert remove(client, member["id"], report["confirmation_token"]).status_code == 409
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_unfinished_remote_lease_blocks_even_with_terminal_status(client):
    _, members, _ = group(client)
    member = members[-1]
    member.update(
        execution="remote",
        lease_hash="saved lease",
        lease_expires=time.time() + 120,
        finish_received=False,
    )
    client.app.state.store.save_job(member)
    assert not preview(client, members[0]["id"])["can_delete"]
    member["finish_received"] = True
    client.app.state.store.save_job(member)
    assert preview(client, members[0]["id"])["can_delete"]


@pytest.mark.parametrize("state", ["FAIL", "PRUNED"])
def test_trial_artifact_owner_discovers_contributor_without_selected_checkpoint(client, state):
    _, members, database = group(client, 1)
    contributor, directory = job(
        client, "a" * 32, kind="tune", status="failed", config=members[0]["config"]
    )
    # Older contributor records can lack storage. Persisted trial ownership is
    # authoritative; a source checkpoint alone must never imply membership.
    study = optuna.load_study(storage="sqlite:///" + str(database), study_name="Shared search")
    study.add_trial(
        optuna.trial.create_trial(
            state=optuna.trial.TrialState[state], user_attrs={"artifact_owner": str(directory)}
        )
    )
    report = preview(client, contributor["id"])
    assert {item["id"] for item in report["jobs"]} == {members[0]["id"], contributor["id"]}
    assert remove(client, contributor["id"], report["confirmation_token"]).status_code == 200


def test_new_external_dependency_after_preview_is_rechecked(client):
    _, members, _ = group(client)
    report = preview(client, members[0]["id"])
    path = client.app.state.data_dir / "jobs" / members[-1]["id"] / "trial-1" / "objective.pt"
    external, _ = job(client, "a" * 32, config={"resume": str(path)})
    before, records = tree(client.app.state.data_dir / "jobs"), client.app.state.store.jobs()
    response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 409 and external["id"] in response.text
    assert (
        tree(client.app.state.data_dir / "jobs") == before
        and client.app.state.store.jobs() == records
    )


def test_cli_study_lease_blocks_execution_without_terminating_or_mutating(client):
    _, members, database = group(client)
    report = preview(client, members[0]["id"])
    before = tree(client.app.state.data_dir / "jobs")
    with study_lease({"storage": "sqlite:///" + str(database)}):
        response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 409 and "already running" in response.text
    assert tree(client.app.state.data_dir / "jobs") == before


def test_overlapping_group_deletions_are_serialized(client):
    source, members, _ = group(client)
    report = preview(client, members[0]["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda member: remove(client, member["id"], report["confirmation_token"]),
                [members[0], members[-1]],
            )
        )
    assert sorted(result.status_code for result in results) == [200, 404]
    assert client.app.state.store.jobs() == [source]


def test_startup_rejects_unsafe_recovery_journal_without_following_paths(client, tmp_path):
    root = client.app.state.data_dir
    directory = root / "jobs" / ".study-deletions" / ("a" * 32)
    directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.pt").write_bytes(b"keep")
    (directory / "manifest.json").write_text(
        json.dumps({"version": 1, "job_ids": ["../../outside"]})
    )
    with pytest.raises(ValueError, match="journal"):
        create_app(root)
    assert (outside / "precious.pt").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "problem",
    [
        "corrupt",
        "database_symlink",
        "checkpoint_symlink",
        "traversal",
        "missing_owner",
        "foreign_owner",
        "attribute_type",
    ],
)
def test_unsafe_or_ambiguous_metadata_never_enlarges_or_deletes_scope(client, tmp_path, problem):
    _, members, database = group(client)
    root = client.app.state.data_dir / "jobs"
    checkpoint = root / members[1]["id"] / "trial-0" / "objective.pt"
    if problem == "attribute_type":
        with sqlite3.connect(database) as db:
            db.execute(
                "UPDATE trial_user_attributes SET key=? WHERE key='checkpoint'",
                (sqlite3.Binary(b"checkpoint"),),
            )
    elif problem == "corrupt":
        database.write_bytes(b"not sqlite")
    elif problem == "database_symlink":
        other = tmp_path / "external.sqlite3"
        database.rename(other)
        database.symlink_to(other)
    elif problem == "checkpoint_symlink":
        checkpoint.unlink()
        checkpoint.symlink_to(tmp_path / "outside.pt")
    elif problem == "missing_owner":
        with client.app.state.store.discard_job(members[1]["id"]):
            pass
    elif problem == "foreign_owner":
        participant = members[1]
        participant["config"]["storage"] = "sqlite:///" + str(
            root / participant["id"] / "study.sqlite3"
        )
        client.app.state.store.save_job(participant)
    else:
        with sqlite3.connect(database) as db:
            db.execute(
                "UPDATE trial_user_attributes SET value_json=? WHERE key='checkpoint'",
                (json.dumps(str(checkpoint.parent / ".." / "trial-0" / checkpoint.name)),),
            )
    before, records = tree(root), client.app.state.store.jobs()
    assert (
        client.get(f"/api/training/jobs/{members[0]['id']}/study-deletion-preview").status_code
        == 409
    )
    assert remove(client, members[0]["id"], "0" * 64).status_code == 409
    assert tree(root) == before and client.app.state.store.jobs() == records


def test_fresh_scope_is_required_and_shared_database_is_read_once_per_calculation(
    client, monkeypatch
):
    _, members, database = group(client)
    from hnefatafl import job_studies

    original = job_studies.study_attributes
    calls = []

    def counted(path, name):
        calls.append(path)
        return original(path, name)

    monkeypatch.setattr(job_studies, "study_attributes", counted)
    report = preview(client, members[0]["id"])
    extra, _ = job(
        client,
        "a" * 32,
        kind="tune",
        config={**members[0]["config"], "storage": "sqlite:///" + str(database)},
    )
    response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 409 and "scope changed" in response.text
    assert calls == [database, database]
    assert client.app.state.store.get_job(extra["id"]) is not None
    report = preview(client, extra["id"])
    assert remove(client, extra["id"], report["confirmation_token"]).status_code == 200
    assert calls == [database] * 4


@pytest.mark.parametrize("failure", ["rename", "database_statement", "database_after_staging"])
def test_failed_staging_or_database_transaction_restores_all_jobs(client, monkeypatch, failure):
    _, members, database = group(client)
    root, store = client.app.state.data_dir / "jobs", client.app.state.store
    report = preview(client, members[0]["id"])
    before, records = tree(root), store.jobs()
    if failure == "rename":
        original = os.rename

        def fail_second(source, destination):
            if Path(source) == root / members[1]["id"]:
                raise OSError("Injected rename failure")
            return original(source, destination)

        monkeypatch.setattr("hnefatafl.job_deletion.os.rename", fail_second)
    elif failure == "database_statement":
        with store.connection() as db:
            db.execute(
                f"CREATE TRIGGER reject_group BEFORE DELETE ON jobs WHEN old.id='{members[1]['id']}' BEGIN SELECT RAISE(ABORT,'Injected delete failure'); END"
            )
    else:
        original_discard = store.discard_jobs

        @contextmanager
        def fail_before_commit(ids):
            with original_discard(ids):
                yield
                raise sqlite3.OperationalError("Injected transaction failure after staging")

        monkeypatch.setattr(store, "discard_jobs", fail_before_commit)
    response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 503, response.text
    assert store.jobs() == records and tree(root) == before
    assert database.is_file()


def test_cleanup_failure_is_committed_consistently_and_retried_on_restart(client, monkeypatch):
    _, members, _ = group(client)
    from hnefatafl import job_deletion

    root, store = client.app.state.data_dir, client.app.state.store
    report = preview(client, members[0]["id"])
    original = job_deletion.shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if Path(path).parent.parent.name == ".study-deletions":
            raise OSError("Injected cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(job_deletion.shutil, "rmtree", fail_cleanup)
    response = remove(client, members[0]["id"], report["confirmation_token"])
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_pending"]
    assert all(store.get_job(member["id"]) is None for member in members)
    assert all(not (root / "jobs" / member["id"]).exists() for member in members)
    assert list((root / "jobs" / ".study-deletions").iterdir())
    monkeypatch.undo()
    create_app(root)
    assert not list((root / "jobs" / ".study-deletions").iterdir())


def test_failed_rollback_is_quarantined_and_blocks_reads_until_recovery(client, monkeypatch):
    _, members, _ = group(client)
    root, store = client.app.state.data_dir / "jobs", client.app.state.store
    report = preview(client, members[0]["id"])
    records, before = store.jobs(), tree(root)
    original = os.rename

    def fail_stage_and_restore(source, destination):
        if Path(source) == root / members[1]["id"] or Path(destination) == root / members[0]["id"]:
            raise OSError("Injected temporary filesystem outage")
        return original(source, destination)

    monkeypatch.setattr("hnefatafl.job_deletion.os.rename", fail_stage_and_restore)
    assert remove(client, members[0]["id"], report["confirmation_token"]).status_code == 503
    assert store.jobs() == records
    assert client.get("/api/training/jobs").status_code == 503
    monkeypatch.undo()
    assert preview(client, members[0]["id"])["can_delete"]
    assert store.jobs() == records and tree(root) == before


@pytest.mark.parametrize("committed", [False, True])
def test_process_exit_recovers_from_durable_journal_on_startup(client, committed):
    source, members, _ = group(client)
    root, store = client.app.state.data_dir, client.app.state.store
    ids = [member["id"] for member in members]
    code = """
import os, sys
from pathlib import Path
from hnefatafl.storage import Store
from hnefatafl.job_deletion import StudyDeletion
root = Path(sys.argv[1])
ids = sys.argv[3:]
manager = StudyDeletion(root / 'jobs', Store(root / 'hnefatafl.sqlite3'))
if sys.argv[2] == 'committed':
    original = manager.recover
    def crash_after_commit():
        if not (set(ids) & manager.store.job_ids()):
            os._exit(42)
        original()
    manager.recover = crash_after_commit
else:
    original = os.rename
    def crash_during_staging(source, destination):
        original(source, destination)
        os._exit(42)
    os.rename = crash_during_staging
manager.delete(ids)
"""
    before = tree(root / "jobs")
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), "committed" if committed else "staging", *ids],
        timeout=20,
    )
    assert result.returncode == 42
    create_app(root)
    if committed:
        assert all(store.get_job(identifier) is None for identifier in ids)
        assert all(not (root / "jobs" / identifier).exists() for identifier in ids)
    else:
        assert all(store.get_job(identifier) is not None for identifier in ids)
        assert tree(root / "jobs") == before
    assert store.get_job(source["id"]) == source
    assert not list((root / "jobs" / ".study-deletions").iterdir())


def test_routes_require_admin_session_and_csrf(client):
    _, members, _ = group(client, 1)
    app = client.app
    app.state.auth.create_account("User", "user@example.test", "fixture-only-password-123", "USER")
    path = f"/api/training/jobs/{members[0]['id']}"
    with RawClient(app) as other:
        assert other.get(path + "/study-deletion-preview").status_code == 401
        login = other.post(
            "/api/auth/login",
            json={"identity": "User", "password": "fixture-only-password-123"},
            headers={"Origin": "http://testserver"},
        )
        other.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
        )
        assert other.get(path + "/study-deletion-preview").status_code == 403
        assert (
            other.delete(path + "/study", params={"confirmation_token": "0" * 64}).status_code
            == 403
        )
    report = preview(client, members[0]["id"])
    assert (
        client.delete(
            path + "/study",
            params={"confirmation_token": report["confirmation_token"]},
            headers={"X-CSRF-Token": "invalid"},
        ).status_code
        == 403
    )
