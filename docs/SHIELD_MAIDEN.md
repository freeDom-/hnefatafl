# Shield maiden strategy and benchmark evidence

[AI profiles](AI.md) · [Training evaluation](TRAINING.md) · [Performance](PERFORMANCE.md)

Shield maiden (`shield_maiden`, heuristic version 1) is an Intermediate,
model-free one-ply opponent for all three rulesets. The standalone Shield bearer
(`baseline`) algorithm and its generic static evaluation remain unchanged. Jarl
and Seer now reuse the same Shield maiden child scoring for their model-free
search, so the Jarl results below predate that default. A custom alpha-beta or
MCTS opponent may set `heuristic: "shield_bearer"` to compare against the generic
evaluation. No engine semantics changed.

## Strategy and implementation

Every legal candidate is applied through the authoritative engine. Exact terminal
wins/losses score ±1,000,000; rule draws score zero. A child allowing the next side
to capture the king or legally escape scores approximately ±100,000, beyond every
ordinary positional score. Terminal checks include shieldwalls, encirclement,
exit forts and repetition. Geometric reply detection covers royal captures and
escapes; it does not prove the absence of other Copenhagen terminal replies.

Cached geometry contains rook rays, king neighbors, capture axes, restricted
squares, escape distance and attacker square values. Each decision computes
parent material, square values, repetition counts and royal route dependencies
once. Unaffected candidates reuse the parent's royal routes. No reply move tree
or neural inference is used. Cancellation is checked between candidates; deadlines
retain the strongest examined legal move. Depth diagnostics distinguish a full
one-ply pass or exact win from a deadline-truncated pass.

Ordinary defender-relative weights are internal, versioned constants:

| Feature | Weight / interpretation |
| --- | --- |
| Material | +180 per defender, −100 per attacker |
| Clear corner approach | +300, multiplied by 0.5 for contested entry; two corner exits add 2.5 route units |
| Immediate king escape | +650 per clear ray when the attacker moves next |
| Escape progress | +32 per reduced edge/corner distance, with clear-route features evaluated separately |
| King mobility | +5 per legal destination; +35 in Copenhagen |
| Royal threat pressure | −180 for a geometric capture threat; decisive penalty when executable next |
| Adjacent attackers | −30 each; −16 in Copenhagen (proximity alone does not capture a strong king) |
| Adjacent defenders | +14 each |
| Exposed moved piece | −240 for defender, +140 for attacker, based on a legal closing capture |
| Credible capture opportunity | +20 against attacker, −36 against defender beside the moved piece |
| Attacker position | 5 per inner ring up to two; 6 near corners; Copenhagen edge guards 2–3 cells from corners get 160 instead |
| Previously visited child | 24 against the mover, subordinate to tactics and engine repetition |

Captured material includes the engine's simultaneous Copenhagen shieldwalls.
Capture danger uses the actual closing-move geometry: moving between already
opposed enemies is safe. Restricted thrones can be crossed but cannot be used as
soldier landing squares. Brandubh's throne is not hostile; market Tablut has no
restricted or hostile squares. Copenhagen kings survive shieldwalls and cannot
be sandwiched or captured on the edge.

## Protocol

The checked-in [configuration](../configs/benchmarks/shield-maiden.json) runs
128 Brandubh and 64 games each of Tablut/Copenhagen, with equal roles in every
seed pair. Pairs share a seed and a legal starting position; opening lengths
cycle through 0, 4 and 8 random legal nonterminal plies. Decision seeds advance
by ply. The cap is 300 additional plies and unfinished games earn zero, reported
separately. Confidence intervals resample whole seed pairs (5,000 bootstrap
samples, fixed resampling seed); they describe this opponent/opening suite.

Latency uses identical opening/middle/sparse/endgame and immediate-threat
positions for both algorithms, warms both paths and alternates execution order
for seven repetitions. Exact-win early termination can reduce maiden's evaluated
move count. Whole-game tests compare baseline self-play with maiden self-play;
these change game length as well as decision cost. The neural evaluation test
uses the actual training evaluator with a fixed randomly initialized 4-channel,
zero-block network, four MCTS simulations, one-second deadline and eight balanced
games, repeated three times in alternating order after inference warm-up. It
performs no training and says nothing about trained-model quality.

Development used seeds starting at 100 and 32 games per variant to compare
moderate/aggressive route weights and Copenhagen guards/mobility. Larger route
weights harmed play and were rejected. Preliminary validation at seed 50000
showed 85.5% Brandubh, 81.3% Tablut and 46.9% Copenhagen, with evaluation runtimes
1.14×/1.70×/1.21× baseline. This prompted one final development refinement of
Copenhagen mobility and nearby capture opportunities. Final validation uses
fresh seeds starting at 70000. Both validation rounds remain disclosed.

No selective two-ply lookahead was included. The measured geometric checks
already provide the main weak-king tactical improvement. Copenhagen siege play
and full-evaluation throughput remain explicit limitations rather than reasons
to silently increase search depth.

## Final measured results (2026-10-23)

Host: AMD EPYC 9V74 virtual CPU, three exposed cores, Linux x86-64,
Python 3.12.14; one CPU thread for the neural evaluator. No GPU was tested.
[Raw results](benchmarks/shield-maiden-v1.json) retain configurations, source
SHA-256 digests, corpus digest, per-position timings, seed/opening/side outcomes,
move digests, Jarl records and every repeated neural evaluation.

| Ruleset | Maiden as attacker W/L/D/U | Maiden as defender W/L/D/U | Overall score | Paired 95% interval |
| --- | --- | --- | --- | --- |
| Brandubh | 51 / 10 / 3 / 0 | 55 / 2 / 7 / 0 | 86.7% (128 games) | 81.6–91.4% |
| Tablut | 23 / 7 / 2 / 0 | 32 / 0 / 0 / 0 | 87.5% (64 games) | 80.5–93.8% |
| Copenhagen | 5 / 27 / 0 / 0 | 30 / 2 / 0 / 0 | 54.7% (64 games) | 48.4–62.5% |

W/L/D/U are maiden wins, losses, rule draws and unfinished games. Scores count
rule draws as half. Brandubh and market Tablut show a large improvement on this
suite. Copenhagen does **not** establish an improvement: the interval includes
50%, and attacker play remains weak. This is not evidence of advanced siege play.

| Matched decisions, milliseconds | Shield bearer | Shield maiden |
| --- | --- | --- |
| Median | 4.652 | 4.685 |
| Mean | 9.723 | 9.314 |
| p95 | 27.908 | 27.179 |
| Evaluated moves (308 decisions each) | 17,836 | 15,799 |
| Evaluated positions / second | 5,956 | 5,507 |

Across 44 positions and seven repetitions, mean decision latency was 0.958×
baseline, median 1.007× and p95 0.974×. Per evaluated child the richer evaluator
costs more; exact-win early termination saves work. There is no general claim
that each child is cheaper or that one-ply algorithms have equal latency.

| Ruleset | Bearer / maiden mean ms | Bearer / maiden p95 ms | Bearer / maiden evaluated positions per second |
| --- | --- | --- | --- |
| Brandubh | 1.170 / 0.905 | 1.802 / 1.780 | 24,508 / 22,566 |
| Tablut | 2.963 / 2.430 | 5.077 / 5.177 | 15,445 / 14,279 |
| Copenhagen | 19.749 / 19.336 | 38.370 / 37.523 | 4,288 / 4,249 |

The complete-game tests below use eight games per agent and variant, starting
from the standard opening with matched seeds. Both sides use the named algorithm.
Their game lengths differ substantially, so this is not an isolated evaluator
microbenchmark. All games completed under the same 300-ply cap.

| Ruleset | Bearer / maiden wall seconds | Bearer / maiden average plies | Bearer / maiden games per hour | Maiden / bearer runtime |
| --- | --- | --- | --- | --- |
| Brandubh | 0.083 / 0.220 | 6.88 / 16.00 | 346,466 / 131,086 | 2.64× |
| Tablut | 0.294 / 0.524 | 8.13 / 14.75 | 98,004 / 54,994 | 1.78× |
| Copenhagen | 6.707 / 2.870 | 38.25 / 16.25 | 4,294 / 10,033 | 0.43× |

### Actual training evaluation cost

These are **complete eight-game evaluations**, averaged over three repeated
alternating runs of each opponent, using the fixed untrained candidate described
above. They include both agents' searches, engine transitions and result collection;
model construction and warm-up are excluded. The plies column is the total across
one eight-game evaluation, identical between repetitions of that opponent.

| Ruleset | Bearer / maiden mean seconds | Bearer / maiden total plies | Maiden / bearer runtime |
| --- | --- | --- | --- |
| Brandubh | 0.185 / 0.702 | 65 / 266 | **3.79×** |
| Tablut | 0.577 / 0.527 | 90 / 86 | 0.91× |
| Copenhagen | 6.557 / 5.807 | 286 / 246 | 0.89× |

**The universal no-increase target for training evaluation was not met.**
Brandubh evaluation took 279% longer with this candidate, despite comparable
per-decision latency. Stronger tactical resistance extended the games. Other
candidates, seeds and budgets can change that trade-off; the preliminary seed
suite also had slower Tablut/Copenhagen evaluations. Keep Shield bearer available
for the cheapest established training benchmark. Choosing maiden explicitly opts
into a different strength/runtime trade-off; it never starts a second evaluation.

### Bounded Jarl sanity check

This historical check ran before Jarl defaulted to the Shield maiden heuristic,
so it compared two independently evaluated alpha-beta searches. Re-running it now
would test the Shield maiden evaluator against itself.

Eight paired games per variant used the alpha-beta algorithm at depth 2 with a
50 ms deadline, rather than the fixed Jarl depth-5/two-second preset. Maiden scored
56.3% Brandubh (4W/3L/1D), 87.5% Tablut (7W/1L) and 50% Copenhagen (4W/4L), with no
unfinished games. This tiny, budget-limited check does not establish superiority
to Jarl. Wall-clock cutoffs can change which alpha-beta depth finishes.

## Reproduce

```sh
.venv/bin/python -m hnefatafl.cli benchmark-heuristics \
  --config configs/benchmarks/shield-maiden.json \
  --output work/shield-maiden.json
```

Keep the host idle for timing. Outcomes are reproducible when searches finish
before deadlines; wall times fluctuate. The checked-in artifact's source digests
identify the actual evaluator and benchmark implementation. Replaying a match
uses its opening seed/length, side, search configuration and incremented decision
seeds. Source digests and move digests detect implementation/outcome changes.

## Regression verification

`make check` passed with 611 backend tests and 151 frontend tests; the sole
backend skip requires CUDA. The full real-server Playwright suite passed 44 tests,
with four desktop-covered mobile skips. Validation includes explicit king tactics
on each ruleset, Copenhagen shieldwalls, safe/exposed captures, restricted squares,
seeded legality, cancellation/deadline accounting, cached/fresh route equivalence,
API profiles/statistics/tournaments, local resume state and real remote-worker
maiden evaluation. Ruff, mypy, TypeScript, Prettier, production build and local
Markdown validation passed. No GPU claim follows from these CPU checks.
