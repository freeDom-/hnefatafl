"""Measure the completed-trial bookkeeping cost of an Optuna study.

The previous bookkeeping re-read the whole study after every trial, so a study of N
trials paid N full-history reads and touched O(N^2) stored trials. The current
implementation counts from Optuna's own trial callback and reads the history once
when a study is loaded and once when its final result is aggregated.

Real SQLite storage is used, and the per-operation costs below are measured: a full
history read (`study.trials` plus a state scan, which is what the reconciliation
did) and one counter update (what the callback does per trial). The cumulative
figures are *derived* from those measurements rather than timed from two complete
runs of the old and new code, and the row counts are exact.

Run in a fresh process without other benchmarks or tests.
"""

import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import threading
import time

import optuna


def build_study(path: Path, trials: int):
    study = optuna.create_study(
        study_name="bookkeeping",
        storage=f"sqlite:///{path}",
        direction="maximize",
        load_if_exists=True,
    )
    missing = trials - len(study.trials)
    if missing > 0:
        study.optimize(lambda trial: trial.suggest_float("x", 0.0, 1.0), n_trials=missing)
    return study


def measure_read(study, repeats: int) -> tuple[float, int]:
    """One full-history read plus the state scan the reconciliation performed."""
    samples = []
    complete = 0
    for _ in range(repeats):
        started = time.perf_counter()
        complete = sum(
            1 for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE
        )
        samples.append(time.perf_counter() - started)
    return statistics.median(samples), complete


def measure_update(repeats: int, per_sample: int = 2000) -> float:
    """One counter update: the lock plus the increment the callback performs."""
    lock = threading.Lock()
    latest = {"completed_trials": 0}
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(per_sample):
            with lock:
                latest["completed_trials"] += 1
        samples.append((time.perf_counter() - started) / per_sample)
    return statistics.median(samples)


def rows_read(trials: int, historical: int = 0) -> dict:
    """Stored trials touched by each strategy; the counters are exact."""
    previous = historical * trials + trials * (trials + 1) // 2
    return {
        "previous_rows_read": previous,
        "current_rows_read": 2 * (historical + trials),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--historical", type=int, default=1000)
    parser.add_argument("--resumed", type=int, default=10)
    args = parser.parse_args()
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    report: dict = {
        "environment": {
            "python": platform.python_version(),
            "optuna": optuna.__version__,
            "platform": platform.platform(),
        },
        "note": (
            "Per-operation costs are measured on real SQLite storage; the cumulative "
            "and row-count figures are derived from them."
        ),
        "studies": [],
    }
    update_seconds = measure_update(args.repeats)
    report["counter_update_seconds"] = update_seconds

    with tempfile.TemporaryDirectory(prefix="tafl-bookkeeping-bench-") as temporary:
        root = Path(temporary)
        for size in args.sizes:
            study = build_study(root / f"study-{size}.sqlite3", size)
            read_seconds, complete = measure_read(study, args.repeats)
            report["studies"].append(
                {
                    "trials": size,
                    "completed_trials": complete,
                    "full_history_read_seconds": read_seconds,
                    # An upper bound: every read would cost at most this study's final read.
            "previous_bookkeeping_seconds_upper_bound": read_seconds * size,
                    "current_bookkeeping_seconds": read_seconds * 2 + update_seconds * size,
                    **rows_read(size),
                }
            )

        # A resumed study: the history is already large when the new trials run.
        resumed = build_study(root / f"resumed-{args.historical}.sqlite3", args.historical)
        read_seconds, complete = measure_read(resumed, args.repeats)
        report["resumed_study"] = {
            "historical_trials": args.historical,
            "new_trials": args.resumed,
            "completed_trials": complete,
            "full_history_read_seconds": read_seconds,
            "previous_bookkeeping_seconds_upper_bound": read_seconds * args.resumed,
            "current_bookkeeping_seconds": read_seconds * 2 + update_seconds * args.resumed,
            **rows_read(args.resumed, historical=args.historical),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
