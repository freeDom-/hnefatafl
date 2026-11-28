# Evaluation

Evaluation records preserve technical knowledge that is expensive to reproduce.
They are grouped by topic, not by implementation phase or pull request.

| Topic | Page |
| --- | --- |
| Benchmark protocol, units, comparability and current limits | [Methodology](methodology.md) |
| AI opponents, wrappers, custom agents and search settings | [Agents](agents.md) |
| AI-only round-robin tournaments | [AI tournaments](ai-tournaments.md) |
| Shield maiden heuristic and measured trade-offs | [Shield maiden](shield-maiden.md) |
| Preserved benchmark reports and raw reports | [Benchmarks](benchmarks/README.md) |
| Browser-local analysis research, phases and evidence | [Browser analysis](browser-analysis/README.md) |
| League Arena evidence | [League Arena](league-arena.md) |
| League achievement evidence | [League achievements](league-achievements.md) |

## Evidence rules

- A measurement belongs to the revision, hardware, model, ruleset, parameters and
  sample size stated in its report. It is not current evidence after a code or
  hardware change.
- Fixed-depth speed, fixed-time strength, throughput and playing strength are
  different claims. A faster search is not automatically a stronger player.
- CPU and GPU measurements are not interchangeable. A smoke model is not a
  competitive model.
- Unfinished games are reported separately from rule draws and are not wins.
- Do not invent missing measurements, confidence intervals or statistical
  significance. A result without a comparison is descriptive only.
- Historical measurements remain unchanged. Superseded or non-comparable results
  are labelled, not deleted.

Current release claims and exact test counts live in
[release notes](../releases/README.md). Current operational limits are in
[readiness](../releases/readiness.md).
