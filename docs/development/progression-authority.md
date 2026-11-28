# Progression, portraits and awards

[Development index](README.md) · [Progression guide](../game/progression.md) · [API](api/README.md)

## Catalog and score

The backend owns a declarative achievement catalog. Ordinary families use nine
thresholds; streak uses four; league families use the shared league tier policy.
Milestone points and family definitions are code-owned, so database values cannot
drift independently. The score sums unique permanent award IDs against the catalog.

Portrait catalog IDs are server-owned and allowlisted by the frontend. Only earned
IDs can be selected; unknown IDs fall back safely. Published portraits use the same
safe projection in leaderboard, online and tournament responses.

## Award transaction

`award_achievements` runs inside the authoritative caller's transaction. Unique keys
and insert-if-absent semantics make retries/concurrent completion idempotent. All
newly reached thresholds are inserted together. Rated completion applies the rating
ledger first; league settlement awards from the permanent qualified peak; local/
vs-AI completion awards the owner in the same transaction; successful completed
server analysis of a unique replay can award the analysis milestone.

Later corrections never delete earned awards. Future milestones are evaluated from
currently valid results. Replay receipts and portrait claims are unique per account/
scope; concurrent claims yield one row. Passive viewing, partial/failed searches and
browser-local analysis do not count.

## Migrations and recovery

Additive schemas introduced replay receipts, catalog reconciliation, league peak
progress and league reconciliation/evidence cursors. Migrations are idempotent and
must not invent awards for evidence that was never recorded before a superseding
correction. Award page reads batch portrait entitlements; the Hall writes only on
claim/selection.

## Artwork provenance

The twelve large gallery portraits are identity-guided redraws produced from the
original 320px references, with a manifest recording native/output dimensions, byte
size and WebP quality. They are not recovered originals or upscaled derivatives.
Original 320px assets remain byte-for-byte unchanged. See
[PORTRAIT artwork provenance](../releases/0.41.0.md) for the release context and
`docs/portrait-gallery-artwork.json` for the source manifest.
