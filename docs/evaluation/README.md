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

Use [benchmark methodology](methodology.md): measurements belong to the revision,
hardware, model, ruleset, parameters and sample in their report; fixed-depth speed,
fixed-time strength, throughput and playing strength are separate claims; unfinished
games are not wins; historical/superseded results keep their scope. Do not invent
missing measurements or significance.

Current release claims and exact test counts live in
[release notes](../releases/README.md). Current operational limits are in
[readiness](../development/releasing/readiness.md).
