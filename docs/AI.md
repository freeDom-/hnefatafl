# Computer opponents and reproducible evaluation

[Documentation index](README.md) · [Gameplay](GAMEPLAY.md) · [Analysis](ANALYSIS.md)

Computer players consume immutable `engine.State` values and return a legal
`engine.Move`. They have no database or HTTP dependency. Every algorithm supports
Brandubh, Tablut market rules and Copenhagen. Search loops use a monotonic
wall-clock deadline and retain a legal fallback when the budget expires; an
in-progress expansion or inference is not a hard real-time operation.

## Account game opponents

New account games select four fixed presets, resolved on the server: Shield bearer
(baseline tactical heuristic, 1 s), Shield maiden (royal escape geometry and piece
safety, 1 s), Jarl (alpha-beta, depth 5, 2 s), Seer (MCTS, up to 5000 iterations,
2 s). The dialog describes styles and budgets without a measured strength ranking.
Normal accounts play as their account against AI; administrators can also run two
preset AIs. Custom/neural parameters belong to the separate authorized workflows
below. Existing stored custom/neural games remain readable/playable.

## Available opponents

Named Shield bearer/Shield maiden/Jarl/Seer presets are fixed in New game and Tournament
setup. Choose **Custom opponent** to edit their budgets. Selecting **Trained model**
requires a registered model compatible with the selected battlefield.

| UI profile | Algorithm | Preset settings |
| --- | --- | --- |
| Shield bearer | One-ply tactical heuristic | Maximum 1 second |
| Shield maiden | Rules-aware one-ply tactical heuristic (Intermediate) | Maximum 1 second; heuristic version 1 |
| Jarl | Iterative-deepening alpha-beta | Depth 5, maximum 2 seconds |
| Seer | Monte Carlo tree search | 5,000 simulations, exploration 1.4, rollout depth 8, temperature 0, maximum 2 seconds |
| Trained model | Selected neural wrapper | MCTS by default; profile supplies 40 simulations and maximum 10 seconds; controls remain configurable |

Jarl and Seer reuse the Shield maiden heuristic for their model-free evaluation,
including move ordering and the alpha-beta leaf/rollout horizon. Only the
standalone Shield bearer baseline keeps the generic static evaluation below.

That static heuristic uses defender-relative material, the king's escape distance,
open escape rays and adjacent attackers. It uses edge escape for Tablut and corner
escape for Brandubh/Copenhagen. Engine terminal outcomes always take precedence.
Shield bearer checks all legal moves for immediate wins, captures and king safety
through the resulting static score. This is a useful baseline rather than a
claim of expert strength.

Alpha-beta searches alternating maximizing defender and minimizing attacker
nodes and orders moves by the Shield maiden child score plus, optionally, the
previous iteration's principal variation, the transposition-table move, killer
moves and cutoff history. The production priority is principal variation, then
transposition-table move, then killers, then history-ranked quiet moves, then the
stable static order. History is consulted even when no preferred move exists, so
a quiet move that caused a cutoff still rises; when every history score is zero
the base order is returned untouched. The root now searches each candidate with a
window narrowed by the best value so far, so the widest part of the tree is no
longer a full-window minimax fan-out; the first move to reach the best value wins,
which stays deterministic. Iterative deepening retains the last completed result
when time expires. An optional `model_path` replaces its nonterminal leaf
evaluation with a compatible neural value network.

Each search owns a bounded transposition table for its lifetime. Entries record
searched depth, an exact/lower/upper bound and a preferred move. The ordinary key
is ``(ruleset, board, turn, repetition signature)``: the ordered move history is
replaced by the order-independent occurrence counts of board/turn keys. The
engine's threefold rule and the Shield maiden repetition penalty depend only on
those counts, so the compact key preserves every future outcome while letting
move-order transpositions share one entry. Two histories with different counts
(for example a position seen once versus twice) keep distinct keys, so
repetition-sensitive states never alias. A depth-compatible bound is classified
against the window that was *actually searched* after any table tightening; a
fail-low against a tightened alpha is stored as an upper bound, never as an exact
value. Bounds tighten the search window, cut off when established and supply move
ordering. An interrupted search never publishes a partial exact result. The table
is discarded when the search ends and is never shared across games, rulesets or
evaluators.

### Search optimization configuration

Named presets keep their existing parameters, so Jarl uses the optimized search
with no configuration change. Headless callers (benchmarks and experiments) may
pass an optional internal `search` object to ablate individual features:

| `search` field | Default | Effect |
| --- | --- | --- |
| `transposition` | `true` | Depth-aware exact/lower/upper transposition bounds |
| `move_ordering` | `true` | PV, table, killer and history ordering |
| `symmetry_transposition` | `false` | D4-canonical, repetition-safe table keys |
| `symmetry_pruning` | `false` | Search one representative per full-state move orbit |
| `table_capacity` | `100000` | Maximum resident table entries (1–500000) |
| `legacy` | `false` | Restore the original exact-value-cache reference search |

The capacity ceiling is bounded because a repetition-aware key can grow to several
kilobytes on a long game; the measured per-entry cost and the long-game peak are
recorded in the [control-refinement evidence](benchmarks/alpha-beta-controls.md).

The Shield maiden evaluator is D4-invariant, so the two symmetry features are
valid for heuristic-backed search. Trained value heads are not guaranteed
equivariant even after augmentation; requesting either symmetry feature with a
neural evaluator raises instead of merging unequal predictions. Neural alpha-beta
still uses the ordinary orientation-specific table and move ordering. Symmetry
features are optional rather than defaults because canonicalizing long histories
costs more than it saves on some rulesets; see the
[benchmark evidence](PERFORMANCE.md#phase-4-alpha-beta-symmetry-search).

Symmetry move pruning runs before successors are built: the full-state stabilizer
partitions the legal moves into orbits, one deterministic representative per orbit
is retained, and only those moves are applied and evaluated. Because the stabilizer
is computed from the complete state (board, turn and ordered repetition history),
a visually symmetric board with asymmetric history is never pruned. The engine's
legal-move list is never modified.

Move ordering, canonicalization, the stabilizer and the per-move evaluation loop
all observe the monotonic search deadline. A long-history canonicalization or
stabilizer therefore aborts promptly when the budget expires instead of running
the whole computation to completion; the interrupted iteration keeps the last
completed depth, exactly like any other deadline expiry. History is consulted in
the documented priority even without a preferred move, but the measured node
effect is position-dependent and is not claimed as a strength gain.

Tournament setup exposes the same options for a custom alpha-beta opponent under
**Alpha-beta optimizations**, with **Legacy alpha-beta**, **Optimized default**,
**Full symmetry** and **Custom** presets. Jarl remains a fixed preset without
editable optimization controls. The nested `search` object is accepted by the
game, advisor and tournament APIs and is validated and normalised server-side
before it reaches `choose_move`. An explicit `search` object is part of the
effective configuration identity, so a record that names the defaults explicitly
stays distinct from one without the object; the two behave identically but keep
separate runes and statistics so historical records never change identity. See
[persistent identity runes](IDENTITIES.md).

### Evaluator heuristic

Jarl (alpha-beta) and Seer (MCTS) default to the Shield maiden evaluator. A
custom alpha-beta or MCTS opponent may set a top-level `heuristic` field to
`shield_maiden` (the default) or `shield_bearer` for testing and evaluation. The
standalone `baseline` algorithm is always Shield bearer, the `shield_maiden`
algorithm has no separate evaluator, and neural search uses its model value head.
New-game custom opponents, tournament setup and custom advisors expose the same
choice. The resolved `heuristic` is part of the effective AI configuration, so a
Shield bearer Jarl record stays distinct from a default Shield maiden one.

Alpha-beta diagnostics add `nodes`, `evaluations`, `cutoffs`, `successors`,
`ordered_moves`, `transposition_lookups`, `transposition_successful_lookups`,
`transposition_exact_hits`, `transposition_bound_hits`,
`transposition_depth_compatible`, `transposition_board_history_divergence`,
`transposition_cutoffs`, `transposition_cross_orientation_hits`,
`transposition_peak_entries`, `canonicalization_seconds`, `stabilizer_seconds`,
`pruning_seconds`, `successor_seconds`, `sort_seconds`,
`transposition_seconds` and `symmetry_moves_skipped`. Iterative deepening adds
`iteration_timings` (per completed depth: seconds and nodes), `interrupted_depth`,
`interrupted_seconds`, `discarded_nodes` and `chosen_from_completed_iteration`.
The per-node timing fields are only populated when the benchmark-only `_profile`
hook is set; the integer counters are always collected.
`transposition_hits` remains the exact-plus-bound hit count. `symmetry_hits` keeps
its historical meaning (a probe that found any canonical-table entry);
`transposition_cross_orientation_hits` is the precise count of probes whose stored
orientation differed from the query's canonical orientation, so genuine
cross-orientation reuse is distinguishable from ordinary same-orientation table
hits. `transposition_board_history_divergence` counts distinct `(board, turn)`
keys probed with different repetition signatures, measuring the reuse the old
full-history key forwent. Counts describe visited interior nodes, leaf/terminal
evaluations, alpha-beta cutoffs, built successors, ordered moves, reused results
and symmetry-pruned moves respectively.

MCTS stores defender-perspective values consistently at every node. Selection
converts that value to the active side's perspective, adds a PUCT exploration
bonus, expands legal moves and backs the result up the visited path. Progressive
widening permits deeper exploration with hundreds of legal opening moves. Plain
MCTS uses seeded random rollouts and the Shield maiden evaluator at the rollout
horizon.
Neural search uses a masked policy prior and the model value instead of rollouts.
An immediate one-move win is always taken even with a tiny simulation budget.
Training additionally applies configurable Dirichlet noise at the root.

## Neural search wrappers

The network supplies a legal policy and defender-relative value; the wrapper
chooses how to use them. Select **Search wrapper** for a neural opponent in New
game or Tournament setup. Analysis honors the saved agent wrapper; advisor API
configurations also accept it. The current advisor UI does not expose a wrapper
selector and defaults neural advisors to MCTS.

| UI choice / `search_wrapper` | Move selection | Relevant controls |
| --- | --- | --- |
| MCTS / `mcts` | PUCT uses masked policy priors and backed-up network/terminal values | `iterations`, `exploration`, `temperature`, `time_limit` |
| Alpha-beta / `alpha_beta` | Minimax evaluates nonterminal leaves with the model value head | `depth`, `time_limit`; no policy sampling |
| None (policy only) / `none` | Direct masked network policy; no tree or rollouts | `temperature`: 0 chooses highest probability; positive values sample with the seed |

Missing `search_wrapper` means MCTS for compatibility with older games/models.
Switching wrappers retains hidden form values, but irrelevant settings do not
change the selected algorithm's behavior or split equivalent record identities.
All three modes use the same ruleset/shape checks and legal-action filtering;
changing the wrapper does not retrain the network. Policy-only position analysis
reports the raw current-position value head, unlike searched leaf/root estimates.
Opponent setup hides the time field for policy-only inference: one forward pass is not a
timed tree search that can return a last completed depth.

For the game API, put a neural configuration under a side's `players` entry:

```json
{
  "ruleset": "brandubh",
  "mode": "human-ai",
  "human_side": "defenders",
  "players": {
    "attackers": {
      "config": {
        "algorithm": "neural",
        "model_id": "REPLACE_WITH_COMPATIBLE_MODEL_ID",
        "search_wrapper": "alpha_beta",
        "depth": 3,
        "time_limit": 2
      }
    }
  }
}
```

Submit to `POST /api/games` after replacing the placeholder using `/api/models`.
The server resolves model IDs to owned files. Headless Python/CLI AI configurations
instead use a local `model_path`; do not send that path to the browser API.

## Configuration

```python
from hnefatafl.ai import choose_move
from hnefatafl.engine import new_game

state = new_game("tablut")
move = choose_move(state, {
    "algorithm": "alpha_beta", "depth": 3, "time_limit": 2.0, "seed": 42
})
```

Defaults and limits depend on the interface. Named presets above are not the
raw engine defaults and training has [its own bounds](TRAINING.md#configuration-bounds).

| Search field | Core Python/headless limit | Game/tournament API limit | Game UI control |
| --- | --- | --- | --- |
| `depth` | 1–8 | 1–5 | 1–5 |
| `iterations` | 1–100,000 | 1–5,000 | 1–5,000 |
| `rollout_depth` | 0–128 | 0–50 | Not exposed |
| `time_limit` | >0–300 seconds | 0.01–15 seconds | 0.05–15 seconds for search |
| `exploration` | 0–20 | 0–10 | 0–10 |
| `temperature` | 0–10 | 0–5 | 0–2 |

A direct configuration without `profile_id` uses core defaults: alpha-beta depth 2,
MCTS 80 simulations, rollout depth 8, exploration 1.4, temperature 0 and a 10-second
ceiling; non-MCTS strategies keep their historical 2-second default when no value
is supplied. The fixed Seer preset is ruleset-independent: it caps search at
5,000 simulations with a 2-second ceiling; the per-ruleset training plans use
128/192/256. Profile selection supplies the named preset's parameters before explicit
headless overrides. New browser games fix named preset budgets instead; use Custom
for overrides. Advisors have a separate maximum time setting (default 5, 0.01–15
seconds through API; see [Analysis](ANALYSIS.md)).

Zero temperature chooses the most visited MCTS root action or highest policy-only
probability; positive temperature samples. No algorithm intentionally returns an
illegal or out-of-turn move. Unknown algorithms, invalid budgets, terminal states,
missing or incompatible models raise errors. A time limit bounds the search loop,
not hard real-time request latency: a move expansion or network call already in
progress may overrun its deadline.

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

See [Tournaments](TOURNAMENTS.md) for browser and CLI round robins, balanced side
assignments, budgets, scoring and legal replay. Wins score one; rules draws half;
unfinished move-cap games score zero and remain separate from draws/losses.
CLI reports go to stdout; browser reports form a persisted, separate collection.
Use [model import/export](TRAINING_DATA.md#checkpoints-registry-import-and-export)
for compatible trained opponents.

## Recorded configurations

The Hall of records separates effective AI configurations, including relevant search
budgets, depth, exploration, temperature, evaluation and model identity. Games
with otherwise equal agents share a row across random seeds; each game retains
its own seed. Open an AI performance row's parameter dialog or a recorded game's details to inspect these
settings. Equivalent omitted/explicit defaults group together, and unrelated stale
controls do not split the same opponent. Historical explicit-algorithm configurations
retain the engine defaults they originally used. See [PERSONALIZATION.md](PERSONALIZATION.md).

## Position evaluations and advice

The game interface saves each playing agent's estimate for the current player,
and an optional advisor can highlight its best searched legal move. Recorded
estimates remain available during replay. See [ANALYSIS.md](ANALYSIS.md) for
sources, perspective, draw handling and persistence. These are uncalibrated
agent assessments rather than measured probabilities of future wins.

## Extending the player registry

Implement a pure move-selection function against `State`, dispatch it through
`choose_move`, and add its profile metadata in `ai_profiles`. Add tests for legal
play in every supported ruleset, bounded execution and tactical/terminal
behavior. Keep any model format versioned and compatible with safe checkpoint
loading. Training quality must be measured with balanced tournaments; a passing
software smoke test does not demonstrate stronger play.

## Shield maiden

`shield_maiden` is a separate CPU-only algorithm between Shield bearer and Jarl.
It needs no model or PyTorch. Select the named profile in games, AI versus AI,
tournaments or advisors, or use the algorithm with a custom deadline.
Its effective configuration includes `heuristic_version: 1`; seeds break ties
reproducibly when the same candidates finish within the deadline.
Jarl and Seer reuse the same Shield maiden child scoring for model-free search.
The `baseline` algorithm and existing identities are unchanged.

The evaluator combines legal king capture geometry, independent clear escape
approaches, contested entry squares, mobility, material, exposed pieces, credible
capture opportunities and attacker corner guards. Immediate engine wins dominate
ordinary scores; permitting an immediate royal escape/capture is strongly penalized.
Brandubh and market Tablut use weak kings, Copenhagen uses four-sided capture and
edge immunity; Tablut escapes to any edge, the others to corners. All children use
the engine, including Copenhagen shieldwalls, encirclement, forts and repetition.
Opponent geometry covers royal capture/escape, not every possible fort, encirclement
or shieldwall reply. This remains a shallow heuristic with limited siege planning.

See [strategy, measured strength and timing](SHIELD_MAIDEN.md). No selective reply
search is enabled: ray geometry supplies tactical safety without expanding a
second ply. Evaluation estimates are heuristic scores, not calibrated win rates.
