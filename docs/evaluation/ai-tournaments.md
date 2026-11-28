# AI comparison tournaments

[Evaluation index](README.md) · [AI Testing UI](../game/interface/workshop.md#ai-testing) · [AI Testing authority](../development/ai-testing.md) · [Agents](agents.md)

AI Testing (`/tournaments`) saves reproducible comparisons between computer
opponents. It is an evaluation tool, not a player competition and never affects
player Elo, leagues or statistics.

## Comparison formats

| Format | Use |
| --- | --- |
| Round robin, two to four opponents | Every unordered pair meets; balanced attacker/defender assignments |
| Fixed-role comparison | One attacker and one defender with independent configurations |

Each seat can use a different algorithm, heuristic, compatible model and search
budget. Round robin seats cycle sides; fixed-role seats keep their role. Paired
openings can be randomized and legal, so the same opening is reused with swapped
sides where configured.

## Fairness requirements

- Keep ruleset, model identity, search budget, ply cap and evaluation mode explicit.
- Use balanced sides and paired openings for strength comparisons.
- Record complete and unfinished games separately. An unfinished game is not a
  draw or a win.
- Do not compare different search budgets as if only the algorithm changed.
- A win matrix describes these matches; it is not a universal strength ranking.

## Results

The saved series shows standings, per-opponent scores, battlefield win matrices,
role analytics, average game length and explicit unfinished counts. Games retain
their effective configurations, seed, side assignment, outcome and legal replay.
Results remain readable after source model deletion because canonical labels are
stored with the history.

Export writes the comparison configuration/result. Import validates fields instead
of accepting arbitrary client configuration. Stopping a running series and deleting
its data are separate actions; deletion protects live references.

## Limits

- AI Testing is not training. A model can be improved by training, but a comparison
  result does not prove general playing strength.
- Small samples and short budgets give weak evidence. Report the exact workload.
- CPU/GPU and smoke/competitive-model results are not interchangeable.
- The complete API, persistence and worker semantics are in
  [AI Testing authority](../development/ai-testing.md). UI operation is in
  [AI Testing](../game/interface/workshop.md#ai-testing).
