"""Import response compatibility and bounded imported model associations."""

import json

import pytest
from auth_support import AuthenticatedClient as TestClient
from legacy_archive_support import historical_export
from test_job_archives import export, job_record, register_model

from hnefatafl.models import ModelRegistry
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as session:
        yield session


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize(
    "query,compact", [("", False), ("?summary=false", False), ("?summary=true", True)]
)
def test_import_response_selection_is_backward_compatible(
    client, tmp_path, version, query, compact
):
    history = [{"iteration": i, "policy_loss": 1 / (i + 1)} for i in range(1001)]
    report = {"iteration": 1001, "history": history}
    job, directory = job_record(client, progress=report, result=report)
    for filename in ("progress.json", "result.json"):
        (directory / filename).write_text(json.dumps(report))
    content = (
        export(client, job)
        if version == 3
        else historical_export(
            job,
            directory,
            ModelRegistry(client.app.state.data_dir / "models"),
            tmp_path / "legacy.zip",
            version,
        )
    )
    response = client.post(
        "/api/training/jobs/import" + query, files={"file": ("job.archive", content)}
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["id"] != job["id"] and imported["status"] == "completed"
    if compact:
        assert imported["summary"] is True
        assert "history" not in imported["progress"] and "result" not in imported
        assert len(response.content) < 20000
    else:
        assert not imported.get("summary")
        assert imported["progress"]["history"] == history
        assert imported["result"]["history"] == history
    detail = client.get(f"/api/training/jobs/{imported['id']}").json()
    assert detail["progress"]["history"] == history


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("report_name", ["progress", "result"])
@pytest.mark.parametrize("deduplicated", [False, True])
def test_imported_summaries_preserve_searchable_best_model_ids(
    client, tmp_path, version, report_name, deduplicated
):
    registry = ModelRegistry(client.app.state.data_dir / "models")
    current = register_model(registry, tmp_path / "current.pt", seed=51)
    best = register_model(registry, tmp_path / "best.pt", seed=52)
    deleted = register_model(registry, tmp_path / "deleted.pt", seed=53)
    registry.delete(deleted["id"])
    retained = [
        {"model_id": model["id"], "iteration": i, "diagnostic": "x" * 10000}
        for i, model in enumerate((best, best, deleted))
    ]
    report = {"iteration": 1001, "best_models": retained, "history": [{"iteration": 1001}]}
    job, directory = job_record(client, model_id=current["id"], **{report_name: report})
    if report_name == "result":
        job.pop("progress", None)
        client.app.state.store.save_job(job)
    (directory / f"{report_name}.json").write_text(json.dumps(report))
    content = (
        export(client, job)
        if version == 3
        else historical_export(job, directory, registry, tmp_path / "legacy.zip", version)
    )
    if not deduplicated:
        registry.delete(current["id"])
        registry.delete(best["id"])
    response = client.post(
        "/api/training/jobs/import?summary=true", files={"file": ("job.archive", content)}
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    by_weights = {entry["weights_sha256"]: entry["id"] for entry in registry.list()}
    # Portable metadata can change file-based IDs on a fresh installation;
    # existing weights instead deduplicate to the current registry identities.
    assert imported["model_id"] == by_weights[current["weights_sha256"]]
    if deduplicated:
        assert imported["model_id"] == current["id"]
    expected = [{"model_id": by_weights[best["weights_sha256"]]}]
    assert imported["progress"]["best_models"] == expected
    navigator = next(
        row
        for row in client.get("/api/training/jobs?summary=true").json()
        if row["id"] == imported["id"]
    )
    assert navigator["progress"]["best_models"] == expected
    assert len(json.dumps(navigator)) < 20000
    full = client.get(f"/api/training/jobs/{imported['id']}").json()
    assert len(full[report_name]["best_models"]) == 3
    assert full[report_name]["best_models"][0]["diagnostic"] == "x" * 10000
    assert full[report_name]["best_models"][-1]["model_id"] is None


def test_summary_model_associations_are_bounded_and_never_copy_model_records():
    from hnefatafl.job_archives import MAX_FILES
    from hnefatafl.training_monitoring import imported_report_summary

    references = [
        {"model_id": f"{i:024x}", "history": ["not navigator data"] * 100}
        for i in range(MAX_FILES + 100)
    ]
    report = imported_report_summary(
        {
            "iteration": 1200,
            "best_models": [None, {}, {"model_id": None}, {"model_id": "x" * 100000}, *references],
            "history": references,
            "metrics": {"policy_loss": 0.25, "history": references},
        }
    )
    assert report["iteration"] == 1200
    assert report["metrics"] == {"policy_loss": 0.25}
    assert report["best_models"] == [{"model_id": f"{i:024x}"} for i in range(MAX_FILES)]
    assert "history" not in report
    assert len(json.dumps(report)) < 128 * 1024
