"""Compare a browser Jarl benchmark report against a native reference report.

This is the command-line front end to ``jarl_compare`` (the canonical comparison
module), so the mandatory correctness rules live in exactly one place. It exits
0 only when every mandatory check passes; fixed-time differences are reported as
informational warnings and never cause a non-zero exit.

Either input may be `-`, in which case that report is read from standard input.
This lets the browser verifier pipe its fresh report through the same comparator.

Usage:
  python compare_jarl_benchmarks.py native.json browser.json --output comparison.json
  node verify_jarl.mjs ... | python compare_jarl_benchmarks.py native.json -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarl_compare  # noqa: E402


def _load(spec: str) -> dict:
    if spec == "-":
        return json.load(sys.stdin)
    return json.loads(Path(spec).read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", help="Native/reference report path, or - for stdin")
    parser.add_argument("actual", help="Browser/candidate report path, or - for stdin")
    parser.add_argument("--output", type=Path, help="Write the full comparison as JSON")
    args = parser.parse_args()
    if args.expected == "-" and args.actual == "-":
        parser.error("only one input may be read from standard input")

    expected = _load(args.expected)
    actual = _load(args.actual)
    result = jarl_compare.compare(expected, actual)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")

    print(
        json.dumps(
            {
                "mode": result["mode"],
                "fixed_work": result["fixed_work"],
                "comparable_cases": result["comparable_cases"],
                "limited_cases": result["limited_cases"],
                "move_agreement": result["move_agreement"],
                "value_agreement": result["value_agreement"],
                "depth_agreement": result["depth_agreement"],
                "equal_work_latency_ratio_median": result["equal_work_latency_ratio_median"],
                "failures": len(result["failures"]),
                "warnings": len(result["warnings"]),
                "ok": result["ok"],
            },
            indent=2,
        )
    )
    for message in result["failures"]:
        print(f"MANDATORY FAILURE: {message}", file=sys.stderr)
    for message in result["warnings"][:20]:
        print(f"note: {message}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
