# Personal games and review

[Documentation index](README.md) · [Account play](GAMEPLAY.md) ·
[Online play](ONLINE_PLAY.md) · [Guest storage](GUEST_PLAY.md)

Sign in and choose **Dashboard**, or open `/dashboard`. This page shows only your
own account records and online participation, including when you are an
administrator. The existing Hall of records retains its established account and
administrator behavior.

## Find a game

**Account single-player** lists your server-backed records. Filter by ruleset,
unfinished/completed status and recorded winning side or draw. The totals describe
recorded sides: owning a game does not establish which human or AI seat is yours.
They therefore do not claim personal wins or losses. A retained completed line
continues to count after its shared play cursor has been moved backwards.

**Online multiplayer** starts with **Awaiting your move**. Other sections show
other active games, waiting games you created, all current games, or terminal
history. Overdue active games appear under other active games while awaiting
adjudication. The server decides turn eligibility and deadlines; a dashboard link
never permits a move after the deadline. Open a current game to use the dedicated
online play surface. Simultaneous and sequential matches link their individual
legs and statuses without an aggregate score or winner.

Choose **Online history** for completed, cancelled and voided records. Filter by
ruleset, lifecycle and your eligible outcome (win, loss, draw or your timeout).
Cancelled and voided games have no competitive outcome. Each category has its own
bounded pages, filters and totals. Totals cover the entire category independently
of page filters. Online totals count current eligible completed results per game;
a timeout is abandonment only for the timed-out player. Concession counts as a
loss, not abandonment. A void withdraws its superseded result from these totals.

Pages sort newest first, with game ID ascending for ties. Account pages use
creation time; online terminal history uses the latest terminal update time.
**Next page** continues after the last row. Reads are consistent within one
response, but the pages do not freeze the database: new records, moves, completion
and corrections can change membership or order between reads. Use **Refresh** to
return to the first page and see current results. A cursor belongs to its account,
category and filters; changing these starts again. Signing in through the existing
login redirect starts with the default filters. No browser history archive is
created. A stale/error notice means the displayed records may be old; use **Retry**.

## Review without changing a game

**Review account game** opens `/dashboard/single-player/{id}`. It reuses the existing
engine-validated retained replay while keeping the selected move in this viewer.
Previous/next never changes the shared account-game cursor or version. The existing
account play and Hall controls retain their own capabilities and authorization.

**Review online game** opens `/online/games/{id}/review` and uses participant-scoped
personal reads. The dedicated online viewer displays the authoritative lifecycle
and current effective result alongside engine replay. Concession/timeout may end
an otherwise non-terminal board. A void is shown as void with no winning side,
regardless of its old result. Review never moves, concedes, seeks, undoes or branches
a game and offers no hints, evaluation, analysis, imports or exports. Completed
online analysis still requires a separate product decision.

## Guest and account boundaries

**Guest play on this device** opens the existing `/guest` surface. The dashboard
never opens the guest database, reads its slot, uploads it, converts it, counts it
or adds it to any history. Its one-slot retention/replacement policy is unchanged.
Guest and account play use distinct adapters; online IDs only enter dedicated
online adapters. Logging out removes private dashboard state; account changes
abort reads and discard old responses. Existing setup/selection keys retain their
formats. Guest storage remains independent of authentication.
