# API and extension boundaries

[Development index](README.md) · [Architecture](architecture.md) · [Security](../administration/security.md) · [API permission inventory](../API_PERMISSIONS.json)

The running server exposes OpenAPI at `/openapi.json` and interactive reference at
`/docs`. Game clients use flattened zero-based `source`/`target` indexes and expected
`version` values; [Analysis](../game/replay-and-analysis.md) has its separate revision lifecycle.
Web model/job routes resolve owned IDs instead of trusting client filesystem paths.

Keep `engine.py` free of HTTP, storage and PyTorch. Add explicit variant positions
for rules changes, bounded/legal behavior for search changes and real updates,
checkpoint reload and legal play for training changes. Extend `ai_profiles` and
`choose_move` together when adding an opponent. [Architecture](./architecture.md)
maps the current modules; [appearance extension points](../game/personalization.md#adding-another-theme)
cover frontend themes. Preserve deterministic replay and optimistic write guards.

## Permissions

The checked-in [API permission inventory](../API_PERMISSIONS.json) is derived from
the registered routes and exercised by `tests/test_security.py`. Do not edit it by
hand to grant access. See [security](../administration/security.md#coverage-and-explicit-exceptions).
