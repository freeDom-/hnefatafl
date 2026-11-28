# Progression, achievements and portraits

[Game index](README.md) · [Hall of Renown interface](interface/records.md#hall-of-renown) · [Profile](profile.md) · [Leagues](leagues.md)

The **Hall of Renown** (`/portraits`) shows unlocked and locked portraits,
achievement progress and milestone rewards. Progression is permanent: confirmed
awards remain after later result corrections, demotion, season rollover or a
lowered live progress value.

## Portraits

Portraits are earned through achievements or unlocked manually when the progression
rules allow. Only earned catalog IDs can be selected; an unknown or locked ID falls
back to the default portrait. Selection is cosmetic and does not affect gameplay,
ratings or matchmaking.

Portrait cards show the character, unlock state and related achievement. The
detailed leaderboard, online seats and tournament displays use the same safe portrait
projection as the profile. Original 320px assets remain unchanged; gallery displays
use the larger redraws documented under [artwork provenance](#artwork-provenance).

## Achievement families

| Family | Basis |
| --- | --- |
| Rated online wins | Eligible rated wins |
| Rated Brandubh / Tablut / Copenhagen wins | Rated wins per ruleset |
| Rated defender / attacker wins | Actual seat in rated games |
| Human-versus-AI wins | Completed human victories against AI |
| Distinct analyzed replays | Unique completed replays with a successful advisor report |
| Best rated win streak | Longest applied rated win streak |
| Fireside Game | First local 1v1 game |
| League tiers | One five-tier family per league ruleset |

Ordinary families use nine thresholds: 1, 3, 10, 25, 50, 100, 250, 500 and 1000.
The streak family uses 1, 3, 5 and 10. Each threshold has a fixed incremental point
value; the score is the sum of permanently awarded milestones. The catalog is
code-owned, so point values cannot drift independently in the database.

League families use the league tier thresholds (Midgard, Nidavellir, Alfheim,
Asgard, Valhalla) and the first five incremental point values. League results
qualify only after three eligible league games. Reaching a tier in a later season
does not duplicate the award.

## Award rules

- Awards are permanent and idempotent. Retries, duplicate completion reports and
  concurrent claims cannot create duplicate milestones.
- Rated awards follow the authoritative rating ledger. Casual, unfinished and
  withdrawn results do not count.
- Later result corrections never remove an earned milestone. Future progress is
  evaluated from currently valid results.
- Local/vs-AI completion awards only the owning account and only for completed
  games; replay, reload and undo/redo cannot duplicate an award.
- Replay-analysis awards require a successful completed server advisor report on a
  completed record. Passive viewing, live analysis, partial/failed searches and
  browser-local analysis do not count.
- Manual portrait unlocks revalidate the earned milestone before claiming.
  Eligibility follows the permanent award, not current progress.

## Milestone rewards and claiming

Each achievement card shows the recorded progress, highest achieved milestone,
collected tier and next threshold. **Milestone rewards** expands the point history
and available claim. A claim can be made only once; if two requests race, one
wins and the other sees the already-claimed state.

A manually unlockable portrait shows a **Collect** action with its point cost. The
claim is permanent and appears immediately in the profile and Hall.

## Artwork provenance

The repository originally contained only 320×320 portrait derivatives. The
`*-large.webp` gallery files are identity-guided redraws with a source manifest
recording native dimensions, output dimensions, byte size and WebP quality; they
are not upscaled originals. The original 320px assets remain byte-for-byte
unchanged. See [artwork provenance](../development/progression-authority.md#artwork-provenance)
for generation details and known limitations.

## Known limitations

- Achievement progress can be lower than a previously earned milestone after an
  official correction; the earned award remains.
- The catalog score does not infer achievements from client-supplied values.
- A missing portrait asset falls back to a rune/initial; no URL is trusted from the
  client.
- Implementation details for catalogs, receipts, migrations and awarding live in
  [progression authority](../development/progression-authority.md).
