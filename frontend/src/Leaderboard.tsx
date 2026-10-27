import { useCallback, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import { useOnlinePoll } from './useOnlinePoll';
import type { Leaderboard as LeaderboardData, LeaderboardEntry } from './onlineTypes';
import './leaderboard.css';

const rulesets = ['brandubh', 'tablut', 'copenhagen'] as const;

function formatRating(value: number) {
  return String(Math.round(value));
}

function Entries({
  entries,
  ownId,
  caption,
}: {
  entries: LeaderboardEntry[];
  ownId: string | undefined;
  caption: string;
}) {
  const { t } = useI18n();
  return (
    <div className="leaderboard-table">
      <table className="leaderboard">
        <caption className="sr-only">{t(caption)}</caption>
        <thead>
          <tr>
            <th scope="col">{t('Rank')}</th>
            <th scope="col">{t('Player')}</th>
            <th scope="col">{t('Rating')}</th>
            <th scope="col">{t('Rated games')}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.account_id} className={entry.account_id === ownId ? 'you' : undefined}>
              <td>{entry.rank}</td>
              <td>
                {entry.name}
                {entry.account_id === ownId ? ` · ${t('You')}` : ''}
              </td>
              <td>{formatRating(entry.rating)}</td>
              <td>{entry.rated_games}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Leaderboard({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const { account } = useAuth();
  const [ruleset, setRuleset] = useState<string>('tablut');
  const [offset, setOffset] = useState(0);
  const pageSize = compact ? 5 : 25;
  const load = useCallback(
    (signal: AbortSignal) =>
      api<LeaderboardData>(
        `/leaderboard?ruleset=${ruleset}&limit=${pageSize}&offset=${offset}`,
        undefined,
        undefined,
        signal,
      ),
    [ruleset, offset, pageSize],
  );
  const poll = useOnlinePoll(
    `leaderboard:${account?.id ?? 'anonymous'}:${ruleset}:${offset}`,
    load,
  );
  const data = poll.data;
  const qualified = data?.qualified?.entries ?? [];
  const provisional = data?.provisional?.entries ?? [];
  const empty = !!data && !qualified.length && !provisional.length;
  return (
    <section
      className={compact ? 'panel leaderboard-compact' : 'leaderboard-page'}
      aria-labelledby={compact ? 'dashboard-leaderboard-heading' : 'leaderboard-heading'}
    >
      <div className="section-heading">
        <h2 id={compact ? 'dashboard-leaderboard-heading' : 'leaderboard-heading'}>
          {t('Leaderboard')}
        </h2>
        {compact && (
          <a href="/leaderboard" onClick={surfaceLink}>
            {t('View full leaderboard')}
          </a>
        )}
      </div>
      <label className="leaderboard-filter">
        {t('Ruleset')}
        <select
          value={ruleset}
          onChange={(event) => {
            setRuleset(event.target.value);
            setOffset(0);
          }}
        >
          {rulesets.map((value) => (
            <option value={value} key={value}>
              {t(value === 'brandubh' ? 'Brandubh' : value === 'tablut' ? 'Tablut' : 'Copenhagen')}
            </option>
          ))}
        </select>
      </label>
      {poll.phase === 'loading' && <p role="status">{t('Loading leaderboard…')}</p>}
      {poll.phase === 'stale' && (
        <p role="alert">
          {t('Leaderboard unavailable or stale. Retry to load current standings.')}{' '}
          <button className="button secondary" onClick={() => void poll.refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
      {data && empty && <p>{t('No rated games in this pool yet.')}</p>}
      {data && !empty && (
        <>
          {data.me && (
            <p className="leaderboard-me" role="status">
              {t('Your standing')}: {data.me.rank} · {formatRating(data.me.rating)} ·{' '}
              {t('{count} rated games', { count: data.me.rated_games })}
              {data.me.provisional ? ` · ${t('Provisional')}` : ''}
            </p>
          )}
          {!data.me && (
            <p className="leaderboard-me">{t('You are not ranked in this pool yet.')}</p>
          )}
          <h3>{t('Qualified standings')}</h3>
          {qualified.length ? (
            <Entries entries={qualified} ownId={account?.id} caption="Qualified standings" />
          ) : (
            <p>{t('No qualified players in this pool yet.')}</p>
          )}
          {provisional.length > 0 && (
            <>
              <h3>{t('Provisional players')}</h3>
              <p className="subtle">
                {t('Provisional until {count} rated games.', {
                  count: data.policy.provisional_games,
                })}
              </p>
              <Entries entries={provisional} ownId={account?.id} caption="Provisional players" />
            </>
          )}
          {!compact && (
            <nav className="leaderboard-pages" aria-label={t('Leaderboard pages')}>
              <button
                className="button secondary"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                {t('Previous page')}
              </button>
              <span>{t('Page {page}', { page: offset / pageSize + 1 })}</span>
              <button
                className="button secondary"
                disabled={qualified.length < pageSize || offset + pageSize > 100000}
                onClick={() => setOffset(offset + pageSize)}
              >
                {t('Next page')}
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
