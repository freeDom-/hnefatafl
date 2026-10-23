# Agent evaluation and move advice

[Documentation index](README.md) · [AI settings and wrappers](AI.md)

Winning chances are independent agent estimates for the **current side to move**.
The **Show winning chances** checkbox hides or reveals them. Estimates use the
agent's configured search and are saved for replay; background work never locks
board input or delays an AI move request.

## Playing agents

The moving AI's estimate comes from its actual move search. The server saves its
value, best move, source, effective configuration, derived seed and available
search diagnostics before advancing the game. It does not run a preliminary
search to calculate winning chances. The other AI can evaluate independently in
the background, including during a human turn. Two AI opponents can therefore
retain different opinions about the same position without searching twice for
the moving agent.

Shield bearer is a fixed opponent preset with a maximum 1-second search.
Jarl uses depth 5, maximum 2 seconds. Seer uses 5,000
iterations, temperature 0, exploration 1.4 and maximum 2 seconds. Custom opponents
retain editable search budgets. These presets do not rewrite saved
agent configurations or historical reports.

## Move advisor

Every live or saved game has an **Analytics** section below its move history.
Choose None (the default), Custom move advisor, Shield bearer, Seer, Jarl or a
compatible registered model. The maximum search time defaults to 5 seconds and
is independent of the playing agents' time limits. Custom advisors expose the
same algorithm, depth, iterations, exploration, temperature and model choices as
custom opponents. The advisor UI currently has no neural-wrapper selector; neural
advisors use MCTS by default. API clients can set `search_wrapper` in the advisor
configuration, and playing-agent reports honor the wrapper selected for that agent.
New-game setup no longer contains advisor settings.

Advisor search runs on a bounded background executor, separate from gameplay
requests. Completed alpha-beta depths and MCTS checkpoints update the suggested
move and, when enabled, its winning-chance estimate. Changing advisor settings
starts a new settings revision and discards superseded results from the display.
With winning chances hidden, the advisor still searches for a move, but does not
publish a probability. Searching necessarily uses internal evaluation scores.

Explicit advisor analysis also works in past replay positions and completed
games. It is stored separately from original playing-agent evidence. A new
advisor opinion is an analysis of the replay, rather than a claim that the
original player produced that opinion during the game.

## Interpreting the numbers

These are estimates of expected score, not calibrated match-winning
probabilities. All engine values use the defender's perspective in `[-1, 1]`.
The display converts the current player's signed value to `(value + 1) / 2`.
Draws count as half a point. Disagreement between agents can help evaluate their
judgment, but does not establish which agent is stronger.

| Agent | Recorded value source |
| --- | --- |
| Tactical heuristic | Heuristic value after its best searched one-ply move |
| Alpha-beta | Minimax value from the deepest fully completed depth; current-position evaluation if depth one cannot finish |
| Monte Carlo tree search | Mean backed-up root value from completed simulations, including heuristic leaf/rollout cutoffs |
| Neural search / model-backed MCTS | Mean backed-up neural/terminal leaf value from completed simulations |
| Alpha-beta with a model | Minimax value from neural leaf evaluations |
| Neural policy only | Current-position network value head; suggested move comes from the masked policy |
| Finished position | Exact rules outcome: win 100%, loss 0%, draw 50% |

Search time is a maximum, not a guarantee that all requested depths or
simulations finish. The display distinguishes configured and completed depth.
Network depth is taken from the validated model architecture and is independent
of search depth. Advisors recommend the best searched move regardless of play
sampling temperature; actual players keep their configured sampling behavior.
Policy-only analysis uses `neural_value`. Older reports also retain their original
value-head source label rather than being retroactively rewritten as search results.

## API and persistence

- `GET /api/games/{id}/analytics` returns `advisor`, `time_limit`,
  `show_win_chances` and an independent settings `revision`.
- `PUT /api/games/{id}/analytics` saves those settings without changing the
  gameplay version or moves.
- `POST /api/games/{id}/analysis` with `{ "version": ..., "cursor": ...,
  "background": true }` schedules missing work and returns immediately with the
  available position report, `status` (`pending` or `complete`) and
  `advisor_revision`. Poll the same request for progressive updates. The UI polls
  only while work remains pending and ignores obsolete position/settings replies.
- The older synchronous analysis request remains available to existing API
  clients. The game UI uses background requests and actual-move diagnostics.

Game responses include saved `analysis` and `analytics`. Reloads, undo, redo and
seeks recover retained reports. Player opinions are immutable per position and
agent; advisor reports are separated by settings revision. Original player
opinions are never invented retroactively for missing historical positions.

SQLite schema 7 adds `game_analytics` and `advisor_analysis` to the existing
schema 6 database. Migration is additive. Back up the data directory before
upgrading; older servers cannot open schema 7. Checkpoint formats are unchanged.

A late computation can finish after a move or seek and still save its opinion
about a retained position. Storage checks the original game seed and retained
move prefix. Restarting, branching away, changing advisor revision or deleting a
game rejects invalid late writes. Analysis cannot recreate deleted records.
Deleting a game removes its preferences and all analysis in the same transaction.

Advisor models use catalog reference protections just like playing models.
Completed histories keep their reports after source-model deletion. Completed
games hide AI playback and restart controls, including when seeking to an earlier
position. Captured kings remain visible in the finished board presentation;
the authoritative engine state and capture rules are unchanged.

## Statistics across seeds

AI identity includes algorithm, effective search parameters and model ID while
excluding random seed. The Hall of records combines otherwise equal agents
across game seeds. Aggregate configuration omits seed and reports `seed_count`;
individual recorded games retain their actual seeds and full parameters.
Different model IDs remain distinct even if display names match.
