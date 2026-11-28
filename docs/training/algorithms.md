# Algorithms

This page describes what Hnefatafl actually implements and how the parts interact.
It is not a general reinforcement-learning tutorial. External references are given
for background only.

## Self-play policy/value learning

Training is AlphaZero-style, not a supervised dataset pipeline:

1. The current network plays `games` self-play games for one iteration.
2. Each move is chosen from a Monte Carlo tree search (MCTS) visit distribution.
3. Positions, legal-action masks, MCTS policy targets and final defender-relative
   outcomes are appended to a bounded replay buffer.
4. The network is trained for `epochs` passes over that buffer with policy
   cross-entropy plus value mean-squared error.
5. The model is optionally evaluated, checkpointed and (for the Champion opponent)
   considered for promotion.

Value targets use the defender perspective: defender win `+1`, attacker win `-1`,
rules draw `0`. A plies-limit termination is **unfinished**, not a draw; its final
position is bootstrapped from the value head. Evaluation treats unfinished games
as non-wins and can make promotion inconclusive. Search and training budgets must
always report unfinished games separately from rule draws.

## Neural network

`tafl-policy-value-v1` is a residual convolutional network:

- GroupNorm and SiLU activations; no BatchNorm running statistics.
- Configurable `channels` (4–128) and residual `blocks` (0–8).
- A pairwise source/destination policy head. A move index is
  `source * board_area + target`.
- A bounded value head from the defender perspective.
- Six binary input planes: attackers, defenders, king, side to move, throne and
  restricted corners. Tablut has no restricted corners.

The six-plane encoder (`six-plane-v1`) is shared by native PyTorch inference and
the browser search worker; it is byte-for-byte the same representation. It does not
encode repetition history or ruleset identity beyond board size/corner
conventions. It is an input identity, not a complete search-state identity.

Illegal actions are masked at inference and in the policy loss. Checkpoints store
tensors and primitive values, never pickled model objects. Format version 1 is
validated by a tensor-only loader; see [models](models.md).

## MCTS

The production search is a cooperative PUCT implementation:

- Selection uses PUCT with the network prior and visit counts.
- Dirichlet noise is added at the root during self-play when
  `dirichlet_fraction > 0`; evaluation uses no noise.
- Temperature applies only to self-play move sampling. The effective self-play
  temperature drops after the first 16 plies to a quarter of the configured value,
  with a minimum of 0.1; evaluation is deterministic.
- `mcts_iterations` and `mcts_time_limit` are both limits; whichever is reached
  first stops the search.
- A node-local prediction cache can reuse a network evaluation when the same
  position is re-reached, while preserving PUCT traversal.
- The search yields one dependent neural evaluation at a time so expensive
  inference can be batched by a caller without changing search semantics.
- Cancellation is cooperative and deadline-aware. Diagnostic counters distinguish
  completed depth, truncated searches and fallbacks.

The same MCTS semantics are exported to the optional browser runtime through a
Pyodide search driver; there is no second JavaScript rules or search engine. See
[the browser analysis roadmap](../development/roadmap/browser-analysis.md).

## Symmetry

The board has D4 rotations/reflections. Two independent features use them:

- **Training augmentation** (`training_symmetry_mode: random_d4`) transforms
  samples and their policy targets, with a dedicated resumable RNG stream saved in
  the checkpoint. It changes training data only.
- **Search symmetry** optionally canonicalises positions and move order for
  transposition reuse. Unsafe board-only pruning is avoided by including the
  history/state stabilizer in the key. See
  [alpha-beta benchmarks](../evaluation/benchmarks/search-alpha-beta.md).

Symmetry modes are optional. Do not assume a fixed-depth speedup is a playing
strength gain.

## Alpha-beta search

Model-free Jarl/alpha-beta agents use minimax with alpha-beta pruning:

- Iterative depth with a cooperative deadline and cancellation callbacks.
- Move ordering and optional transposition tables with bounded capacity
  (default/maximum documented in the agent guide).
- Optional symmetry canonicalisation; neural evaluation can refuse unsupported
  symmetry contexts rather than silently changing semantics.
- Heuristic static evaluation: Shield bearer (generic baseline) or Shield maiden
  (rules-aware one-ply scoring). Jarl and Seer reuse Shield maiden for model-free
  search.

Fixed-depth correctness is compared with an independent exhaustive minimax; speed
and strength are separate claims. See [agents](../evaluation/agents.md).

## Execution, batching and caches

| Mechanism | Scope | Interaction |
| --- | --- | --- |
| Sequential self-play | One game at a time | Lowest memory; useful for correctness |
| Batched self-play | Independent games concurrently | Groups neural evaluations; faster on GPUs, not proven faster on every CPU |
| Parallel CPU search workers | Additional CPU processes | Reuses MCTS; capped by phase concurrency; PyTorch CPU only |
| Inference batch size | Max positions per forward pass | Larger batches need enough queued positions; never waits to fill a batch |
| Exact inference cache | Per phase/backend/precision | Keyed by ruleset, board and side to move; deduplicates identical positions; dropped when the model/backend/precision changes |
| Symmetry cache | Search-local | Reuses canonicalised evaluations; optional |

`auto` self-play mode selects batched on CUDA/OpenVINO and sequential otherwise.
Batching and caching are throughput features, not algorithm changes. CPU smoke
measurements showed no general batching speedup; do not infer GPU performance from
CPU numbers. See [performance](performance.md).

## Evaluation and Champion selection

Evaluation plays an even number of two-game, side-swapped pairs against exactly one
opponent:

| Opponent | Role |
| --- | --- |
| Shield bearer (`baseline`) | Fast one-ply heuristic; early-training benchmark |
| Shield maiden (`shield_maiden`) | Rules-aware one-ply heuristic |
| Jarl (`alpha_beta`) | Alpha-beta search with Shield maiden evaluation |
| Champion | The current best model; the only opponent that can promote the candidate |

The candidate score is completion-aware. Each pair shares a seed and swaps sides.
Champion promotion uses a one-sided confidence bound (`champion_confidence`), a
minimum number of complete pairs (`champion_min_pairs`) and an optional required
margin above 50% (`champion_min_improvement`). Unfinished games, missing pairs and
inconclusive evidence do not promote. A promotion records a new best model and can
carry a persistent Champion-aware adaptation state; raw scores from different
Champions are not compared as if they were the same baseline.

Changing the evaluation opponent starts a new comparison series. Evaluation games
never enter the self-play replay buffer.

## Optimizers and precision

- AdamW (default), Adam, momentum SGD and Schedule-Free AdamW.
- Schedule-Free maintains training and averaged evaluation parameters without a
  conventional decay schedule. Self-play, evaluation, Champion snapshots and
  playable exports use the averaged parameters. It cannot be combined with the
  optional online HyperController. See [Schedule-Free CUDA](../development/schedulefree-cuda.md).
- Mixed precision (`mixed_precision`) takes effect on CUDA; CPU and MPS remain
  full precision. Numerical results are not bit-exact.
- Gradients are clipped. Checkpoints retain model/optimizer, replay, Python/PyTorch
  RNG and CUDA/scaler state.

## Hyperparameter optimization

Optuna runs separate full trials; it is not the online controller. Each trial
trains its own model from the same source checkpoint when configured, samples only
tunable training fields, and stores results in the job's own study database. Study
identity, continuation, weighted multi-opponent objectives and held-out verification
are documented in [getting started](tuning.md#tuning-workflow).
Best-configuration export is a configuration artifact, not a promoted model.

## Optional online adaptation

`adaptation: hypercontroller` adjusts the learning rate inside one continuing run
from evaluation scores. It is off by default, requires at least four evaluation
games, and is not Optuna. The controller has a bounded candidate set, warm-up,
dwell timers and a context history; a Champion promotion refreshes only the new
Champion's local baseline. See [online adaptation](online-adaptation.md).

## Remote workers and model compatibility

Remote workers run the same CLI and training loop. The server assigns exact-version
compatible work, owns leases and validates uploaded checkpoints and models. A worker
capability mismatch leaves the job queued rather than silently downgrading an
algorithm or precision mode. See [remote workers](remote-workers.md).

## Planned and experimental work

- Browser-local neural MCTS is implemented as an opt-in, deployment-controlled
  analysis path (Phase 5 transparent integration), not production-accepted.
  Competitive checkpoints, physical WebGPU, sustained memory, offline readiness and
  playing-strength gates remain open.
- Distributed self-play and PostgreSQL migration tooling are not implemented.
- Leagues and player tournaments are separate game features, not training
  algorithms.
