import { useCallback, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import type { Leaderboard as LeaderboardData, LeaderboardEntry } from './onlineTypes';
import './leaderboard.css';

const rulesets = ['brandubh', 'tablut', 'copenhagen'] as const;
const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
const RULESET_KEY = 'hnefatafl-leaderboard-ruleset';
const isRuleset = (value: string | null): value is (typeof rulesets)[number] =>
  value !== null && (rulesets as readonly string[]).includes(value);
/** Default Copenhagen; ignore invalid values and unavailable storage. */
export function readStoredRuleset(): string {
  try {
    const value = localStorage.getItem(RULESET_KEY);
    if (isRuleset(value)) return value;
  } catch {
    /* Browser storage may be unavailable. */
  }
  return 'copenhagen';
}
export function persistRuleset(value: string) {
  if (!isRuleset(value)) return;
  try {
    localStorage.setItem(RULESET_KEY, value);
  } catch {
    /* Keep the in-memory selection. */
  }
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
                {entry.provisional ? (
                  <span className="badge leaderboard-provisional">{t('Provisional')}</span>
                ) : null}
              </td>
              <td>{Math.round(entry.rating)}</td>
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
  const [ruleset, setRuleset] = useState<string>(readStoredRuleset);
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
  const entries = data?.entries ?? [];
  const total = data?.total ?? 0;
  return (
    <section
      className={compact ? 'panel leaderboard-compact' : 'leaderboard-page'}
      aria-labelledby={compact ? 'dashboard-leaderboard-heading' : 'leaderboard-heading'}
    >
      <div className="section-heading leaderboard-heading-row">
        <h2 id={compact ? 'dashboard-leaderboard-heading' : 'leaderboard-heading'}>
          {t('Leaderboard')}
        </h2>
        <label className="leaderboard-filter">
          {t('Ruleset')}
          <select
            value={ruleset}
            onChange={(event) => {
              setRuleset(event.target.value);
              persistRuleset(event.target.value);
              setOffset(0);
            }}
          >
            {rulesets.map((value) => (
              <option value={value} key={value}>
                {t(ruleNames[value])}
              </option>
            ))}
          </select>
        </label>
      </div>
      {poll.phase === 'loading' && <p role="status">{t('Loading leaderboard…')}</p>}
      {poll.phase === 'stale' && (
        <p role="alert">
          {t('Leaderboard unavailable or stale. Retry to load current standings.')}{' '}
          <button className="button secondary" onClick={() => void poll.refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
      {data && total === 0 && <p>{t('No rated games in this pool yet.')}</p>}
      {data && total > 0 && (
        <>
          <Entries entries={entries} ownId={account?.id} caption="Leaderboard" />
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
                disabled={entries.length < pageSize || offset + pageSize > 100000}
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
