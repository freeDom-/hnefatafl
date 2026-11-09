"""API and remote transport keep optimizer identity, capabilities and state."""

from pathlib import Path
from uuid import uuid4

import pytest
from auth_support import AuthenticatedClient

from hnefatafl.models import load_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import train
from test_remote_api import claim, finish, upload, worker
from test_training_resume_api import completed


@pytest.fixture
def client(tmp_path):
    with AuthenticatedClient(create_app(tmp_path / "server")) as session:
        yield session


def inputs(**changes):
    return {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "cpu_threads": 1,
        "optimizer": "adamw_schedulefree",
        "schedulefree_warmup_steps": 20,
        "max_game_plies": 4,
        "evaluation_max_plies": 2,
        **changes,
    }


def test_local_creation_resume_review_immutable_settings_and_rate_overrides(client):
    first = completed(client, client.post("/api/training/jobs", json=inputs()))
    route = f"/api/training/jobs/{first['id']}/resume"
    info = client.get(route).json()
    assert info["can_resume"] and info["optimizer"] == "adamw_schedulefree"
    assert info["config"]["schedulefree_warmup_steps"] == 20
    for changed in [{"optimizer": "adamw"}, {"schedulefree_warmup_steps": 21}]:
        response = client.post(route, json={"iterations": 2, **changed})
        assert response.status_code == 400
        assert list(changed)[0] in response.json()["fields"]
    response = client.post(route, json={"iterations": 2, "adaptation": "hypercontroller"})
    assert response.status_code == 400 and "not supported yet" in response.text
    resumed = completed(
        client,
        client.post(route, json={"iterations": 2, "learning_rate": 0.002, "weight_decay": 0.003}),
    )
    _, saved = load_checkpoint(client.app.state.data_dir / "jobs" / resumed["id"] / "last.pt")
    group = saved["optimizer_state"]["param_groups"][0]
    assert group["k"] > 1 and group["warmup_steps"] == 20
    assert group["lr"] == 0.002 and group["weight_decay"] == 0.003
    assert group["train_mode"] is False


@pytest.mark.parametrize("value", [-1, 1000001, 0.5, True, "20"])
def test_api_rejects_invalid_warmup_before_launch(client, value):
    response = client.post(
        "/api/training/jobs", json=inputs(execution="remote", schedulefree_warmup_steps=value)
    )
    assert response.status_code == 400 and "schedulefree_warmup_steps" in response.text


def test_remote_capability_required_even_with_version_override_and_complete_transport(
    client, tmp_path
):
    remote, auth = worker(client)
    override = client.patch(
        f"/api/training/workers/{remote['id']}/version-override", json={"enabled": True}
    )
    assert override.status_code == 200
    bad = client.post("/api/training/jobs", json=inputs(execution="remote", worker_id=remote["id"]))
    assert bad.status_code == 400 and "Schedule-Free AdamW" in bad.text
    queued = client.post("/api/training/jobs", json=inputs(execution="remote")).json()
    assert claim(client, auth, "old") is None
    for value in [1, "true", []]:
        response = client.post(
            "/api/worker/heartbeat",
            headers=auth,
            json={"capabilities": {"schedule_free_adamw": value}},
        )
        assert response.status_code == 400
    client.post(
        "/api/worker/heartbeat",
        headers=auth,
        json={
            "capabilities": {
                "package_version": client.app.version,
                "training_symmetry_modes": ["off", "random_d4"],
                "schedule_free_adamw": True,
            }
        },
    )
    leased = claim(client, auth, "updated")
    assert leased["id"] == queued["id"]
    assert leased["config"]["schedulefree_warmup_steps"] == 20
    result = train(
        {
            **leased["config"],
            "output_dir": str(tmp_path / "remote"),
            "registry_dir": str(tmp_path / "remote-models"),
        }
    )
    for kind, path in [
        ("checkpoint", result["checkpoint"]),
        ("inference", result["inference_checkpoint"]),
    ]:
        response = upload(client, auth, leased, kind, Path(path))
        assert response.status_code == 200, response.text
    assert finish(client, auth, leased).status_code == 200
    response = client.post(
        f"/api/training/jobs/{queued['id']}/resume", json={"iterations": 2, "execution": "remote"}
    )
    assert response.status_code == 201, response.text
    resumed = claim(client, auth, uuid4().hex)
    download = client.get(
        f"/api/worker/jobs/{resumed['id']}/resume",
        headers={**auth, "X-Lease-Token": resumed["lease_token"]},
    )
    assert download.status_code == 200
    received = tmp_path / "received.pt"
    received.write_bytes(download.content)
    _, old = load_checkpoint(result["checkpoint"])
    _, transported = load_checkpoint(received)
    assert transported["optimizer_state"]["param_groups"] == old["optimizer_state"]["param_groups"]
    continuation = train(
        {
            **resumed["config"],
            "resume": str(received),
            "output_dir": str(tmp_path / "resumed"),
            "registry_dir": str(tmp_path / "remote-models"),
        }
    )
    _, final = load_checkpoint(continuation["checkpoint"])
    assert (
        final["optimizer_state"]["param_groups"][0]["k"]
        > old["optimizer_state"]["param_groups"][0]["k"]
    )


def test_legacy_workers_receive_no_new_setting(client):
    _, auth = worker(client)
    result = client.post(
        "/api/training/jobs", json={"preset": "smoke", "execution": "remote", "device": "cpu"}
    )
    assert result.status_code == 201
    leased = claim(client, auth)
    assert leased["config"]["optimizer"] == "adamw"
    assert "schedulefree_warmup_steps" not in leased["config"]
