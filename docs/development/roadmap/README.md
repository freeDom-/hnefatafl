# Roadmaps

An implementation roadmap is the long-lived plan for a feature that is still being
developed. It keeps the original objective, all phases (completed, current and
future), dependencies, decisions, acceptance criteria, validation evidence and
outstanding findings. Completed phases stay documented until the **whole feature**
is fully developed and accepted.

- [Browser-local analysis roadmap](browser-analysis.md): complete phase history,
  acceptance criteria, evidence and external gates for the opt-in local analysis
  runtime.
- [Arena and platform roadmap](arena.md): remaining program-level acceptance work.
  Completed Arena features are recorded in release notes and the historical
  completeness snapshot.

## Lifecycle

| Stage | Action |
| --- | --- |
| Feature planned, partial, under development or waiting for acceptance | Keep and update the complete roadmap; mark completed phases instead of deleting them |
| Standalone task inside an active roadmap completes | Mark it completed; remove it only when it is redundant with canonical documentation |
| Whole feature fully developed and accepted | Move durable decisions/measurements to canonical docs, then remove the roadmap |
| Feature blocked by external/hardware acceptance | Keep the roadmap; a merged phase or green PR is not completion |

Do not accumulate standalone phase-completion reports. Update the roadmap and the
canonical guide instead. See
[documentation guidelines](../documentation-guidelines.md#implementation-roadmaps).
