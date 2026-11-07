"""Identity fields cannot disappear merely because diagnostics use the budget."""

import json

from hnefatafl.training_monitoring import imported_report_summary


def test_imported_summary_reserves_run_trial_and_model_identities():
    diagnostics = {f"field_{i}": "x" * 1900 for i in range(128)}
    report = {
        "iteration": 1200,
        "self_play_games": diagnostics,
        "metrics": diagnostics,
        "trial_progress": {
            "metrics": diagnostics,
            **{f"extra_{i}": i for i in range(130)},
            "history": [{"iteration": 1200}],
            "run": "trial-run",
            "trial": 4,
            "model": {"id": "b" * 24, "name": "Model name not needed for identity"},
        },
        "run": "study-run",
        "trial": 4,
        "model": {"id": "a" * 24, "history": diagnostics},
    }
    summary = imported_report_summary(report)
    assert summary["run"] == "study-run" and summary["trial"] == 4
    assert summary["model"] == {"id": "a" * 24}
    assert summary["trial_progress"] == {"run": "trial-run", "trial": 4, "model": {"id": "b" * 24}}
    assert len(json.dumps(summary).encode()) <= 32 * 1024
    assert "history" not in summary


def test_summary_preserves_explicit_null_identity_and_model_removals():
    report = {
        "run": None,
        "trial": None,
        "model": None,
        "trial_progress": {"run": None, "model": {"id": None}},
    }
    assert imported_report_summary(report) == report
