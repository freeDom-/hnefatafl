# Guest play and device storage

[Documentation index](README.md) · [Account gameplay](GAMEPLAY.md) ·
[Online multiplayer](ONLINE_PLAY.md) · [Phase 4 evidence](validation/online-phase4.md)

Open **Guest play** at `/guest` without logging in. Select Brandubh, Tablut market
or Copenhagen and your side, then start. Select a piece and a highlighted
destination; keyboard arrows, Enter/Space and Escape work as on the account board.
The bounded baseline opponent moves automatically after your move, when it moves
first, and after a validated restore on its turn. A thinking indicator and cancel
control accompany computation; saving completes before another turn can begin.
Failures require an explicit retry and never create a retry loop. Operation guards
discard late cancelled/unmounted results; terminal games and historical replay
never start a computer turn. No model download, strength claim or server analysis
is involved.

Guest games are local, noncompetitive single-player. They never create server
games or enter account statistics, online results, ratings, leaderboards, rewards,
training collections or tournaments. Logging in does not upload or import them.
Account single-player still requires login and saves on the server; Online still
uses authenticated, server-authoritative multiplayer.

## One current or last game

The owner approved the Phase 0 one-slot policy with “Yes, I approve this” in the
implementation conversation on 2026-10-25 UTC. This approval covers one browser
slot, accepted-move saving, validated replay, confirmed replacement and explicit
clearing. It does not activate production admission, timeout forfeits or a PWA.

Only one current or last guest game is retained per browser profile and site
origin. Every accepted human/computer move saves it; replay navigation saves the
cursor. Previous/Next and Return to current position review that game's sequence
without branching it. Restoring a completed game recomputes its result.

**Start guest game** asks for confirmation when an in-memory or saved game exists,
including an unreadable save. Cancel initially has focus; Escape cancels. Confirming
replaces the single slot. There is no guest archive or game list. **Clear device
game** asks separately, then deletes the slot and its in-memory view. Language,
appearance and server account games remain independent.

Browser storage may be evicted or cleared, including private-profile/session
cleanup. Another browser, device, profile or site origin has its own slot. This
feature provides no backup, synchronization or durability guarantee.

## Validation and recovery

IndexedDB database `hnefatafl-guest-v1`, version 1, has one `device` object store:
`game` contains a JSON string, and `revision` is an opaque random UUID used for
atomic comparison. An empty database has no revision. There are no archived
revisions. A single readwrite transaction checks the revision and writes both
fields. If the transaction fails or aborts, the previous committed game remains.
Explicit clear writes a new revision while deleting the game, so a stale tab
cannot resurrect a cleared game; it also repairs damaged revision metadata.

The only accepted game fields are:

```json
{
  "schema": 1,
  "engine_version": "<64-character compatibility digest>",
  "ruleset": "brandubh",
  "ruleset_version": "1",
  "human_side": "attackers",
  "moves": [{"source": 3, "target": 1}],
  "cursor": 1
}
```

The envelope is limited to 262,144 UTF-8 bytes and 4,096 moves. Coordinates must
be integers within the chosen board. Schema, exact fields, ruleset/version, side
and cursor are checked before import. The worker independently validates and
replays every move from the canonical initial position through the existing
Python engine. Stored boards, winners, identities, credentials, CSRF tokens,
online IDs and AI evaluations are rejected. Illegal and post-terminal moves are
rejected; only the reconstructed result is displayed. The limits constrain
storage/import and never create an artificial game outcome.

A corrupt, illegal or incompatible save stays untouched and produces an error.
Choose clear or explicitly confirm replacement to recover. There is no automatic
migration or deletion. If storage is blocked, unavailable or full, play continues
in memory with a warning: reload may lose changes or restore the last successful
checkpoint. The same applies if a very long game exceeds storage limits. If
another tab changes the slot, actions stop and **Reload saved device game** offers
a confirmed discard of unsaved memory followed by fresh validation. Clear removes
the device slot explicitly, including changes made in another tab.

If a browser has an unknown IndexedDB version or cannot open its database, clearing
may also fail. The UI reports that failure without deleting preferences or server
data; the browser's site-storage settings remain the final recovery mechanism.
Cancelling or timing out a computation terminates its worker; retry reloads the
current in-memory sequence when available.

## Runtime, connection and updates

The application reuses the unchanged Python engine and AI in a dedicated Pyodide
0.27.7 worker. A first build downloads the SHA-512-pinned npm runtime; later builds
recheck its cached archive. Only named core files are extracted. Generated runtime
binaries are build output, not checked into Git. See
[runtime notices and corresponding source](../frontend/guest/licenses/NOTICE.md)
for MPL, CPython/standard-library and Emscripten licenses. The repository itself
has no newly assigned distribution license.

The package is approximately 14 MB raw and startup takes seconds. Exact local
measurements and their limits are in the validation record. Loading requires a
connection to obtain the app/runtime. Once loaded, moves and permitted AI compute
locally. No production service worker, cache installation, PWA or fully offline
startup is provided. Do not rely on the browser HTTP cache as an offline contract.

The build uses a content-addressed runtime directory and a no-store manifest. The
existing server sends no-store responses. Only the hashed worker's CSP permits
WebAssembly compilation (`wasm-unsafe-eval`); document, account and online policy
remains strict. JavaScript `unsafe-eval` is unnecessary. The appearance prepaint
script is a same-origin static file so strict document CSP can run it.

Engine/AI/guest/bridge or Pyodide-version changes rotate the replay compatibility
digest; unsupported saves fail visibly instead of silently replaying new rules.
Presentation/notice changes rotate the asset address without invalidating engine
compatibility. A build retains one runtime package; an old tab requesting removed
assets must reload the current app. Runtime upgrades require pin, notices, replay
parity and production-CSP review. Future persistent caching requires a separate
cache lifecycle/security/update design.

## Account and online compatibility

`/guest` mounts no Auth, account-game or online adapter. Its only requests fetch
static app/runtime assets with no game persistence endpoint. Navigation terminates
the guest worker. Returning validates the saved sequence; it never uploads it.
Account logout/switch cancels protected requests and drops protected UI state;
responses from an old session cannot install into the new account's UI.

Existing `hnefatafl-game`, `hnefatafl-new-game-options`,
`hnefatafl-account-cache` localStorage keys and tab-scoped
`hnefatafl-online-intent` retain their existing formats and account semantics.
Navigating to Guest preserves the current account selection; explicit logout or
account change still clears account-scoped selections. No existing game database,
history or selected-game key is migrated into the guest store. Online deep links
retain their login redirects and never load the guest engine. Active online
analysis/AI/export restrictions still apply to every role.
