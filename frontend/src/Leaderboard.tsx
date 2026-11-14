import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import PageHeading from './PageHeading';
import type { Leaderboard as LeaderboardData, LeaderboardEntry } from './onlineTypes';
import './leaderboard.css';
import { countryName } from './profileCountry';
import { accountName } from './accountIdentity';
import { surfaceLink } from './surfaceNavigation';

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

/** One decorative initial, never a fabricated portrait: the leaderboard DTO
 * carries no portrait id yet, so the layout slot stays clearly non-pictorial. */
export function avatarInitial(name: string | undefined): string {
  const trimmed = (name ?? '').trim();
  if (!trimmed) return '?';
  return Array.from(trimmed)[0].toUpperCase();
}

function Entries({
  entries,
  ownId,
  caption,
  details = false,
}: {
  entries: LeaderboardEntry[];
  ownId: string | undefined;
  caption: string;
  details?: boolean;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
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
            {details && (
              <>
                <th scope="col">{t('Country')}</th>
                <th scope="col">{t('City')}</th>
                <th scope="col">{t('Age')}</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const name = accountName(entry);
            return (
              <tr key={entry.account_id} className={entry.account_id === ownId ? 'you' : undefined}>
                <td>{number.format(entry.rank)}</td>
                <td>
                  <span className="leaderboard-avatar" aria-hidden="true">
                    {avatarInitial(name)}
                  </span>
                  {name}
                  {entry.account_id === ownId ? ` · ${t('You')}` : ''}
                  {entry.provisional ? (
                    <span className="badge leaderboard-provisional">{t('Provisional')}</span>
                  ) : null}
                </td>
                <td>{number.format(Math.round(entry.rating))}</td>
                <td>{number.format(entry.rated_games)}</td>
                {details && (
                  <>
                    <td>{entry.country_code ? countryName(entry.country_code, locale) : '—'}</td>
                    <td>{entry.city ?? '—'}</td>
                    <td>{entry.age ?? '—'}</td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The signed-in standing, kept outside the page so it is readable even when
 * the account is not on the visible slice. */
function YourRank({ data, onPage }: { data: LeaderboardData; onPage: boolean }) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  return (
    <div className="leaderboard-you" role="group" aria-label={t('Your rank')}>
      <span className="leaderboard-you-label">{t('Your rank')}</span>
      {data.me ? (
        <span className="leaderboard-you-value">
          <strong>#{number.format(data.me.rank)}</strong>
          {' · '}
          {t('Elo {rating}', { rating: number.format(Math.round(data.me.rating)) })}
          {' · '}
          {t('{count} rated games', { count: number.format(data.me.rated_games) })}
          {data.me.provisional && (
            <span className="leaderboard-provisional-note">
              {t('Provisional until {count} rated games.', {
                count: number.format(data.policy.provisional_games),
              })}
            </span>
          )}
          {!onPage && <span className="leaderboard-you-offpage">{t('Not on this page.')}</span>}
        </span>
      ) : (
        <span className="leaderboard-you-value">{t('You are not ranked in this pool yet.')}</span>
      )}
    </div>
  );
}

export default function Leaderboard({
  compact = false,
  fullStandings = false,
}: {
  compact?: boolean;
  /** Adds the link to the full standings page; the compact card stays pageless. */
  fullStandings?: boolean;
}) {
  const { t, locale } = useI18n();
  const { account } = useAuth();
  const number = new Intl.NumberFormat(locale);
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
  const page = Math.floor(offset / pageSize) + 1;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // A shrinking pool must not strand the view on an empty page.
  useEffect(() => {
    if (data && offset > 0 && offset >= data.total) setOffset(0);
  }, [data, offset]);
  const onPage = Boolean(
    data?.me && entries.some((entry) => entry.account_id === data.me?.account_id),
  );
  return (
    <section
      className={compact ? 'panel leaderboard-compact' : 'leaderboard-page'}
      aria-labelledby={compact ? 'dashboard-leaderboard-heading' : 'leaderboard-heading'}
    >
      <div className={compact ? 'section-heading leaderboard-heading-row' : 'page-toolbar'}>
        {compact ? (
          <h2 id="dashboard-leaderboard-heading" className="section-title">
            {t('Leaderboard')}
          </h2>
        ) : (
          <PageHeading title={t('Leaderboard')} id="leaderboard-heading" />
        )}
        {compact && fullStandings && (
          <a href="/leaderboard" onClick={surfaceLink}>
            {t('Full standings')} →
          </a>
        )}
        <div className="leaderboard-filter">
          <select
            aria-label={t('Ruleset')}
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
        </div>
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
      {data && !compact && <YourRank data={data} onPage={onPage} />}
      {data && total === 0 && <p>{t('No rated games in this pool yet.')}</p>}
      {data && total > 0 && (
        <>
          <Entries entries={entries} ownId={account?.id} caption="Leaderboard" details={!compact} />
          {!compact && (
            <nav className="leaderboard-pages" aria-label={t('Leaderboard pages')}>
              <button
                className="button secondary"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                {t('Previous page')}
              </button>
              <span>{t('Page {page} of {pages}', { page, pages: totalPages })}</span>
              <button
                className="button secondary"
                disabled={page >= totalPages}
                onClick={() => setOffset(offset + pageSize)}
              >
                {t('Next page')}
              </button>
            </nav>
          )}
        </>
      )}
      {data && compact && <YourRank data={data} onPage={onPage} />}
      {data && !compact && total > 0 && (
        <p className="leaderboard-total">
          {t('{count} ranked players in this pool.', { count: number.format(total) })}
        </p>
      )}
    </section>
  );
}
