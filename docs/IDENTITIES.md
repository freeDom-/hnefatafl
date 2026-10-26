# Persistent identity runes

Each human player and model has a persistent presentation rune. API player/model
lists, game participants, move advisors, recorded analyses and statistics expose
the same `rune` for that identity. Names can repeat; runes distinguish separate
player UUIDs and model IDs. A model keeps its rune across different search budgets
and uses on either side. Ordinary non-model AI agents use their normalized search
configuration, excluding game seed, so equivalent agents have the same rune.
An explicit nested `search` object is part of that normalized configuration:
a record that names the default optimizations explicitly is intentionally a
different identity from the same algorithm without the object. The two behave
identically, but keeping them separate means a historical record is never
silently merged with a newer explicit-default record, and their runes and
statistics stay stable. New explicit configurations are preserved rather than
collapsed into the implicit shape.

Tournament participants display their underlying model/agent rune consistently
with games and the model collection. Reusing the same model also reuses its rune;
opponent numbers and search parameters distinguish seats with different budgets.
Earlier records can retain reserved seat runes in storage, but the response/view
uses the underlying agent identity. Those earlier assignments are not recycled.
Renaming a model changes its catalog label without changing its rune.

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
