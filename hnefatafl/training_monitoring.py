"""Small job navigator payloads; full histories remain in the detail endpoint."""

from __future__ import annotations


def compact_progress(progress: dict) -> dict:
    result = {
        key: value
        for key, value in progress.items()
        if key not in {"history", "metrics", "evaluation_selection", "trial_progress"}
    }
    metrics = progress.get("metrics")
    if isinstance(metrics, dict):
        result["metrics"] = {
            key: value for key, value in metrics.items() if key not in {"self_play", "evaluation"}
        }
    trial = progress.get("trial_progress")
    if isinstance(trial, dict):
        result["trial_progress"] = compact_progress(trial)
    return result


def job_summary(job: dict) -> dict:
    result = {key: value for key, value in job.items() if key not in {"progress", "result", "logs"}}
    if isinstance(job.get("progress"), dict):
        result["progress"] = compact_progress(job["progress"])
    saved = job.get("result")
    if isinstance(saved, dict):
        # Older completed jobs can retain their counters only in result.json.
        # Preserve the result-then-progress precedence used by the navigator.
        progress = result.setdefault("progress", {})
        for key in ("self_play_games", "games_generated"):
            if key not in progress and key in saved:
                progress[key] = saved[key]
    registered = saved.get("model") if isinstance(saved, dict) else None
    if isinstance(registered, dict) and registered.get("id"):
        result["model_id"] = registered["id"]
    result["summary"] = True
    return result
