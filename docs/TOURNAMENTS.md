# AI tournaments

Use **Tournaments** in the main navigation to set up a balanced comparison and
return to its saved results later. Compare two, three or four opponents: each
opponent can use a different algorithm, budget or compatible trained model.

![Saved four-player tournament standings and match results](screenshots/four-player-tournament.png)

The [setup dialog](screenshots/tournament-setup.png) and
[mobile dark view](screenshots/tournaments-mobile.png) use the same saved collection.

## Set up a comparison

1. Choose **New tournament** and give the run a recognizable name.
2. Select one or more battlefields, listed in board-size order.
3. Select the opponents' models or profiles and search parameters. Use **Add
   opponent** to include a third or fourth participant. A neural
   model must match every selected battlefield; use separate tournaments to compare
   models trained for different rulesets.
4. Choose an even number of games per pairing on each battlefield, a move limit and a seed.
5. Start the tournament. The saved view shows progress while the worker plays.

Participants use their model's name, or the AI profile name for ordinary agents.
There is no second participant alias. Model summaries show the network and key
training settings next to each selected model, with its persistent rune and ID.
Saved tournaments retain a model metadata snapshot, so those details remain
readable after deleting training data or removing the model from the catalog.
Later catalog renames apply to new tournaments; existing reports keep their
recorded model names. Older alias-based records display their recorded model or
profile name where available, with the original alias as a legacy fallback.

Every unordered pair of opponents plays a balanced series, swapping
attacker/defender sides after every game. Two players have one pairing, three
have three, and four have six. Total scheduled games are games per pairing
multiplied by pairings and selected battlefields. For example, four opponents
playing two games per pairing on one battlefield schedule 12 games; each
opponent participates in six. Standings count only the games that opponent plays.
The move limit counts individual moves (plies), not pairs of turns.
The shared tournament seed determines the per-game/per-move randomness; it
supersedes individual profile seeds. Tight wall-clock budgets can still produce
different search completion on different hardware. A larger balanced sample is
needed to assess playing strength.

## Read the results

Standings show each opponent's wins, losses, draws, unfinished games and score.
Wins earn one point, actual rule draws half a point for each opponent, and
unfinished games no points. Reaching the experimental move limit is unfinished,
even when one side appears to have an advantage. A stopped tournament can have
fewer finished games than scheduled; an interrupted in-flight game is discarded.

Inspect the saved opponent parameters, results by battlefield, game lengths and
decision timings. Each retained match includes its side assignments, recorded
moves and outcome. **Replay game** opens a read-only board with move navigation.
Replay uses the rules engine, so it still works after the original model is deleted.
The selected appearance settings also apply to tournament boards.
Each saved match identifies its attacker and defender explicitly. Earlier
two-player tournament records remain viewable and replayable without migration.

## Keep, stop and delete

Tournaments are stored on the server in the application's SQLite database, not
only in browser storage. Reloading the page or reopening the application retains
the collection. One tournament can run at a time; its worker runs separately so
regular gameplay remains available. Training has its own worker lifecycle.

**Stop tournament** retains the completed matches and marks the run cancelled.
Shutdown stops workers started by that server; after an unexpected interruption,
the saved view reports that the worker stopped and keeps completed matches.
Runs do not automatically resume. Start a new comparison to schedule more games.

Delete an inactive tournament using its delete button. The confirmation offers
**Keep it** or **Delete**. Deletion removes that tournament's configuration,
results, replay records and worker artifacts. It does not remove models,
training checkpoints, player profiles or regular saved games. Stop a running
tournament before deleting it. Models used by a running tournament are protected
from deletion until the worker has stopped.

## API and deployment

The API exposes collection/detail routes under `/api/tournaments`, creation via
`POST`, cancellation via `POST /{id}/cancel`, and deletion via `DELETE /{id}`.
`GET /{id}/games/{index}/replay` validates and reconstructs a zero-indexed match.
Request schemas are available at `/docs`; clients provide model IDs, never paths.

Schema 3 adds a tournament table without rewriting existing game records.
Run a single API worker as documented in [DEPLOYMENT.md](DEPLOYMENT.md). Back up
the entire data directory consistently before updating. Tournament endpoints have
the same trusted-local-administrator scope as the model/training endpoints.

For headless comparisons, the existing CLI remains available:

```sh
.venv/bin/python -m hnefatafl.cli tournament --config configs/tournament.json
```

CLI reports are written to stdout; browser tournaments form the saved collection.
