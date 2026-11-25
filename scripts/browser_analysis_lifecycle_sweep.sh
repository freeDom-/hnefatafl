#!/usr/bin/env bash
# Bounded browser-analysis lifecycle sweep (Phase 3C-B owner workflow).
#
# Corrects finding E1:
#   * validator exit statuses are handled explicitly (0 accepted, 1 invalid,
#     2 structurally valid but partial);
#   * a valid partial result continues the sweep and is never labelled accepted;
#   * cleanup is ownership-file based (never PID-only) and runs on startup
#     failure, collection failure and interruption;
#   * active-process tracking is cleared after the workload has been reaped;
#   * each case validates its own session-specific finalized report.
#
# This intentionally does not use `set -e`: unexpected execution failures must
# still reach the cleanup trap with their status preserved.
set -uo pipefail

PYTHON=${PYTHON:-.venv/bin/python}
NODE=${NODE:-node}
SPIKE_DIR=${SPIKE_DIR:-spikes/browser_offline}
STAGE_DIR=${STAGE_DIR:?set STAGE_DIR to a staged neural spike directory}
WORK_DIR=${WORK_DIR:-/tmp}
SESSION_PREFIX=${SESSION_PREFIX:-owner}
DURATION=${DURATION:-60}
WARMUP=${WARMUP:-10}
COLLECT_DURATION=${COLLECT_DURATION:-30}
ITERATIONS=${ITERATIONS:-64}
TIME_LIMIT=${TIME_LIMIT:-1}
MODEL_SHA256=${MODEL_SHA256:?set MODEL_SHA256 to the approved model digest}
MODEL_SHA256_ALT=${MODEL_SHA256_ALT:-}
CASES=${CASES:-"cancel-restart cancel-restart|model-reload model-reload|worker-replacement worker-replacement|client-disposal client-disposal|cache-churn cache-churn|batching independent-batching"}

workload_pid=""
ownership_file=""
session=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$ownership_file" ]; then
    "$PYTHON" "$SPIKE_DIR/wait_for_ready.py" --terminate-only \
      --ownership-file "$ownership_file" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

run_case() {
  local mode=$1 profile=$2
  local rc=0 workload_status=0 validator=0
  session="$SESSION_PREFIX-$mode-$(date +%s)-$$"
  local telemetry="$WORK_DIR/$session.jsonl"
  local ready="$WORK_DIR/$session.ready"
  local summary="$WORK_DIR/$session-workload.json"
  local collected="$WORK_DIR/$session-memory-collected.json"
  local final="$WORK_DIR/$session-memory.json"
  ownership_file="$ready.owner.json"

  "$NODE" "$SPIKE_DIR/sustained_workload.mjs" "$STAGE_DIR" \
    --duration "$DURATION" --mode "$mode" --session "$session" --provider wasm \
    --telemetry "$telemetry" --ready "$ready" --overwrite \
    --iterations "$ITERATIONS" --time-limit "$TIME_LIMIT" --cache --output "$summary" &
  workload_pid=$!

  if ! "$PYTHON" "$SPIKE_DIR/wait_for_ready.py" --ready "$ready" \
    --pid "$workload_pid" --ownership-file "$ownership_file" --timeout 180 \
    --session "$session" --model-sha256 "$MODEL_SHA256" --mode "$mode"; then
    echo "lifecycle $mode: readiness failed (driver or ownership verification)" >&2
    return 3
  fi

  if ! "$PYTHON" "$SPIKE_DIR/memory_profile_collect.py" --pid "$workload_pid" \
    --profile "$profile" --warmup-duration "$WARMUP" --duration "$COLLECT_DURATION" \
    --telemetry-file "$telemetry" --telemetry-session-id "$session" \
    --telemetry-model-sha256 "$MODEL_SHA256" --telemetry-workload sustained-analysis \
    --inference-cache --final-summary-timeout 120 --output "$collected"; then
    echo "lifecycle $mode: memory collection failed" >&2
    return 4
  fi

  if wait "$workload_pid"; then workload_status=0; else workload_status=$?; fi
  # Clear active-process tracking only after the workload has been reaped.
  workload_pid=""

  if ! "$PYTHON" "$SPIKE_DIR/finalize_workload_evidence.py" \
    --profile "$collected" --workload-summary "$summary" \
    --ownership-file "$ownership_file" --exit-status "$workload_status" --output "$final"; then
    echo "lifecycle $mode: evidence finalization failed" >&2
    return 5
  fi
  if [ "$workload_status" -ne 0 ]; then
    echo "lifecycle $mode: workload exited with status $workload_status" >&2
    return 2
  fi

  "$PYTHON" "$SPIKE_DIR/memory_validation.py" "$final"
  validator=$?
  case "$validator" in
    0) echo "lifecycle $mode: accepted ($final)"; return 0 ;;
    2) echo "lifecycle $mode: PARTIAL, structurally valid ($final)" >&2; return 2 ;;
    *) echo "lifecycle $mode: INVALID evidence ($final)" >&2; return 1 ;;
  esac
}

overall=0
IFS='|' read -r -a specs <<< "$CASES"
for spec in "${specs[@]}"; do
  read -r mode profile <<< "$spec"
  run_case "$mode" "$profile"
  rc=$?
  case "$rc" in
    0) ;;
    2) overall=2 ;;
    *) exit "$rc" ;;
  esac
done

if [ "$overall" -eq 2 ]; then
  echo "lifecycle sweep completed with partial evidence; not acceptance" >&2
else
  echo "lifecycle sweep completed with accepted evidence"
fi
exit "$overall"
