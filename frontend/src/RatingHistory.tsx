import { useCallback } from 'react';
import { accountName } from './accountIdentity';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import type {
  PersonalProgress,
  RatingHistory as RatingHistoryData,
  RatingHistoryEntry,
} from './personalTypes';
import { statisticRuleNames, statisticRulesets } from './personalStatisticsData';
import RatingChart from './RatingChart';
import { useOnlinePoll } from './useOnlinePoll';
import './personalStatistics.css';

/** One bounded read of the applied ledger; the newest page is enough for a curve. */
const HISTORY_LIMIT = 100;

const ratingHistoryPath = (ruleset: string) =>
  `/personal/ratings/${ruleset}/history?limit=${HISTORY_LIMIT}`;

const resultNames: Record<RatingHistoryEntry['result'], string> = {
  win: 'Win',
  loss: 'Loss',
  draw: 'Draw',
};

function RatingEntries({ items }: { items: RatingHistoryEntry[] }) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const format = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  const signed = (value: number) =>
    `${value > 0 ? '+' : ''}${number.format(Math.round(value * 10) / 10)}`;
  return (
    <details className="rating-entries">
      <summary>{t('Rating entries')}</summary>
      <div
        className="statistics-table-scroll"
        role="region"
        tabIndex={0}
        aria-label={t('Rating entries')}
      >
        <table className="statistics-table rating-entries-table">
          <caption className="sr-only">{t('Rating entries')}</caption>
          <thead>
            <tr>
              {['Date', 'Result', 'Change', 'Rating', 'Opponent'].map((label) => (
                <th key={label} scope="col">
                  {t(label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((entry) => (
              <tr key={`${entry.sequence}:${entry.game_id}`}>
                <th scope="row">
                  {entry.recorded_at === null ? (
                    t('Date unavailable')
                  ) : (
                    <time dateTime={entry.recorded_at}>
                      {format.format(new Date(entry.recorded_at))}
                    </time>
                  )}
                  {entry.corrected && (
                    <span className="badge rating-corrected">{t('Corrected result')}</span>
                  )}
                </th>
                <td data-label={t('Result')}>{t(resultNames[entry.result])}</td>
                <td data-label={t('Change')}>{signed(entry.delta)}</td>
                <td data-label={t('Rating')}>{number.format(Math.round(entry.rating_after))}</td>
                <td data-label={t('Opponent')}>
                  {accountName({
                    name: entry.opponent_display_name ?? '',
                    username: entry.opponent_username,
                  }) ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function RulesetRating({
  ruleset,
  accountId,
  streak,
  selected,
}: {
  ruleset: string;
  accountId: string;
  streak: PersonalProgress['streaks']['by_ruleset'][number] | undefined;
  selected: boolean;
}) {
  const { t, locale } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const load = useCallback(
    (signal: AbortSignal) =>
      api<RatingHistoryData>(ratingHistoryPath(ruleset), undefined, undefined, signal),
    [ruleset],
  );
  const poll = useOnlinePoll(`rating-history:${accountId}:${ruleset}`, load);
  const data = poll.data;
  const items = data?.items ?? [];
  const name = t(statisticRuleNames[ruleset] ?? ruleset);
  const current =
    data?.current_rating == null || !Number.isFinite(data.current_rating)
      ? null
      : number.format(Math.round(data.current_rating));
  const gaps = items.filter((entry) => entry.recorded_at === null).length;
  // The page flag covers a pool whose timestamp gaps sit on other pages; the
  // per-entry null covers this page. Neither ever fabricates a date.
  const gapNote =
    gaps > 0
      ? t('{count} entries have no recorded date; the ledger order still applies to them.', {
          count: number.format(gaps),
        })
      : data?.legacy_timestamp_gaps
        ? t('Some rated games have no recorded date; the ledger order still applies to them.')
        : null;
  return (
    <section
      className={`panel rating-ruleset-card${selected ? ' rating-ruleset-selected' : ''}`}
      aria-label={t('{ruleset} rating history', { ruleset: name })}
    >
      <div className="section-heading">
        <div>
          <h3>{name}</h3>
          {/* The pool count and streak come from the progress projection; while
              it is unavailable nothing is invented for this pool. */}
          {streak && (
            <small>{t('Rated games: {count}', { count: number.format(streak.rated_games) })}</small>
          )}
        </div>
        <div className="rating-ruleset-headline">
          <strong>{current ?? '—'}</strong>
          <span>{t('Current rating')}</span>
        </div>
      </div>
      {streak && (
        <p className="rating-streak">
          {t('Rated win streak')}:{' '}
          {t('Current {current} · Best {best}', {
            current: number.format(streak.rated_win_current),
            best: number.format(streak.rated_win_best),
          })}
        </p>
      )}
      {poll.phase === 'loading' && <p role="status">{t('Loading rating history…')}</p>}
      {poll.phase === 'stale' && (
        <p role="alert">
          {t('Rating history unavailable or stale. Retry to load current ratings.')}{' '}
          <button className="button secondary" onClick={() => void poll.refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
      {data && !items.length && <p>{t('No rated games in this pool yet.')}</p>}
      {items.length > 0 && (
        <>
          <RatingChart
            entries={items}
            label={t('{ruleset} rating progression', { ruleset: name })}
          />
          {gapNote && <p className="rating-gap-note">{gapNote}</p>}
          {(data?.matching ?? items.length) > items.length && (
            <p className="rating-gap-note">
              {t('Showing the most recent {count} rated games.', {
                count: number.format(items.length),
              })}
            </p>
          )}
          <RatingEntries items={items} />
        </>
      )}
    </section>
  );
}

/**
 * Verified Elo progression for every pool. The curve and the entries come from
 * the server's applied rating ledger, never from a game's start-rating snapshot.
 */
export default function RatingHistory({ ruleset }: { ruleset: string }) {
  const { t, locale } = useI18n();
  const { account } = useAuth();
  const number = new Intl.NumberFormat(locale);
  const load = useCallback(
    (signal: AbortSignal) =>
      api<PersonalProgress>('/personal/progress', undefined, undefined, signal),
    [],
  );
  const progress = useOnlinePoll(`rating-progress:${account?.id ?? 'anonymous'}`, load);
  const streaks = progress.data?.streaks;
  return (
    <section className="rating-history" aria-labelledby="rating-history-heading">
      <div className="section-heading">
        <div>
          <h2 id="rating-history-heading">{t('Rating history')}</h2>
          <p className="statistics-explanation">
            {t(
              'Rated online games only. Every value comes from the server rating ledger, never from a game start snapshot.',
            )}
          </p>
        </div>
        {streaks && (
          <div className="rating-history-streak">
            <strong>
              {t('Current {current} · Best {best}', {
                current: number.format(streaks.rated_win_current),
                best: number.format(streaks.rated_win_best),
              })}
            </strong>
            <span>{t('Rated win streak')}</span>
          </div>
        )}
      </div>
      {progress.phase === 'stale' && (
        <p role="alert">
          {t('Rating progress unavailable or stale. Retry to load current streaks.')}{' '}
          <button className="button secondary" onClick={() => void progress.refresh()}>
            {t('Retry')}
          </button>
        </p>
      )}
      <div className="rating-ruleset-grid">
        {statisticRulesets.map((id) => (
          <RulesetRating
            key={id}
            ruleset={id}
            accountId={account?.id ?? 'anonymous'}
            streak={streaks?.by_ruleset.find((entry) => entry.ruleset === id)}
            selected={ruleset === id}
          />
        ))}
      </div>
    </section>
  );
}
