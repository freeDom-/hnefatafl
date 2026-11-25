"""Build a competitive-evidence manifest from independently verified provenance.

Reuses ``competitive_validation.model_provenance`` for checkpoint/export
fingerprinting and checkpoint-metadata canonicalization. Owner attestation text is
mandatory for competitive classification and the attestation timestamp is captured
at execution time; automated tests should only generate structural manifests.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import competitive_validation as cv  # noqa: E402

EVIDENCE_NAMES = {
    "level_a_native": "native-a.json",
    "level_a_browser": "browser-a.json",
    "level_b_native": "native-b.json",
    "level_b_browser": "browser-b.json",
    "direct_fixed": "direct-relay-fixed.json",
    "direct_fixed_time": "direct-relay-fixed-time.json",
    "native_benchmark": "native-bench.json",
    "browser_benchmark": "browser-bench.json",
}


def build_manifest(
    checkpoint: Path,
    export_dir: Path,
    evidence_dir: Path,
    *,
    validation_level: str,
    policy: str,
    attestation_statement: str | None = None,
    attestation_source: str = "owner",
) -> dict:
    provenance = cv.model_provenance(export_dir, checkpoint)
    if validation_level == "competitive-trained":
        if policy != "competitive":
            raise ValueError("competitive-trained manifests require the competitive policy")
        if provenance.get("structural_smoke_checkpoint"):
            raise ValueError("known structural smoke checkpoints cannot be competitive")
        if not attestation_statement or not attestation_statement.strip():
            raise ValueError("an explicit owner attestation statement is required")
        if attestation_source != "owner":
            raise ValueError("only an owner attestation is accepted")
        training = {
            "classification": "competitive-trained",
            "checkpoint_metadata_sha256": provenance["checkpoint_metadata_sha256"],
            "attestation": {
                "source": "owner",
                "statement": attestation_statement.strip(),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    else:
        if policy != "structural":
            raise ValueError(f"{validation_level} manifests require the structural policy")
        training = None
    provenance_entry = {
        "training_status": validation_level,
        "checkpoint_sha256": provenance["checkpoint_sha256"],
        "onnx_sha256": provenance["onnx_sha256"],
        "source_weights_sha256": provenance["source_weights_sha256"],
        "ruleset": provenance["ruleset"],
        "architecture": provenance["architecture"],
        "encoder_version": provenance["encoder_version"],
    }
    if training is not None:
        provenance_entry["training"] = training
    required = ["level_a_native", "level_a_browser"]
    if validation_level != "structural-smoke":
        required.extend(
            [
                "level_b_native",
                "level_b_browser",
                "direct_fixed",
                "direct_fixed_time",
                "native_benchmark",
                "browser_benchmark",
            ]
        )
    evidence = {}
    for key in required:
        path = evidence_dir / EVIDENCE_NAMES[key]
        if not path.is_file():
            raise ValueError(f"required evidence {path.name} is missing")
    manifest_evidence = {
        "level_a": {
            "native": EVIDENCE_NAMES["level_a_native"],
            "browser": EVIDENCE_NAMES["level_a_browser"],
        }
    }
    if validation_level != "structural-smoke":
        manifest_evidence.update(
            {
                "level_b": {
                    "native": EVIDENCE_NAMES["level_b_native"],
                    "browser": EVIDENCE_NAMES["level_b_browser"],
                },
                "direct_relay": {
                    "fixed": EVIDENCE_NAMES["direct_fixed"],
                    "fixed_time": EVIDENCE_NAMES["direct_fixed_time"],
                },
                "benchmarks": [
                    EVIDENCE_NAMES["native_benchmark"],
                    EVIDENCE_NAMES["browser_benchmark"],
                ],
            }
        )
    return {
        "kind": "browser-competitive-evidence",
        "schema": 2,
        "validation_level": validation_level,
        "acceptance_policy": policy,
        "provenance": provenance_entry,
        "evidence": manifest_evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--validation-level",
        choices=("structural-smoke", "representative-architecture", "competitive-trained"),
        required=True,
    )
    parser.add_argument("--policy", choices=("structural", "competitive"), required=True)
    parser.add_argument("--attestation-statement")
    parser.add_argument("--attestation-source", default="owner")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_manifest(
            args.checkpoint,
            args.export_dir,
            args.evidence_dir,
            validation_level=args.validation_level,
            policy=args.policy,
            attestation_statement=args.attestation_statement,
            attestation_source=args.attestation_source,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2) + "\n")
        validated = cv._load_manifest(args.output)
    except (ValueError, OSError, TypeError, SystemExit) as error:
        message = str(error)
        for private in (args.checkpoint, args.export_dir):
            message = message.replace(str(private), "<private-path>")
        print(json.dumps({"ok": False, "failures": [message]}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "level": validated["level"],
                "policy": validated["policy"],
                "evidence": {key: len(value) for key, value in validated["paths"].items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
