# Records, collections and appearance

## Human profiles and history

Each human side in **New game** has a player selector. Choose an existing profile
or select the new-player option and enter a name. Switching to a new name clears
the previous profile ID; a dropdown choice identifies the player by ID, not just name.

Delete profiles in **Hall of records → Local players**. The confirmation explains
that existing history remains. Deletion removes the profile from new-game selectors;
saved games, results, replay, name and stable historical identity remain. A later
profile using the same display name is a separate identity. Existing saved games
can still be viewed and played. Starting another game from a record whose player
was deleted requires choosing active players in a new game.

## AI configurations in the Hall of records

Different effective AI configurations have separate records even when they share
an opponent name or model. Expand **AI parameters** under a recorded game or AI
performance row to inspect the actual algorithm and settings: depth, iteration/time
budget, rollout depth, exploration, temperature, seed, evaluation and model ID as
applicable. Compact summaries help distinguish opponents before opening details.

Identity uses normalized effective settings, not the order of JSON fields. Omitted
defaults and equivalent explicit defaults group together; different relevant parameter
values remain separate. Stale controls for a different algorithm do not create false
variants. Historical explicit-algorithm games keep their original engine-default
meaning rather than receiving a profile's different defaults retroactively.

## Models and suggested names

In **AI workshop**, use the name-suggestion button beside the model name to generate
a Viking-inspired name such as “Astrid Ravenwise”. Suggestions avoid names already
in the model collection and the current suggestion. You can always edit the name.

Each model card supports export and deletion. Deletion requires confirmation,
removes the registered inference copy and its model entry, and refreshes the available
AI choices. Original training checkpoints remain for resumption or re-import. Saved
game histories and historical model labels remain. A model referenced by an unfinished
game cannot be deleted while that game still needs it; finish the game first.

Battlefields are consistently ordered by board size: Brandubh 7×7, Tablut 9×9,
Copenhagen 11×11, in new games, records and training.

## Color, tokens and boards

Open **Appearance** in the application header. The three settings are independent:

| Setting | Choices |
| --- | --- |
| Color mode | Follow device, Light, Dark |
| Token theme | Classic stones, Viking figures, Carved wood |
| Board theme | Golden oak, Old walnut, Fjord slate |

![Appearance settings with live previews](screenshots/appearance.png)

See the [Viking figures in dark mode](screenshots/dark-vikings.png),
[carved wood on walnut](screenshots/carved-wood.png), and
[mobile dark mode](screenshots/mobile.png) for actual rendered examples.

The Viking set renders shield bearers and a crowned king. The carved set uses
weathered wood grain and carved markings. Side and king distinctions remain visible
in every theme. Legal destinations, move highlights and keyboard controls keep the
same behavior. Changing appearance does not change game state or AI parameters.

Preferences are stored in this browser under `hnefatafl-appearance`, not in player
profiles or the database. Follow-device mode responds to operating-system color
changes. Invalid saved values fall back to supported defaults. If browser storage is
unavailable, settings still work for the visit and the dialog reports the limitation.
Choose Follow device + Classic stones + Golden oak to return to the defaults.

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
