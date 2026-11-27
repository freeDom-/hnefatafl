# Agent evaluation and move advice

[Documentation index](README.md) · [AI settings and wrappers](AI.md)

Winning chances are independent agent estimates for the **current side to move**.
They appear together with the best-move hint when a **move advisor** is selected;
without an advisor the analysis section stays closed. Estimates use the agent's
configured search and are saved for replay; background work never locks board
input or delays an AI move request.

## Playing agents

The moving AI's estimate comes from its actual move search. The server saves its
value, best move, source, effective configuration, derived seed and available
search diagnostics before advancing the game. It does not run a preliminary
search to calculate winning chances. The other AI can evaluate independently in
the background, including during a human turn. Two AI opponents can therefore
retain different opinions about the same position without searching twice for
the moving agent.

Shield bearer and Shield maiden are fixed opponent presets with a maximum 1-second search.
Jarl uses depth 5, maximum 2 seconds. Seer uses 5,000
iterations, temperature 0, exploration 1.4 and maximum 2 seconds. Custom opponents
retain editable search budgets. These presets do not rewrite saved
agent configurations or historical reports.

## Move advisor

Every live or saved game has an **Analytics** section below its move history.
Choose None (the default), Custom move advisor, Shield bearer, Shield maiden, Seer, Jarl or a
compatible registered model. The maximum search time defaults to 5 seconds and
is independent of the playing agents' time limits. Custom advisors expose the
same algorithm, depth, iterations, exploration, temperature and model choices as
custom opponents. The advisor UI currently has no neural-wrapper selector; neural
advisors use MCTS by default. API clients can set `search_wrapper` in the advisor
configuration, and playing-agent reports honor the wrapper selected for that agent.
New-game setup no longer contains advisor settings.

Neural MCTS advisors expose **MCTS Iterations**, defaulting to **1024**.
This is a per-position simulation budget, independent of the selected maximum
search time. Positive integers from **1 to 5000** are accepted, including 2048
and 4096. Higher budgets generally allow stronger search but take longer; the
time limit may stop a search before it completes all simulations. Changing the
search time, moving through replay or reloading retains the saved iteration count.
The input keeps a local draft and saves a valid value after 600 ms without typing,
or immediately on blur or Enter. Rapid edits produce one settings revision.
Invalid drafts are never saved. Unmounting the control or changing the game/advisor
discards any draft that has not yet been committed.
Focusing an advisor, algorithm or model selector also discards the pending draft
and cancels its timer before the selector changes. Blurring into another setting
still commits a valid value; Enter commits before a subsequent advisor switch.
Policy-only and neural alpha-beta advisors do not expose this MCTS control.
Playing agents, training, evaluation and tournament budgets keep their own defaults.

Advisor search runs on a bounded background executor, separate from gameplay
requests. Completed alpha-beta depths and MCTS checkpoints update the suggested
move and its winning-chance estimate. Changing advisor settings starts a new
settings revision and discards superseded results from the display. Selecting an
advisor keeps the persisted winning-chance gate enabled; deselecting it closes
the analysis section. Searching necessarily uses internal evaluation scores.

Explicit advisor analysis also works in past replay positions and completed
games. The Hall of Records review sends a read-only historical request that
analyzes the selected recorded cursor without seeking or mutating the
authoritative game; the exact-cursor live contract is unchanged. The result is
stored separately from original playing-agent evidence. A new advisor opinion is
an analysis of the replay, rather than a claim that the original player produced
that opinion during the game.

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
  gameplay version or moves. Set the neural MCTS budget in the existing
  `advisor.config.iterations` field, for example:

  ```json
  {
    "advisor": {
      "profile_id": "neural",
      "config": { "model_id": "registered-model-id", "iterations": 4096 }
    },
    "time_limit": 15
  }
  ```

  Omitted neural MCTS iteration budgets default to 1024. Explicit saved budgets
  remain authoritative, including earlier values of 40. Invalid budgets are
  rejected using the shared AI configuration validation. Updating the budget
  creates a settings revision, cancelling superseded advisor work through the
  existing progress observer. No database or checkpoint migration is required.
- `POST /api/games/{id}/analysis` with `{ "version": ..., "cursor": ...,
  "background": true }` schedules missing work and returns immediately with the
  available position report, `status` (`pending` or `complete`) and
  `advisor_revision`. Poll the same request for progressive updates. The UI polls
  only while work remains pending and ignores obsolete position/settings replies.
  HTTP 503 switches the UI to a capacity-waiting state while retaining available
  partial estimates. Retries back off from 1 to 30 seconds, honor `Retry-After`
  within those bounds, and return to normal polling after a successful response.
  Only one poll is active at a time; navigation, position or settings changes
  cancel the old request and retry timer. Other errors retain manual retry.
- The older synchronous analysis request remains available to existing API
  clients and honors saved advisor settings/revisions, including an
  `advisor_revision` in responses when preferences have been saved. It joins
  the same advisor task as a background request for that game, position and
  revision, or reuses its completed report. Synchronous requests at earlier
  replay cursors remain read-only; request explicit historical background
  analysis to generate a new advisor opinion there. The game UI uses background
  requests and actual-move diagnostics.

Two analysis workers share a maximum of 16 running/queued tasks. Excess work
returns HTTP 503 with `Retry-After: 1`. At most eight synchronous callers can
wait for those shared tasks at once, leaving request workers available for
gameplay. Additional waiters receive the same retryable response; completed
cached advice remains readable. A synchronous wait is limited to 20 seconds;
timeout returns the same retryable status without cancelling shared work.
A disconnected caller likewise does not own the shared search's lifetime.
Superseded queued work keeps its bounded slot until a worker drains it, then
checks its revision/position before evaluating. Shutdown cancels queued tasks
and running workers stop cooperatively through the existing progress observer.
An individual model inference cannot be forcibly interrupted. Once an advisor
report completes, storage rejects replacement by late progress or another
completion for the same position/revision.

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

## Execution policy and local analysis

The server deployment, not the player, chooses where optional analysis runs:
`browser_only`, `browser_preferred`, `server_only` or `disabled`
(`HNEFATAFL_ANALYSIS_MODE`). There is no Local/Server/Auto selector in the UI. In
browser modes the existing analysis controls execute the same advisor/model search
locally through the shared Python engine in a worker; the server's
`analysis-context` read supplies the authoritative position (including repetition
history). `browser_preferred` retains the server endpoint as a bounded fallback for
classified failures only. `browser_only` rejects all server analysis computation,
while `disabled` suppresses optional analysis without affecting replay, gameplay,
AI opponents, training or tournaments. In `browser_preferred`, fallback obtains a
short-lived admission receipt for one canonical position and advisor revision.
The two-minute budget covers creation, submission, observation, result retrieval
and retry delays. A repeated submission requires a structured rejection before
admission and a still-prepared receipt, or a terminal receipt that proves no task
was admitted. Ambiguous submission outcomes are observed through GET requests.

The account-bound `GET /api/analysis/receipts/{receipt_id}` endpoint reports
retirement evidence independently of the game's current version, advisor or
visibility. Matching terminal evidence, a process epoch change or an applicable
sequence watermark can free a browser slot. A missing receipt in the same epoch
above the watermark leaves the slot reserved.

`GET /api/analysis/receipts/{receipt_id}/result` also checks current ownership or
completed online participation, including current administrator access. A report
requires the current game version and advisor revision. A retired operation may
return a complete advisor evaluation, a partial checkpoint, or an unavailable
advisor alongside retained playing-agent evaluations. Progression credit comes
from successful computation or an explicit POST that finds a completed cached
advisor evaluation.

Local results are advisory and render through the existing panel. They do not
write authoritative game state, clocks, ratings, league/tournament progression or
replay-achievement credit. See the
[Phase 5 validation](validation/browser-analysis-phase5.md) and
[deployment guide](DEPLOYMENT.md#browser-local-analysis-distribution-optional).

## Replay achievement evidence

Schema 20 records account-scoped successful analysis of completed replays for
[achievement progress](PROGRESSION.md#award-rules). Credit requires an explicit
analysis POST and a completed stored advisor report; presentation merges are
never used as completion evidence because they omit the raw partial flag.
Background workers retain a bounded set of authorized requesting accounts (at
most the two participants) for deduplicated computation. Successful completion
saves the report, all eligible requester receipts and automatic milestone awards
in one transaction, without follow-up browser polling. Cancellation, failure,
shutdown and supersession discard requester registrations without credit. A
separate requester lock serializes enrollment with completion without blocking
existing task scheduling/cache coordination.
Subsequent explicit POSTs may reuse completed cached analysis. Ordinary GET/view
requests, active games, partial searches and failed reports do not earn credit.
Existing receipts use a read-only fast path after scope/completion validation;
new receipts revalidate inside their insertion transaction. A unique
`(account_id, category, game_id)` receipt prevents repeated analysis of
one replay from increasing progress. This records successful position analysis
within a completed replay, not proof of a whole-game batch analysis. Browser-local
analysis is not eligible. Analysis positions, advisor settings and move guards
retain their existing contracts.

## Statistics across seeds

AI identity includes algorithm, effective search parameters and model ID while
excluding random seed. The Hall of records combines otherwise equal agents
across game seeds. Aggregate configuration omits seed and reports `seed_count`;
individual recorded games retain their actual seeds and full parameters.
Different model IDs remain distinct even if display names match.
