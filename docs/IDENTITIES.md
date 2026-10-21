# Persistent identity runes

Each human player and model has a persistent presentation rune. API player/model
lists, game participants, move advisors, recorded analyses and statistics expose
the same `rune` for that identity. Names can repeat; runes distinguish separate
player UUIDs and model IDs. A model keeps its rune across different search budgets
and uses on either side. Ordinary non-model AI agents use their normalized search
configuration, excluding game seed, so equivalent agents have the same rune.

A tournament seat has its own unique `rune` even when two participants use the
same model or search configuration. Its `agent_rune` identifies that underlying
model/agent consistently with games and the model collection. Standings, match
logs and replay use the seat rune; model information uses the agent rune.

SQLite schema 5 adds `entity_runes`, an atomic mapping from stable identity keys
to a globally unique ordinal/rune. The first 24 identities use single Elder
Futhark runes. Further identities receive unique combinations in bijective base
24 (`ᚠᚠ`, `ᚠᚢ`, …); there is no 24-entity limit and no hash-based glyph collision.
Assignments are local to the application data directory, survive restarts and
are never recycled when a player, model, game or tournament is deleted.

Existing player tombstones reserve their identities. Historical games,
tournaments and analysis reports receive missing rune fields in API responses,
without rewriting their stored gameplay JSON or original opinion snapshots.
Models are decorated at the API/catalog boundary; checkpoint and model-registry
formats remain unchanged. Importing the same model ID into the same database
recovers its rune; a different installation assigns its own local catalog runes.

Rune assignment is presentation metadata. It never affects rules, model weights,
search identity, seed grouping, move versions or statistics totals. Restoring a
backup must include the complete SQLite database to preserve rune assignments.
