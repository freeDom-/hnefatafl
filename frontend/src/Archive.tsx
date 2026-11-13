import PageHeading from './PageHeading';
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { useOnlinePoll } from './useOnlinePoll';
import { navigate } from './navigation';
import RecordedGames, {
  categoryName,
  type RecordedCategory,
  type RecordedPage,
  type RecordedGame,
} from './RecordedGames';
import RecordedReview from './RecordedReview';
import Tabs, { type TabItem } from './Tabs';
import './personalDashboard.css';
import { surfaceLink } from './surfaceNavigation';

/** One scope per authorized record family; local never mixes into online. */
const scopeTabs: readonly TabItem[] = [
  { id: 'all', label: 'All records' },
  { id: 'online', label: 'Online multiplayer' },
  { id: 'account-single-player', label: 'Singleplayer' },
  { id: 'local', label: 'Local 1v1' },
];

/**
 * Lifecycle values each scope can actually return: online records use the
 * online lifecycle, private single-player/local records report only
 * in_progress/completed, and the default projection spans both families.
 */
const scopeStatuses: Record<string, readonly string[]> = {
  all: ['waiting', 'active', 'in_progress', 'completed', 'cancelled', 'voided'],
  online: ['waiting', 'active', 'completed', 'cancelled', 'voided'],
  'account-single-player': ['in_progress', 'completed'],
  local: ['in_progress', 'completed'],
};

const statusesForScope = (scope: string) => scopeStatuses[scope] ?? scopeStatuses.all;

/**
 * A direct or bookmarked URL can carry a lifecycle its scope never returns; the
 * `<select>` then hides the option while the backend still applies the filter.
 * Returns the corrected search with the incompatible status and cursor removed,
 * or `null` when the URL already matches the scope policy. Idempotent: the
 * corrected search no longer carries an incompatible status.
 */
function normalizeScopeStatus(search: string): string | null {
  const params = new URLSearchParams(search);
  const status = params.get('status');
  if (!status || statusesForScope(params.get('category') ?? 'all').includes(status)) return null;
  params.delete('status');
  params.delete('cursor');
  const next = params.toString();
  return next ? '?' + next : '';
}

const lifecycleLabels: Record<string, string> = {
  waiting: 'Waiting',
  active: 'Active',
  in_progress: 'In progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
  voided: 'Voided',
};

export default function Archive() {
  const { account } = useAuth();
  const { t } = useI18n();
  const [query, setQuery] = useState(location.search);
  useEffect(() => {
    const changed = () => setQuery(location.search);
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  // Canonicalize once per URL: replaceState keeps the corrected URL on the same
  // history entry, so Browser Back never revisits the invalid intermediate state.
  const normalized = normalizeScopeStatus(query);
  useEffect(() => {
    if (normalized === null) return;
    history.replaceState(null, '', location.pathname + normalized + location.hash);
    setQuery(normalized);
  }, [normalized]);
  const params = new URLSearchParams(normalized ?? query);
  const admin = account?.permissions.includes('all_data');
  const request = new URLSearchParams({ limit: '20' });
  for (const key of ['category', 'player', 'ruleset', 'status', 'winner', 'cursor'])
    if (params.get(key)) request.set(key, params.get(key)!);
  const url = '/history/games?' + request;
  const load = useCallback(
    (signal: AbortSignal) => api<RecordedPage>(url, undefined, undefined, signal),
    [url],
  );
  const poll = useOnlinePoll(`hall:${account?.id}:${url}`, load);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(query);
    value ? next.set(key, value) : next.delete(key);
    if (key !== 'cursor') next.delete('cursor');
    navigate('/archive?' + next);
  };
  // A scope change can hide the active lifecycle option; drop it so a filter the
  // new scope cannot return never stays applied invisibly.
  const changeScope = (value: string) => {
    const next = new URLSearchParams(query);
    if (value === 'all') next.delete('category');
    else next.set('category', value);
    const status = next.get('status');
    if (status && !statusesForScope(value).includes(status)) next.delete('status');
    next.delete('cursor');
    navigate('/archive?' + next);
  };
  const selected = params.get('selected');
  const review = params.get('review');
  const scope = params.get('category') ?? 'all';
  const statuses = statusesForScope(scope);
  const selectedCategory =
    review === 'account-single-player' || review === 'local'
      ? (review as RecordedCategory)
      : 'online';
  const link = (game: RecordedGame) => {
    const next = new URLSearchParams(query);
    next.set('review', game.category);
    next.set('selected', game.id);
    next.delete('view');
    return '/archive?' + next;
  };
  if (selected && account && params.get('view') !== 'list') {
    const back = new URLSearchParams(query);
    back.set('view', 'list');
    return (
      <RecordedReview
        key={`${account.id}:${selectedCategory}:${selected}`}
        id={selected}
        category={selectedCategory}
        accountId={account.id}
        backTo={'/archive' + (back.size ? '?' + back : '')}
      />
    );
  }
  return (
    <section className="hall-page">
      {selected && (
        <a
          className="button secondary"
          href={'/archive?' + new URLSearchParams([...params].filter(([key]) => key !== 'view'))}
          onClick={surfaceLink}
        >
          {t('Return to selected review')}
        </a>
      )}
      <PageHeading
        title={t('Hall of records')}
        subtitle={t(
          admin
            ? 'All server-recorded games.'
            : 'Your participating online games and your private single-player and local 1v1 records.',
        )}
      />
      <p className="subtle">
        {t(
          'Active online games open the live board; completed records open read-only replay with analysis.',
        )}
      </p>
      <Tabs
        id="hall-scope"
        items={scopeTabs.map((item) =>
          item.id === 'all' && admin ? { ...item, label: 'All server games' } : item,
        )}
        active={scope}
        onChange={changeScope}
        label="Record scope"
        className="hall-scope"
      />
      <div className="personal-filters">
        {admin && (
          <label>
            {t('Player name')}
            <input
              value={params.get('player') ?? ''}
              onChange={(e) => {
                // Match the API's 80 + 64 + 4 code-point label bound;
                // native maxLength counts UTF-16 units and truncates emoji.
                if ([...e.target.value].length <= 148) update('player', e.target.value);
              }}
            />
          </label>
        )}
        <label>
          {t('Ruleset')}
          <select
            value={params.get('ruleset') ?? ''}
            onChange={(e) => update('ruleset', e.target.value)}
          >
            <option value="">{t('All rulesets')}</option>
            {['brandubh', 'tablut', 'copenhagen'].map((r) => (
              <option key={r} value={r}>
                {t(r)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('Lifecycle')}
          <select
            value={params.get('status') ?? ''}
            onChange={(e) => update('status', e.target.value)}
          >
            <option value="">{t('All statuses')}</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {t(lifecycleLabels[s])}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('Recorded result')}
          <select
            value={params.get('winner') ?? ''}
            onChange={(e) => update('winner', e.target.value)}
          >
            <option value="">{t('All results')}</option>
            <option value="attackers">{t('Attackers win')}</option>
            <option value="defenders">{t('Defenders win')}</option>
            <option value="draw">{t('Draw')}</option>
          </select>
        </label>
      </div>
      {poll.phase === 'loading' && <p role="status">{t('Loading your games…')}</p>}
      {poll.phase === 'stale' && (
        <p role="alert">{t('History unavailable or stale. Retry to load current records.')}</p>
      )}
      <button
        className="button secondary"
        onClick={() => (params.has('cursor') ? update('cursor', '') : void poll.refresh())}
      >
        {t(poll.phase === 'stale' ? 'Retry' : 'Refresh')}
      </button>
      <div className="hall-totals">
        {poll.data?.totals.map((total) => (
          <p key={total.category}>
            <strong>{t(categoryName(total.category))}</strong>: {total.games} {t('Games')} ·{' '}
            {total.completed} {t('Completed')} · {total.attackers_wins} {t('Attacker wins')} ·{' '}
            {total.defenders_wins} {t('Defender wins')} · {total.draws} {t('Draws')}
          </p>
        ))}
      </div>
      {poll.phase === 'ready' && !poll.data?.games.length && (
        <p>{t('No games match these filters.')}</p>
      )}
      <RecordedGames games={poll.data?.games ?? []} selected={selected ?? undefined} link={link} />
      <nav className="online-toolbar" aria-label={t('History pages')}>
        <button
          className="button secondary"
          disabled={!params.has('cursor') || poll.phase !== 'ready'}
          onClick={() => update('cursor', '')}
        >
          {t('First page')}
        </button>
        <span>
          {t('Showing {shown} of {matching} matching games', {
            shown: poll.data?.games.length ?? 0,
            matching: poll.data?.matching ?? 0,
          })}
        </span>
        <button
          className="button secondary"
          disabled={!poll.data?.next_cursor || poll.phase !== 'ready'}
          onClick={() => update('cursor', poll.data!.next_cursor!)}
        >
          {t('Next page')}
        </button>
      </nav>
    </section>
  );
}
