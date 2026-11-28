# Agent evaluation reference

[Evaluation index](README.md) · [Algorithms](../training/algorithms.md) · [Search parameters](../training/parameters.md#agent-search-settings) · [AI Testing](ai-tournaments.md) · [API](../development/api/README.md)

Hnefatafl agents consume immutable engine states and return a legal move. They have
no database or HTTP dependency. This page describes the implemented agent families
and how their comparative evidence should be interpreted; it is not a UI setup
guide or API reference.

## Agent families

| Family | Algorithm | Typical role |
| --- | --- | --- |
| Shield bearer | One-ply tactical heuristic with generic static evaluation | Fast baseline; early training evaluation |
| Shield maiden | Rules-aware one-ply heuristic | Stronger cheap opponent; Jarl/Seer child scoring |
| Jarl | Iterative-deepening alpha-beta with Shield maiden evaluation | Search opponent and evaluation benchmark |
| Seer | MCTS with Shield maiden leaf evaluation | Search opponent; fixed simulation/time budget |
| Trained-model wrappers | MCTS, alpha-beta or policy-only around a compatible network | Play, advisor and evaluation |

Named game presets are fixed; administrators can configure a custom opponent.
Search fields and bounds are canonical in
[agent search parameters](../training/parameters.md#agent-search-settings).
Implementation details are in [algorithms](../training/algorithms.md) and
[game authority](../development/game-authority.md).

## Evidence interpretation

Agent strength requires paired side-swapped games and the same model/ruleset/budget/
sample. Fixed-depth speed and fixed-time completed work are not strength. See
[benchmark methodology](methodology.md); unfinished games are not draws.

Shield maiden's implementation, weights, protocol, latency, strength and
limitations are preserved in [Shield maiden](shield-maiden.md). Alpha-beta
correctness/optimization evidence is in the
[alpha-beta report](benchmarks/search-alpha-beta.md); MCTS and parallel-search
measurements are in their respective benchmark reports.

## Model recommendations

Compatible model suggestions use saved comparative evaluation results, counting
unfinished games as zero. They do not use training loss as a strength proxy. A model
without recorded evaluation is labelled as such rather than ranked by implication.
Changing a model label/rune does not change its identity or results. See
[training models](../training/models.md) and [AI Testing](ai-tournaments.md).

## Limits

No agent is claimed expert-strength; results apply to the stated workload. Displayed
winning chances are uncalibrated estimates. Browser feasibility does not establish
competitive browser strength. Reproducibility requires the same seed/model/budget/
mode; parallel/GPU reductions may differ numerically.
