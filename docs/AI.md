# Computer opponents and reproducible evaluation

Computer players consume immutable `engine.State` values and return a legal
`engine.Move`. They have no database or HTTP dependency. Every algorithm supports
Brandubh, Tablut market rules and Copenhagen. Search results are bounded by a
monotonic wall-clock deadline, with a legal fallback if the deadline expires.

## Available opponents

| UI profile | Algorithm | Main parameters |
| --- | --- | --- |
| Shield bearer | One-ply tactical heuristic | `time_limit`, `seed` |
| Jarl | Iterative-deepening alpha-beta | `depth` (1–8), `time_limit`, `seed` |
| Seer | Monte Carlo tree search | `iterations`, `exploration`, `rollout_depth`, `time_limit`, `seed` |
| Trained model | Neural PUCT search | compatible registered model, `iterations`, `exploration`, `temperature`, `time_limit`, `seed` |

The static heuristic uses defender-relative material, the king's escape distance,
open escape rays and adjacent attackers. It uses edge escape for Tablut and corner
escape for Brandubh/Copenhagen. Engine terminal outcomes always take precedence.
Shield bearer checks all legal moves for immediate wins, captures and king safety
through the resulting static score. This is a useful baseline rather than a
claim of expert strength.

Alpha-beta searches alternating maximizing defender and minimizing attacker
nodes, orders moves by terminal and static value, and caches only completely
searched values with the repetition context included. Iterative deepening retains
the last completed result when time expires. An optional `model_path` replaces
its nonterminal leaf evaluation with a compatible neural value network.

MCTS stores defender-perspective values consistently at every node. Selection
converts that value to the active side's perspective, adds a PUCT exploration
bonus, expands legal moves and backs the result up the visited path. Progressive
widening permits deeper exploration with hundreds of legal opening moves. Plain
MCTS uses seeded random rollouts and the heuristic at the rollout horizon.
Neural search uses a masked policy prior and the model value instead of rollouts.
An immediate one-move win is always taken even with a tiny simulation budget.
Training additionally applies configurable Dirichlet noise at the root.

## Configuration

```python
from hnefatafl.ai import choose_move
from hnefatafl.engine import new_game

state = new_game("tablut")
move = choose_move(state, {
    "algorithm": "alpha_beta", "depth": 3, "time_limit": 2.0, "seed": 42
})
```

MCTS budgets range from 1 to 100,000 iterations, exploration from 0 to 20,
rollout depth from 0 to 128, and wall-clock time from greater than zero to 300
seconds. A zero temperature selects the most visited root action; positive
temperatures sample visit counts. No algorithm returns an illegal or
out-of-turn move. Supplying an unknown algorithm, invalid budget, terminal state,
missing model or incompatible model raises an explanatory error.

For reproducible algorithm comparisons, use fixed seeds and sufficient time that
the requested simulation/depth budget completes. A tight wall-clock budget can
produce different completed search depths on different machines.

Browser games use fresh random base seeds by default. The server stores the seed
and derives each decision's seed as `(base + ply) % 2**32`. Restart gets a new seed.
For an explicit repeatable API game, supply a top-level `seed` in `POST /api/games`;
the per-side search configuration no longer controls a new game's base seed.
Earlier saved games keep their original behavior. Training and tournament runs
retain their explicit reproducibility configuration and per-game derived seeds.

New-game model suggestions rank compatible models by their comparable baseline
evaluation results, counting unfinished games as zero points. This uses saved
measurements, not training loss or a blanket claim of model strength. The workshop
shows the training seed separately from evaluation/game seeds. See
[PERSONALIZATION.md](PERSONALIZATION.md) for the ranking and fallback rules.

## Tournaments

```sh
.venv/bin/python -m hnefatafl.cli tournament --config configs/tournament.json > tournament-results.json
```

The configuration compares two independently configured profiles, alternates
sides, and runs an even number of games for each requested ruleset. The report
contains individual move records, outcome/reason, side assignment, per-player
scores, average game length and average decision time. Wins score one and actual
rules draws score half. A game reaching `max_plies` with no engine outcome is
reported as `winner: null`, `outcome: "truncated"`, receives no score, and appears
in the separate `truncated` count. It is never silently recorded as a draw.
Moves can be replayed using the deterministic engine to validate each result.

Neural checkpoints require matching rulesets. Use the registry import and export
commands documented in [TRAINING.md](TRAINING.md). Selecting a trained opponent
in Human vs AI or AI vs AI uses the same engine and legal-action filtering as the
traditional opponents.

## Recorded configurations

The Hall of records separates effective AI configurations, including relevant search
budgets, depth, exploration, temperature, random seed, evaluation and model identity.
Expand an AI performance row or recorded game's parameter details to inspect these
settings. Equivalent omitted/explicit defaults group together, and unrelated stale
controls do not split the same opponent. Historical explicit-algorithm configurations
retain the engine defaults they originally used. See [PERSONALIZATION.md](PERSONALIZATION.md).

## Extending the player registry

Implement a pure move-selection function against `State`, dispatch it through
`choose_move`, and add its profile metadata in `ai_profiles`. Add tests for legal
play in every supported ruleset, bounded execution and tactical/terminal
behavior. Keep any model format versioned and compatible with safe checkpoint
loading. Training quality must be measured with balanced tournaments; a passing
software smoke test does not demonstrate stronger play.
