import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { accountName } from './accountIdentity';
import { api, ApiError } from './api';
import { useI18n } from './i18n';
import { avatarInitial } from './Leaderboard';
import {
  leagueDate,
  leagueError,
  leagueMonth,
  SEASON_LABELS,
  TIER_LABELS,
} from './leaguePresentation';
import type {
  LeagueGame,
  LeaguePage,
  LeaguePlayer,
  LeagueRuleset,
  LeagueSeason,
  LeagueSeasonPage,
  LeagueStanding,
} from './leagueTypes';
import Portrait from './Portrait';
import { surfaceLink } from './surfaceNavigation';
import { useOnlinePoll } from './useOnlinePoll';

export function LeagueReadState({
  error,
  loading,
  retry,
}: {
  error: Error | null;
  loading: boolean;
  retry: () => void;
}) {
  const { t } = useI18n();
  if (error)
    return (
      <div className="form-error" role="alert">
        <p>{leagueError(error, t)}</p>
        <button className="button secondary" type="button" onClick={retry}>
          {t('Retry')}
        </button>
      </div>
    );
  return loading ? <p role="status">{t('Loading…')}</p> : null;
}

export function LeaguePlayerName({ player }: { player: LeaguePlayer }) {
  const { t } = useI18n();
  const name = accountName({
    name: player.display_name || player.username,
    username: player.username,
  });
  return (
    <>
      <span className="leaderboard-avatar" aria-hidden="true">
        {player.portrait_id ? <Portrait id={player.portrait_id} /> : avatarInitial(name)}
      </span>
      {name || t('Unknown player')}
    </>
  );
}

export function LeagueTable({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="leaderboard-table" role="region" aria-label={label} tabIndex={0}>
      <table className="leaderboard">
        <caption className="sr-only">{label}</caption>
        {children}
      </table>
    </div>
  );
}

export function useLeaguePages<T>(path: string, scope: string, intervalMs = 0) {
  const [pages, setPages] = useState<Array<string | null>>([null]);
  const cursor = pages[pages.length - 1];
  const load = useCallback(
    (signal: AbortSignal) => {
      const url = new URL(`/api${path}`, location.origin);
      url.searchParams.set('limit', '20');
      if (cursor) url.searchParams.set('cursor', cursor);
      return api<T>(url.pathname.slice(4) + url.search, undefined, undefined, signal);
    },
    [cursor, path],
  );
  const poll = useOnlinePoll(`league-pages:${scope}:${path}:${cursor ?? ''}`, load, { intervalMs });
  const retry = () => {
    if (
      pages.length > 1 &&
      poll.error instanceof ApiError &&
      ['league_page_changed', 'league_invalid_cursor'].includes(poll.error.code ?? '')
    )
      setPages([null]);
    else void poll.refresh();
  };
  return {
    ...poll,
    cursor,
    retry,
    hasPrevious: pages.length > 1,
    previous: () => setPages((current) => current.slice(0, -1)),
    next: (next: string) =>
      setPages((current) => (current.includes(next) ? current : [...current, next])),
  };
}

export function LeaguePageControls({
  label,
  hasPrevious,
  nextCursor,
  previous,
  next,
}: {
  label: string;
  hasPrevious: boolean;
  nextCursor: string | null;
  previous: () => void;
  next: (cursor: string) => void;
}) {
  const { t } = useI18n();
  if (!hasPrevious && !nextCursor) return null;
  return (
    <nav className="leaderboard-pages" aria-label={label}>
      <button className="button secondary" type="button" onClick={previous} disabled={!hasPrevious}>
        {t('Previous page')}
      </button>
      <button
        className="button secondary"
        type="button"
        disabled={!nextCursor}
        onClick={() => nextCursor && next(nextCursor)}
      >
        {t('Next page')}
      </button>
    </nav>
  );
}

export function LeagueSeasonSelector({
  accountId,
  current,
  selected,
  onSelect,
}: {
  accountId: string;
  current: LeagueSeason;
  selected: string;
  onSelect: (season: string) => void;
}) {
  const { t, locale } = useI18n();
  const [archive, setArchive] = useState<LeaguePage<LeagueSeason>>({
    items: [],
    next_cursor: null,
  });
  const pages = useLeaguePages<LeaguePage<LeagueSeason>>(
    '/leagues/seasons',
    `${accountId}:${current.id}`,
  );
  useEffect(() => {
    const incoming = pages.data;
    if (!incoming) return;
    setArchive((previous) => ({
      items: [
        ...new Map([...previous.items, ...incoming.items].map((item) => [item.id, item])).values(),
      ],
      next_cursor: incoming.next_cursor,
    }));
  }, [pages.data]);
  const older = archive.next_cursor;
  return (
    <div className="page-toolbar">
      <label className="league-filter">
        {t('Standings season')}
        <select value={selected} onChange={(event) => onSelect(event.currentTarget.value)}>
          <option value="">
            {t('Current season · {season}', { season: leagueMonth(current.starts_at, locale) })}
          </option>
          {archive.items
            .filter((season) => season.id !== current.id)
            .map((season) => (
              <option key={season.id} value={season.id}>
                {leagueMonth(season.starts_at, locale)}
              </option>
            ))}
        </select>
      </label>
      {older && (
        <button
          className="button secondary"
          type="button"
          disabled={pages.phase === 'loading'}
          onClick={() => pages.next(older)}
        >
          {t('Load older seasons')}
        </button>
      )}
      <LeagueReadState
        error={pages.error}
        loading={pages.phase === 'loading'}
        retry={pages.retry}
      />
    </div>
  );
}

type RecordScope = { accountId: string; season: string; ruleset: LeagueRuleset };

function recordsPath(kind: 'standings' | 'history', { season, ruleset }: RecordScope) {
  return `/leagues/${kind}?${new URLSearchParams({ season, ruleset })}`;
}

export function LeagueStandings({
  scope,
  revision,
  activity,
  onSeasonLoaded,
}: {
  scope: RecordScope;
  revision: number;
  activity: number;
  onSeasonLoaded: (season: LeagueSeason) => void;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const poll = useLeaguePages<LeagueSeasonPage<LeagueStanding>>(
    recordsPath('standings', scope),
    scope.accountId,
  );
  const refresh = poll.refresh;
  const seen = useRef({ revision, activity });
  useEffect(() => {
    if (seen.current.revision !== revision || seen.current.activity !== activity) {
      seen.current = { revision, activity };
      void refresh();
    }
  }, [activity, revision, refresh]);
  const season = poll.data?.season;
  useEffect(() => {
    if (season) onSeasonLoaded(season);
  }, [onSeasonLoaded, season]);
  const items = poll.data?.items ?? [];
  return (
    <>
      {season && (
        <p>
          {t(SEASON_LABELS[season.status])}
          {' · '}
          {t('Revision {revision}', { revision: number.format(season.revision) })}
        </p>
      )}
      <LeagueReadState error={poll.error} loading={poll.phase === 'loading'} retry={poll.retry} />
      {season?.status === 'rebuilding' ? (
        <div className="empty-state compact">
          <p>{t('Standings will return after the season rebuild completes.')}</p>
          <button className="button secondary" type="button" onClick={poll.retry}>
            {t('Refresh standings')}
          </button>
        </div>
      ) : poll.data && items.length === 0 ? (
        <p className="empty-state compact">
          {t('No players have qualified in this ruleset and season.')}
        </p>
      ) : items.length > 0 ? (
        <LeagueTable label={t('Qualified players')}>
          <thead>
            <tr>
              {['Rank', 'Player', 'Tier', 'Points', 'Wins', 'Draws', 'Losses'].map((label) => (
                <th key={label} scope="col">
                  {t(label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.player.id}
                className={item.player.id === scope.accountId ? 'you' : undefined}
              >
                <td>{number.format(item.rank)}</td>
                <th scope="row">
                  <LeaguePlayerName player={item.player} />
                </th>
                <td>{t(TIER_LABELS[item.tier])}</td>
                <td>{number.format(item.points)}</td>
                <td>{number.format(item.wins)}</td>
                <td>{number.format(item.draws)}</td>
                <td>{number.format(item.losses)}</td>
              </tr>
            ))}
          </tbody>
        </LeagueTable>
      ) : null}
      <LeaguePageControls
        {...poll}
        label={t('Standings pages')}
        nextCursor={poll.data?.next_cursor ?? null}
      />
    </>
  );
}

const GAME_STATES: Record<LeagueGame['state'], string> = {
  pending: 'Pending settlement',
  eligible: 'Counts toward the season',
  late: 'Finished after the scoring deadline',
  withdrawn: 'Withdrawn from seasonal standings',
};
const GAME_RESULTS = { win: 'Win', draw: 'Draw', loss: 'Loss' } as const;

export function LeagueHistory({
  scope,
  currentSeason,
  activity,
  onSettled,
}: {
  scope: RecordScope;
  currentSeason: boolean;
  activity: number;
  onSettled: () => void;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const [pending, setPending] = useState({ key: '', reads: 0 });
  const ownRevisions = useRef<{ cursor: string | null; signature: string } | null>(null);
  const seenActivity = useRef(activity);
  const poll = useLeaguePages<LeagueSeasonPage<LeagueGame>>(
    recordsPath('history', scope),
    scope.accountId,
    pending.key && pending.reads < 6 ? 8000 : 0,
  );
  const refresh = poll.refresh;
  useEffect(() => {
    if (seenActivity.current !== activity) {
      seenActivity.current = activity;
      void refresh();
    }
  }, [activity, refresh]);
  useEffect(() => {
    if (!poll.data) return;
    const key = poll.data.items
      .filter((game) => game.state === 'pending' && game.game_status !== 'active')
      .map((game) => game.game_id)
      .join(',');
    setPending((previous) => {
      if (key !== previous.key) return { key, reads: 0 };
      return key ? { key, reads: previous.reads + 1 } : previous;
    });
    const effective = JSON.stringify(
      poll.data.items
        .filter((game) => game.state !== 'pending')
        .map((game) => [game.game_id, game.result_revision, game.state]),
    );
    if (
      currentSeason &&
      ownRevisions.current?.cursor === poll.cursor &&
      effective !== ownRevisions.current.signature
    )
      onSettled();
    ownRevisions.current = { cursor: poll.cursor, signature: effective };
  }, [currentSeason, onSettled, poll.cursor, poll.data]);
  const items = poll.data?.items ?? [];
  const season = poll.data?.season;
  return (
    <section className="arena-surface league-panel" aria-labelledby="league-history-heading">
      <h2 id="league-history-heading">{t('League match history')}</h2>
      {season && <p>{leagueMonth(season.starts_at, locale)}</p>}
      {pending.key && (
        <div className="page-toolbar">
          <button
            className="button secondary"
            type="button"
            onClick={() => {
              setPending((previous) => ({ ...previous, reads: 0 }));
              void refresh();
            }}
          >
            {t('Refresh match history')}
          </button>
        </div>
      )}
      <LeagueReadState error={poll.error} loading={poll.phase === 'loading'} retry={poll.retry} />
      {poll.data && items.length === 0 ? (
        <p className="empty-state compact">
          {t('You have no official league games in this ruleset and season.')}
        </p>
      ) : items.length > 0 ? (
        <LeagueTable label={t('Official league games')}>
          <thead>
            <tr>
              {['Date', 'Opponent', 'Side', 'Official result', 'Seasonal scoring', 'Game'].map(
                (label) => (
                  <th key={label} scope="col">
                    {t(label)}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {items.map((game) => (
              <tr key={game.game_id}>
                <td>
                  <time dateTime={game.paired_at}>{leagueDate(game.paired_at, locale)}</time>
                </td>
                <th scope="row">
                  <LeaguePlayerName player={game.opponent} />
                </th>
                <td>{t(game.side === 'attackers' ? 'Attacker' : 'Defender')}</td>
                <td>
                  {t(
                    game.game_status === 'voided'
                      ? 'Voided'
                      : game.game_status === 'cancelled'
                        ? 'Cancelled'
                        : game.result
                          ? GAME_RESULTS[game.result]
                          : 'Awaiting official result',
                  )}
                  {game.corrected && game.result_revision != null && (
                    <small>
                      {t('Corrected · revision {revision}', {
                        revision: number.format(game.result_revision),
                      })}
                    </small>
                  )}
                </td>
                <td>
                  {t(
                    game.state === 'pending' && game.game_status === 'active'
                      ? 'In progress'
                      : GAME_STATES[game.state],
                  )}
                </td>
                <td className="league-game-links">
                  <a href={game.game_href} onClick={surfaceLink}>
                    {t('Open game')}
                  </a>
                  {game.replay_href && (
                    <a href={game.replay_href} onClick={surfaceLink}>
                      {t('View replay')}
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </LeagueTable>
      ) : null}
      <LeaguePageControls
        {...poll}
        label={t('Match history pages')}
        nextCursor={poll.data?.next_cursor ?? null}
      />
    </section>
  );
}
