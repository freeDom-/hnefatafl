# AI Testing and comparison authority

[Development index](README.md) · [AI Testing guide](../evaluation/ai-tournaments.md) · [API](api/README.md)

AI Testing (`/tournaments`) is an administrator/tournament-permission collection of
saved AI comparison series. It is separate from human player tournaments and never
enters player statistics, Elo or leagues.

## Configurations

A round-robin compares two to four independently configured opponents. A fixed-role
comparison pins one attacker and one defender. Each seat names an algorithm/profile
or compatible model and may set search budget, heuristic, wrapper and depth/
simulations within the shared validation rules. Configurations are portable via
export/import; invalid or spoofed fields are rejected.

## Execution and results

Saved tournaments are durable server records. They can run locally or through remote
workers under the existing job/worker protocol. Each game records the effective
configs, ruleset, seed, side assignment, legal replay, outcome and unfinished flag.
Balanced round robins alternate attacker/defender assignments and reuse paired
openings where configured. Unfinished games are not converted to rule draws or wins.

Results include per-opponent and per-battlefield win matrices, standings, role
analytics and export. A completed series remains readable after source model deletion
because game history retains canonical labels. Stop/cancel and delete are separate
actions; delete protects live references.

## API

The `/api/tournaments` group creates/lists/reads/exports/cancels/deletes series and
serves game replay. It requires the `tournaments` permission. Player-tournament
routes live under `/api/player-tournaments` and are a different authority. See
[OpenAPI](api/README.md).
