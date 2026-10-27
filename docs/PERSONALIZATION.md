# Records, collections and appearance

[Documentation index](README.md) · [Gameplay](GAMEPLAY.md) · [Model and job data](TRAINING_DATA.md)

## Human profiles and history

New human seats are bound to the authenticated account, with its name shown in a
compact card. Normal accounts create Human vs AI; administrators can also create
AI vs AI. Pass-and-play, local-player selection/editing and local-player CRUD APIs
are removed. Stored player IDs/tombstones remain compatibility data for historical
game reads, statistics and runes; no historical data is deleted.

## AI configurations in the Hall of records

[Dashboard and Hall](DASHBOARD.md) now share compact read-only recorded-game rows.
Normal users browse participating online records; administrators can filter all
server-recorded online and account-single-player games by category, player name,
ruleset, lifecycle and result. Counts and category totals match the server filter.
Selected validated review is independent of the current page/filter. Historical
screenshots of the earlier statistics/Local players layout do not describe this UI.

The existing account statistics API continues to group normalized effective AI
configurations independently of seed. Different algorithms/budgets/models remain
separate identities; individual games retain their actual seed and configuration.
Account gameplay still supports its existing analytics/settings; read-only Hall
review grants no analysis, export or mutation controls. Model cleanup below retains
its existing explicit confirmation and authoritative reference checks.

## Models and suggested names

In **AI workshop**, use the name-suggestion button beside the model name to generate
a Viking-inspired name such as “Astrid Ravenwise”. Suggestions avoid names already
in the model collection and the current suggestion. You can always edit the name.

Each model card supports renaming, loading recorded training settings, export and
deletion. Renaming keeps its model ID and rune; historical reports retain their
recorded names. Model deletion requires confirmation,
removes the registered inference copy and its model entry, and refreshes the available
AI choices. Original training checkpoints remain for resumption or re-import.
Completed game histories and historical model labels remain.

Training data has a separate delete action on inactive jobs. It removes that
job's checkpoints and logs, preserving registered model copies and saved games.
See [training management](TRAINING_DATA.md#managing-models-and-retained-training-data)
for resumption consequences and shared-study protections.

The delete dialog checks the server's saved records, including unfinished games
from other browser sessions or games replaced by **New game**. The browser's
current-game pointer represents only one of those records. If unfinished games
still use the model, the dialog lists them and offers an unchecked **Discard
unfinished games and delete model** option. Choosing it removes those unfinished
games without a result or retained history. Completed games remain, including
completed games whose replay cursor is at an earlier position. Changed references
require reviewing the dialog again; deleting a model never silently removes new
references. Running tournaments must be stopped before their model can be deleted.

Model cards show **Training seed** from the saved training configuration. This is
different from an evaluation seed or a game seed. Older imports without the
training seed say **Not recorded**.

## New-game opponents and seeds

Every new account game receives a server-generated random base seed. Restart is
available for unfinished games created under the current policy, rechecks identity
and role, resolves the preset again and uses a fresh seed. Historical setups remain
readable/playable but cannot be cloned through Restart. Recorded moves remain the
replay authority; old seed semantics are retained when reading existing games.

Account setup offers only Shield bearer, Shield maiden, Jarl and Seer, with friendly
search-style explanations and time/depth/iteration limits. There is no benchmarked
strength ranking in this dialog. Trained-model selection and full search settings
remain in separate authorized advisor/training/tournament workflows; the shared
profile catalog still includes neural models. Those workflows can retain their
existing evidence-based model recommendation, without changing account setup.

Only successful permitted setup preferences are saved at
`hnefatafl-new-game-options` (version 2): battlefield, mode, human side and preset IDs.
Older preferences contribute only this allowlisted subset; saved identities,
models, custom settings and fixed seeds cannot restore removed creation options.
Cancel/failure does not replace a successful preference, and unavailable browser
storage does not prevent creation. Stored preferences stay account-scoped.

## Persistent runes and tournament names

Accounts and models receive persistent unique runes; historical local-player IDs
retain their earlier assignments. A model keeps the same rune
in dropdowns, its workshop card, either game side, advisor, analysis and records.
Retained human-profile tombstones keep their runes; no local-player CRUD is supported. Assignments are never recycled;
after the 24 single Elder Futhark runes, unique combinations extend the catalog.
See [IDENTITIES.md](IDENTITIES.md) for identity and migration details.

Tournament participants use their selected model or AI profile names; there is
no separate editable participant alias. Tournament titles remain editable northern
mythology suggestions. The same **⚄** dice control rerolls model and tournament
name suggestions. Seats show the underlying model/agent rune; repeated models are
distinguished by seat number and settings. Older seat-rune reservations remain
stored but are not a second current participant identity.

The header groups language and appearance at the top right. Decorative overlines
above page and panel headings have been removed; statistics retain their labels.

![Identified opponents in the simplified interface](screenshots/identified-opponents.png)

See the [mobile header and player runes](screenshots/cleaned-header-mobile.png).

## Agent opinions and move advice

Choose **Analytics → Move advisor** below the game's move history to highlight
an agent's best searched legal move. **None** is the default, with a 5-second
maximum search time. The settings are also available during history replay.
The advisor updates in the background without blocking either player. Playing
AI estimates reuse actual move searches; other opinions run in the background.
Selecting a move advisor calculates and displays the best-move hint together
with its winning-chance estimate; **None** closes the analysis section. Saved
snapshots appear during replay; missing older evaluations are explicit. See [ANALYSIS.md](ANALYSIS.md) for the value sources,
expected-score interpretation, draw handling and model-reference protection.

## Language

Use the header language selector to switch between English and German without
leaving the current game. The browser stores the choice under `hnefatafl-language`;
a fresh visit follows German browser language or otherwise English. Rules, controls,
accessible labels, number/date formatting and workshop/tournament text follow the
selection. User names and saved tournament/model names keep their original text.

Battlefields are consistently ordered by board size: Brandubh 7×7, Tablut 9×9,
Copenhagen 11×11, in new games, records and training.

## Color, tokens and boards

Open **Appearance** in the application header. The three settings are independent:

| Setting | Choices |
| --- | --- |
| Color mode | Follow device, Light, Dark, Wood |
| Token theme | Classic stones, Viking figures, Carved wood |
| Board theme | Golden oak, Old walnut, Fjord slate |

![Appearance settings with live previews](screenshots/appearance.png)

See the [Viking figures in dark mode](screenshots/dark-vikings.png),
[Fjord Diadem with carved wood on walnut](screenshots/default-wood-walnut.png), and
[mobile dark mode](screenshots/mobile.png) for actual rendered examples.

The Viking set renders shield bearers and a crowned king. The carved set uses
weathered wood grain and carved markings. Its king uses the selected **Fjord Diadem**:
a low three-peak northern circlet with an interlaced band, rendered as scalable SVG.
This motif applies only to Carved wood; the other token sets retain their king art.
See the [three king concepts and selected design](designs/king-options.md).
Side and king distinctions remain visible
in every theme. Legal destinations, move highlights and keyboard controls keep the
same behavior. Changing appearance does not change game state or AI parameters.

**Wood** turns the interface into a timber hall with bronze fittings, parchment
text, carved runes, axes, a dragonboat, a hammer and a shield. It remains independent
of the token and board themes, so it works with every available set.

See the [wood desktop view](screenshots/wood-theme-desktop.png) and
[wood mobile view](screenshots/wood-theme-mobile.png).

Preferences are stored in this browser under `hnefatafl-appearance`, not in player
profiles or the database. Follow-device mode responds to operating-system color
changes. Invalid saved values fall back to supported defaults. If browser storage is
unavailable, settings still work for the visit and the dialog reports the limitation.
Fresh browsers use **Follow device + Carved wood + Old walnut**. Valid saved choices
remain in effect; missing or invalid fields fall back to these defaults. Choose
those three settings to return to the defaults.

## Adding another theme

`frontend/src/appearanceConfig.ts` defines the typed catalogs and allowed preferences.
Add a token catalog entry and a corresponding renderer in `frontend/src/Tokens.tsx`,
then supply its visual treatment in `appearance.css`. Token previews use the same
renderer as the board, so one addition supplies both. Preserve `A`, `D` and `K`
distinctions and leave the board's accessible coordinate/piece labels intact.

For another board, add an entry to `BOARD_THEMES` with the existing palette keys:
light/dark squares, special-square variants, frame/rim/inner/edge, runes, selected
square/ring, last move and legal destination. The provider translates these to
`--board-*` CSS variables. Board and token catalogs are independent of each other
and of the rulesets. Test new combinations in both color modes and at phone sizes.
