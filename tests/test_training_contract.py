"""The workshop and the training API share one configuration vocabulary.

``frontend/src/trainingContract.json`` is asserted from both sides: the frontend
test proves every payload it can build uses only those fields, and this test
proves the API accepts exactly them. A field added to one side alone fails here
with a named field instead of an opaque rejection at runtime.

The rejection tests also pin the exact, actionable message: the user must be able
to see which field was refused, including a nested tuning configuration and the
server-owned output paths that only the CLI may set.
"""

import json
from pathlib import Path

import pytest
from auth_support import AuthenticatedClient

from hnefatafl.remote_training import TRAINING_FIELDS as REMOTE_TRAINING_FIELDS
from hnefatafl.server import TUNE_FIELDS, TRAINING_FIELDS, create_app
from hnefatafl.training import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "frontend" / "src" / "trainingContract.json").read_text())


@pytest.fixture
def client(tmp_path):
    with AuthenticatedClient(create_app(tmp_path / "server")) as session:
        yield session


def launch(client, payload: dict):
    return client.post("/api/training/jobs", json=payload)


def contract_training(**changes) -> dict:
    """Every field the shared contract names, with the backend's own defaults."""
    payload = {key: DEFAULTS[key] for key in CONTRACT["training_fields"]}
    payload.update(changes)
    return payload


def tune_payload(**changes) -> dict:
    payload = {
        "kind": "tune",
        "trials": 2,
        "study_name": "brandubh-smoke",
        "seed": 0,
        "search_space": {"learning_rate": {"type": "float", "low": 0.0001, "high": 0.01}},
        "training": contract_training(preset="smoke", ruleset="brandubh"),
    }
    payload.update(changes)
    return payload


# --- shared contract ---------------------------------------------------------


def test_the_shared_contract_matches_every_backend_field_set():
    assert set(CONTRACT["training_fields"]) == TRAINING_FIELDS - {"resume"}
    # `resume` is resolved to an owned checkpoint path and is not a form input.
    assert "resume" not in CONTRACT["training_fields"]
    assert set(CONTRACT["tune_fields"]) == TUNE_FIELDS
    assert set(CONTRACT["execution_fields"]) == {"execution", "worker_id"}
    # Remote workers validate the same inputs before accepting a claimed job.
    assert REMOTE_TRAINING_FIELDS == TRAINING_FIELDS
    for key in CONTRACT["resume_immutable_fields"]:
        assert key in TRAINING_FIELDS


def test_the_contract_covers_the_full_validator_defaults():
    assert set(CONTRACT["training_fields"]) <= set(DEFAULTS)
    assert TRAINING_FIELDS <= set(DEFAULTS) | {"resume"}


# --- accepted configurations -------------------------------------------------


def test_every_contract_field_is_accepted_for_remote_training(client):
    response = launch(client, {**contract_training(), "kind": "train", "execution": "remote"})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "queued"


def test_a_standard_default_configuration_launches_successfully(client):
    response = launch(
        client,
        {"execution": "remote", "preset": "recommended", "ruleset": "tablut", "device": "auto"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["preset"] == "recommended"


def test_every_ruleset_and_preset_pair_is_accepted(client):
    for ruleset in ("brandubh", "tablut", "copenhagen"):
        for preset in ("recommended", "smoke", "beginner", "balanced"):
            response = launch(
                client,
                {"execution": "remote", "ruleset": ruleset, "preset": preset, "device": "cpu"},
            )
            assert response.status_code == 201, f"{ruleset}/{preset}: {response.text}"


def test_a_tuning_payload_validates_its_nested_configuration(client):
    # Reaching the study-control check proves the nested configuration passed
    # training validation; no optimization process is started for a bad budget.
    response = launch(client, tune_payload(trials=0))
    assert response.status_code == 400
    assert "trials" in response.json()["detail"]


# --- rejected fields are named ----------------------------------------------


def test_unsupported_top_level_field_is_named(client):
    response = launch(client, {"preset": "smoke", "device": "cpu", "bogus": 1})
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Unsupported training configuration field: bogus",
        "code": "unsupported_training_field",
        "fields": ["bogus"],
    }


def test_multiple_unsupported_fields_are_named_in_deterministic_order(client):
    response = launch(client, {"preset": "smoke", "zebra": 1, "alpha": 2})
    assert response.status_code == 400
    assert response.json()["detail"] == ("Unsupported training configuration fields: alpha, zebra")
    assert response.json()["fields"] == ["alpha", "zebra"]


def test_unsupported_nested_tuning_field_is_named_with_its_path(client):
    payload = tune_payload()
    payload["training"] = {**payload["training"], "bogus": 1, "other": 2}
    response = launch(client, payload)
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unsupported training configuration fields: training.bogus, training.other"
    )
    assert response.json()["fields"] == ["training.bogus", "training.other"]


def test_server_owned_output_paths_are_refused_with_an_explanation(client):
    response = launch(client, contract_training(output_dir="data/elsewhere"))
    assert response.status_code == 400
    body = response.json()
    assert body["fields"] == ["output_dir"]
    assert body["code"] == "unsupported_training_field"
    assert body["detail"].startswith("Unsupported training configuration field: output_dir.")
    assert "assigned by the server" in body["detail"]
    assert "run the training CLI with this config" in body["detail"]


def test_a_shipped_cli_configuration_names_its_server_owned_fields(client):
    # configs/train-beginner.json is a shipped, previously working standard
    # configuration. The API owns the persistent output paths, so it must say
    # exactly which fields to remove instead of refusing the whole request.
    shipped = json.loads((ROOT / "configs" / "train-beginner.json").read_text())
    response = launch(client, shipped)
    assert response.status_code == 400
    assert response.json()["fields"] == ["output_dir", "registry_dir"]
    assert "output_dir" in response.json()["detail"]
    assert "registry_dir" in response.json()["detail"]


def test_resume_names_the_fields_that_cannot_change(client):
    # The field check runs before the job lookup, so no checkpoint is needed.
    response = client.post(
        "/api/training/jobs/" + "0" * 32 + "/resume",
        json={"iterations": 5, "ruleset": "tablut", "channels": 8},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Resume cannot change channels, ruleset; checkpoint identity is immutable."
    )
    assert response.json()["fields"] == ["channels", "ruleset"]

    editable_only = client.post(
        "/api/training/jobs/" + "0" * 32 + "/resume",
        json={"iterations": 5, "batch_size": 16},
    )
    assert editable_only.status_code == 404


def test_a_tuning_trial_checkpoint_token_stays_inside_its_job(client):
    # A `trial-N/last.pt` token resumes one study trial's full checkpoint, but
    # only inside the owning job directory; escaping paths are refused before a
    # process is spawned.
    job_id = "0" * 32
    for token in (
        f"{job_id}:trial-0/last.pt",
        f"{job_id}:../../etc/passwd/last.pt",
        f"{job_id}:trial-0/not-a-checkpoint.pt",
    ):
        response = launch(
            client,
            tune_payload(training={**contract_training(preset="smoke"), "resume": token}),
        )
        assert response.status_code == 400, response.text
    assert client.get("/api/training/jobs").json() == []


def smoke_config(tmp_path, **changes) -> dict:
    from hnefatafl.training import normalize_config

    return normalize_config(
        {
            "preset": "smoke",
            "ruleset": "brandubh",
            "device": "cpu",
            "seed": 3,
            "cpu_threads": 1,
            "games": 1,
            "iterations": 1,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            "output_dir": str(tmp_path / "training"),
            "registry_dir": str(tmp_path / "models"),
            **changes,
        }
    )


def test_every_published_training_phase_is_declared(tmp_path, monkeypatch):
    """The declared vocabulary covers the phases the training code publishes.

    A phase added to the trainer without a declaration would have no dashboard
    label and no translation, so it is collected from a real run instead of
    duplicated in a list.
    """
    from hnefatafl.training import train
    from hnefatafl.training_monitoring import TrainingMonitor

    seen: set[str] = set()
    original = TrainingMonitor.phase

    def record(self, name, *, iteration=None):
        seen.add(name)
        return original(self, name, iteration=iteration)

    monkeypatch.setattr(TrainingMonitor, "phase", record)
    config = smoke_config(tmp_path)
    result = train(config)
    assert result["status"] == "completed"
    # A resume adds the checkpoint-loading and state-restoring stages.
    resumed = train(
        smoke_config(
            tmp_path,
            output_dir=str(tmp_path / "resumed"),
            iterations=2,
            resume=result["checkpoint"],
        )
    )
    assert resumed["status"] == "completed"

    declared = set(CONTRACT["training_phases"])
    assert seen, "no training phase was recorded"
    assert seen <= declared, sorted(seen - declared)
    # The terminal phases the trainer can publish are declared too.
    for phase in ("cancelled", "failed", "pruned", "completed"):
        assert phase in declared
    assert "starting" in declared
