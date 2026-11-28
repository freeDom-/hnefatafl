# Evaluation and Champion selection

[Training index](README.md) · [Interface](interface.md) · [Parameters](parameters.md) · [Benchmarks](../evaluation/benchmarks/README.md)

Evaluation decides whether a candidate is recorded as a best model or promoted
against the current Champion. It is separate from the training loss.

## Evaluation opponents and selection

Choose one opponent in the **Evaluation** section of Start Training or Resume
Training. Resume starts from the latest full learning state; changing the
opponent does not reconstruct a model from inference weights or reset learning.
The Resume dialog preserves recorded preset metadata but offers no preset
selector. New jobs still support presets.

| Opponent | Useful stage | Selection rule |
| --- | --- | --- |
| Shield bearer | Early training; existing inexpensive heuristic | Strictly higher score in the same benchmark configuration |
| Shield maiden | Stronger rules-aware heuristic; low per-move overhead | Strictly higher score in its own versioned benchmark configuration |
| Jarl | Intermediate/advanced; alpha-beta search over the Shield maiden evaluation | Strictly higher score with the same depth/deadline and benchmark protocol |
| Champion | Advanced; saved compatible neural incumbent | Balanced paired head-to-head evidence, described below |

Jarl can be expensive: measure its evaluation duration on your hardware. Evaluation
never automatically adds a second baseline or previous-model match. Independent
benchmarks/tournaments remain available outside training.

Evaluation runs at `evaluation_frequency` iteration multiples and at the final
optimized iteration, independent of checkpoint writes. Skipped iterations have no
evaluation point. The last result remains available with its original iteration,
opponent and settings; it cannot trigger promotion again. A cancelled evaluation
preserves the incumbent and completed optimizer updates, so resumption continues
from those updates rather than evaluating unchanged weights redundantly.

Defaults preserve the existing cadence (every iteration), even game budget
(2 for base/smoke; recommended Brandubh/Tablut/Copenhagen: 12/16/20), neural
simulation budget (16 base, 2 smoke, 64/96/128 recommended) and move limits
(150 base, 12 smoke, 300/500/800 recommended). The neural deadline inherits
`mcts_time_limit` when omitted (10 seconds for new jobs); the UI exposes it
independently. Consider these
less frequent starting experiments, adjusting completion rates and cost:

| Opponent | Evaluate every | Games |
| --- | --- | --- |
| Shield bearer | 5 iterations | 12 |
| Shield maiden | 5 iterations | 12–24 |
| Jarl | 5–10 iterations | 12–24 |
| Champion | 10 iterations | 48–64 |

Example overrides for an existing Brandubh job, in addition to its saved settings:

```json
{
  "iterations": 200,
  "evaluation_opponent": "champion",
  "evaluation_frequency": 10,
  "evaluation_games": 48,
  "evaluation_iterations": 64,
  "evaluation_time_limit": 2.0,
  "evaluation_max_plies": 300,
  "champion_confidence": 0.95,
  "champion_min_pairs": 12,
  "champion_min_improvement": 0.0
}
```

See the [new Jarl job example](../../configs/train-evaluation-jarl.json) and
[Champion resume example](../../configs/train-evaluation-champion.json). The resume
example requires an existing full checkpoint at its configured path.

### Fixed benchmarks and distinct model states

The candidate is the network being optimized. `last.pt` is its full resumable
state. Each fixed benchmark has its own immutable best snapshot; equal scores
retain the earlier snapshot. The champion is a separate accepted incumbent.
`best.pt`/the final registered inference model contain the selected benchmark's
best snapshot, or the accepted/provisional champion in Champion mode.

Benchmark identity includes ruleset/version, opponent algorithm and effective
search settings, candidate budget, move/game limits, scoring and seeded pairing
protocol. Different identities never compete for a single best score. Changing
only evaluation/checkpoint frequency does not change comparison identity.
A first result in a new fixed benchmark establishes that benchmark's record;
it does not promote or replace an existing champion. Returning to a previous
configuration restores its record. Saturation (score at least 0.95) is flagged;
small samples remain noisy, and strict score improvements alone do not establish
statistical superiority over other neural checkpoints. Reported lower bounds
help expose uncertainty; confirm strength in held-out tournaments.

### Champion evidence and side advantage

The first Champion evaluation uses the previous context's saved best snapshot
(or historical baseline best) as provisional incumbent. A fresh Champion-only
job uses its initial weights provisionally. Initialization is not a promotion
and invents no historical matches. Existing champions survive switching away
and back. Candidate optimization continues whether promotion succeeds or fails.

Both models use equal neural budgets, no exploration noise and alternating
attacker/defender seats. Adjacent games share the same seed sequence and initial
position with swapped model assignments; different pairs use distinct seeds.
Fixed benchmarks reuse the seeded suite between candidates; Champion rounds use
fresh iteration-derived paired seeds. Full search completion is reproducible on
the same device; wall-clock cutoffs can change completed search work and results.

Promotion requires all games to finish, at least `champion_min_pairs` complete
pairs, and a one-sided Hoeffding lower bound strictly above
`0.5 + champion_min_improvement`. For pair scores in [0,1], the lower bound is
`mean_pair_score - sqrt(log(1 / (1 - confidence)) / (2 * pairs))`, clipped at zero.
Treating the pair as the observation accounts for paired side assignments rather
than pretending the individual games are independent evidence. At 95% confidence
and 48 games (24 pairs), the uncertainty subtraction is about 0.25: a 55% score
is inconclusive. This conservative per-comparison rule assumes independently
seeded pairs; it is not a multiple-testing or general-strength guarantee.

Wins earn 1, rules draws 0.5, and unfinished games 0 in the displayed
completion-aware score. Truncations are neither losses nor draws and block
champion promotion. Strong defender advantage can produce a 50% aggregate
without evidence of improvement; attacker/defender scores are shown separately.
Ties, losses, insufficient samples, failed and cancelled evaluations retain the
incumbent. Accepted weights are cloned and never share candidate storage.

### Reading workshop evaluation history

See the [training dashboard guide](./interface.md) for creation, job selection,
analysis tabs, responsive charts and model-collection management.

The workshop shows the active settings, latest training iteration, incumbent
iteration, last measured result, side scores, rules draws, truncated games,
duration and best fixed benchmark iterations. Curves are separate for each
benchmark identity and each Champion incumbent. Promotion events are shown in
the accessible graph table. Missing evaluations remain absent; old baseline
history stays readable in a separate historical series. Loss is fit to self-play
targets, not proof of improved play. No pretrained strong model is bundled.
