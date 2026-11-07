"""Regenerate small golden ZIPs using complete historical application checkouts.

Only the resulting gzip-wrapped original ZIP bytes and provenance are committed;
models, databases and source checkouts stay in work/. CI reads the goldens offline.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

COMMITS = {
    1: "efbbdbb99f6ba886ddd4dd22f9ff310c4ce80c6b",
    2: "0407d88c08d2f8441f5763d084f209fc9d822656",
}
ROOT = Path(__file__).resolve().parents[1]


def worker(source, data, output, schema):
    sys.path.insert(0, str(source))
    import torch
    from hnefatafl import job_archives
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
    from hnefatafl.training import train, tune

    torch.set_num_threads(1)
    registry = ModelRegistry(data / "models")
    version = "0.35.0" if schema == 1 else "0.36.0"
    outcomes = {}
    for index, scenario in enumerate(("train", "study", "missing_optional", "frozen_study"), 1):
        identifier = f"{schema}{index:031d}"
        directory = data / "jobs" / identifier
        directory.mkdir(parents=True)
        training = {
            "preset": "smoke",
            "ruleset": "brandubh",
            "seed": 17,
            "device": "cpu",
            "cpu_threads": 1,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            "registry_dir": str(registry.root),
        }
        kind = "tune" if "study" in scenario else "train"
        if kind == "tune":
            config = {
                "study_name": f"Historical schema {schema} {scenario}",
                "trials": 1,
                "seed": 17,
                "output_dir": str(directory),
                "training": training,
                "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            }
            if scenario == "frozen_study":
                torch.manual_seed(23)
                opponent = registry.import_model(
                    save_checkpoint(
                        data / f"opponent-{schema}.pt",
                        PolicyValueNet("brandubh", 4, 0),
                        {"name": "Historical frozen reference"},
                    )
                )
                config["opponents"] = [
                    {"opponent": "frozen_model", "weight": 1, "model_id": opponent["id"]}
                ]
            result = tune(config)
        elif scenario == "train":
            config = {**training, "output_dir": str(directory)}
            result = train(config)
        else:
            config = {"preset": "smoke", "ruleset": "brandubh"}
            result = None
        job = {
            "id": identifier,
            "kind": kind,
            "status": "completed" if result else "cancelled",
            "created_at": "2026-01-02T03:04:05+00:00",
            "config": config,
            "progress": result or {},
        }
        if result is not None:
            job["result"] = result
        if result and result.get("model"):
            job["model_id"] = result["model"]["id"]
        path = data / f"{scenario}.zip"
        job_archives.export_job(job, directory, registry, path, version)
        content = path.read_bytes()
        name = f"schema{schema}-{scenario}.zip.gz"
        compressed = gzip.compress(content, compresslevel=9, mtime=0)
        (output / name).write_bytes(compressed)
        outcomes[name] = {
            "schema": schema,
            "app_version": version,
            "commit": COMMITS[schema],
            "writer_sha256": hashlib.sha256(
                (source / "hnefatafl/job_archives.py").read_bytes()
            ).hexdigest(),
            "zip_sha256": hashlib.sha256(content).hexdigest(),
            "zip_bytes": len(content),
            "fixture_bytes": len(compressed),
        }
    (data / "provenance.json").write_text(json.dumps(outcomes, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--schema", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/fixtures/job_archives")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args.source, args.data, args.output, args.schema)
        return
    (ROOT / "work").mkdir(exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix="legacy-fixtures-", dir=ROOT / "work") as tmp:
        for schema, commit in COMMITS.items():
            source = Path(tmp) / f"app-{schema}"
            source.mkdir()
            data = Path(tmp) / f"data-{schema}"
            archive = subprocess.run(
                ["git", "archive", commit, "hnefatafl", "pyproject.toml"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            subprocess.run(["tar", "-x", "-C", str(source)], input=archive.stdout, check=True)
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--worker",
                    "--schema",
                    str(schema),
                    "--source",
                    str(source),
                    "--data",
                    str(data),
                    "--output",
                    str(args.output),
                ],
                check=True,
            )
            results.update(json.loads((data / "provenance.json").read_text()))
    (args.output / "provenance.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
