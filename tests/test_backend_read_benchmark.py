"""Safety and evidence guards for the optional disposable read benchmark."""

from pathlib import Path
import subprocess

import pytest

from scripts.benchmark_backend_reads import (
    fixture_environment,
    mark_fixture_health,
    validate_parameters,
    verified_source,
)


def test_import_environment_is_isolated_and_restored_on_failure(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("HNEFATAFL_DATA_DIR", "/operator-storage-must-not-open")
    monkeypatch.delenv("HNEFATAFL_ENV", raising=False)
    with pytest.raises(RuntimeError):
        with fixture_environment(tmp_path):
            assert os.environ["HNEFATAFL_DATA_DIR"] == str(tmp_path)
            assert os.environ["HNEFATAFL_ENV"] == "development"
            raise RuntimeError("cancelled import")
    assert os.environ["HNEFATAFL_DATA_DIR"] == "/operator-storage-must-not-open"
    assert "HNEFATAFL_ENV" not in os.environ


def test_fixture_identity_uses_existing_health_route_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    mark_fixture_health(app, "fixture-only")
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers["X-Hnefatafl-Benchmark-Fixture"] == "fixture-only"
        assert response.json() == {"status": "ok"}
        unknown = client.get("/__benchmark_identity")
        assert unknown.status_code == 404
        assert "X-Hnefatafl-Benchmark-Fixture" not in unknown.headers


@pytest.mark.parametrize(
    "users,rounds,concurrency,port",
    [
        (1, 3, "1", 8501),
        (17, 3, "1", 8501),
        (8, 0, "1", 8501),
        (8, 11, "1", 8501),
        (8, 3, "0", 8501),
        (8, 3, "9", 8501),
        (8, 3, "1,1", 8501),
        (8, 3, "", 8501),
        (8, 3, "x", 8501),
        (8, 3, "1,2,3,4,5,6", 8501),
        (8, 3, "1", 80),
        (8, 3, "1", 65536),
    ],
)
def test_fixture_bounds(users, rounds, concurrency, port):
    with pytest.raises(ValueError):
        validate_parameters(users, rounds, concurrency, port)


def test_defaults_represent_distinct_account_concurrency():
    assert validate_parameters(8, 3, "1,4,8", 8501) == [1, 4, 8]


def git(directory: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=directory, text=True, stderr=subprocess.DEVNULL
    ).strip()


def test_source_identity_rejects_dirty_nested_and_missing_checkout(tmp_path):
    with pytest.raises(ValueError, match="Git checkout"):
        verified_source(tmp_path)
    git(tmp_path, "init")
    server = tmp_path / "hnefatafl" / "server.py"
    server.parent.mkdir()
    server.write_text("# fixture\n")
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "fixture",
    )
    revision = verified_source(tmp_path)
    assert revision == git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="repository root"):
        verified_source(server.parent)
    server.write_text("# changed\n")
    with pytest.raises(ValueError, match="clean repository"):
        verified_source(tmp_path)
    git(tmp_path, "checkout", "--", "hnefatafl/server.py")
    server.unlink()
    git(tmp_path, "add", "-u")
    git(
        tmp_path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "remove fixture",
    )
    with pytest.raises(ValueError, match="does not contain"):
        verified_source(tmp_path)
