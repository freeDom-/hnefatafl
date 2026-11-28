# Records, language and appearance

[Game index](README.md) · [Profile](profile.md) · [Interface guide](interface/README.md) · [UI guidelines](../development/ui-guidelines.md)

## Recorded games and AI configurations

**The Great Hall** shows a short recent-record strip; **Hall of records** provides
filters, totals and read-only review. Your own account history is private; public
spectator history contains only eligible online games.

Statistics group equivalent AI configurations independently of their random seed.
Different algorithms, budgets or models remain separate identities. A recorded game
keeps its actual seed and configuration even if the same agent is grouped elsewhere.

## Models and suggested names

In the **AI Workshop**, the suggestion button beside the model name offers a
Viking-inspired name. Suggestions avoid existing model names and the current
suggestion; you can always type your own. Renaming a model keeps its ID and rune and
does not rewrite historical game labels.

Model cards support renaming, loading recorded training settings, export and deletion
under the restrictions in [training models](../training/models.md). Deleting a
registered model does not delete its source training job; deleting training data
does not delete a playable model copy.

## New-game choices and seeds

Every new account game receives a fresh server-generated seed. **Restart** is
available only for unfinished games under the current policy and uses a fresh seed.
Existing games keep their recorded moves and original seed semantics.

Single-player setup offers **Shield bearer**, **Shield maiden**, **Jarl** and
**Seer**. Administrators can additionally configure custom opponents and AI-vs-AI.
The dialog does not present a benchmarked strength ranking. Trained-model selection
and full search settings live in authorized advisor, training and tournament flows.

Successful setup preferences are remembered per browser: battlefield, mode, human
side and preset. Custom configurations, model IDs and fixed seeds are not saved as
browser creation preferences.

## Identity runes

Accounts and models have persistent presentation runes. Names can repeat; runes
distinguish identities across lists, games, advisors, analyses and records. A model
keeps its rune across budgets and sides. Renaming changes its label but not its rune.
AI agents use their normalized search configuration, excluding game seed, so
equivalent agents share a rune.

Runes are presentation metadata only. They never affect rules, model weights,
search identity, seed grouping or statistics. Earlier local-player tombstones retain
their reserved runes for historical records, but local-player creation is no longer
supported. Back up the complete application data directory to preserve assignments.

## Move advisor

**Analytics → Move advisor** selects an optional advisor; **None** is the default.
The advisor suggests a legal move without executing it and shows an expected-score
estimate. It runs in the background without blocking either player. Saved estimates
appear in replay; missing older evaluations are shown as absent. See
[replay and analysis](replay-and-analysis.md).

## Language

The header language selector switches between English and German. The choice is
remembered in the browser. Dates, numbers and relative times use the selected locale;
rules and help text fall back to English when a translation is missing.

## Appearance

Choose independently:

- **UI theme:** The King's Table, Iron Ash, Saga Hall or Classic Table.
- **Board:** Golden oak, Old walnut or Fjord slate.
- **Tokens:** Classic stones, Viking figures or Carved wood.
- **Interface scale:** Compact, Default or Large, or follow the display scale.

Preferences are saved in this browser and survive reload. Theme, board and token
choices do not affect rules or gameplay. Shared components and theme variables are
documented in the [frontend guide](../development/frontend.md); extension details are
developer documentation, not player settings.

## Privacy and device boundaries

Games, ratings, achievements and tournament records are server-owned. Browser storage
holds preferences, the last play route and optional retained creation requests; it is
not a copy of your saved games. Logging out or switching accounts clears account-bound
cached state. See [profile](profile.md) for public-field privacy and
[security](../administration/security.md) for account/session protections.
