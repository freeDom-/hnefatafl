# Public Watch Hall

[Game index](README.md) · [Online play](online-play.md) · [Interface guide](interface/README.md)

Signed-out visitors can browse eligible online games at `/watch` and open a live
board or completed replay. The public surface is read-only and separate from the
authenticated application: viewing a public game never creates an account or
grants a seat.

## What is visible

- Online games that the operator exposes publicly.
- The current position, players' public names and selected portraits.
- Remaining time for live games.
- A completed game's result and read-only replay.

## What is never visible

- Single-player or local 1v1 records.
- Private history, profiles, email addresses or full birth dates.
- Live analysis, move advice or win probabilities.
- Account settings, CSRF/session data or moderation notes.
- Any mutation controls.

If an operator disables public discovery, the public namespace and Watch Hall return
not found. Disabling public access does not affect authenticated online play.

The public projection uses the same authoritative game state as the Arena but a
separate allowlisted response. It never accepts a client-supplied asset path.
See [security](../administration/security.md) for the operational exposure
boundary.

## Using the Watch Hall

The discovery page lists live and completed public games with ruleset/status
information. Select a game to open its board. Completed games offer **View replay**;
use the move list to step through the saved position. Public pages do not start
background analysis.

Login and registration links are available when the operator enables them, but
public viewing requires neither.
