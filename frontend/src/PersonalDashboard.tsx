import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import Board from './Board';
import { useI18n } from './i18n';
import { modeNames } from './Online';
import ReplayControls from './ReplayControls';
import { useOnlinePoll } from './useOnlinePoll';
import type {
  AccountHistory,
  AccountReviewData,
  Paging,
  PersonalOnlineHistory,
} from './personalTypes';
import { surfaceLink } from './surfaceNavigation';
import './personalDashboard.css';

const rules = ['brandubh', 'tablut', 'copenhagen'];
const statusNames: Record<string, string> = {
  waiting: 'Waiting',
  active: 'Active',
  completed: 'Completed',
  cancelled: 'Cancelled',
  voided: 'Voided',
  in_progress: 'In progress',
};
const outcomeNames: Record<string, string> = { win: 'Win', loss: 'Loss', draw: 'Draw' };
function navigate(path: string) {
  history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
function LocalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} onClick={surfaceLink}>
      {children}
    </a>
  );
}
function Health({ phase, refresh }: { phase: string; refresh: () => void }) {
  const { t } = useI18n();
  return (
    <div className="personal-health">
      {phase === 'loading' && <p role="status">{t('Loading your games…')}</p>}
      {phase === 'stale' && (
        <p role="alert">{t('History unavailable or stale. Retry to load current records.')}</p>
      )}
      <button className="button secondary" onClick={refresh}>
        {t(phase === 'stale' ? 'Retry' : 'Refresh')}
      </button>
    </div>
  );
}
function RuleFilter({ value, change }: { value: string; change: (value: string) => void }) {
  const { t } = useI18n();
  return (
    <label>
      {t('Ruleset')}
      <select value={value} onChange={(e) => change(e.target.value)}>
        <option value="">{t('All rulesets')}</option>
        {rules.map((rule) => (
          <option key={rule} value={rule}>
            {t(rule)}
          </option>
        ))}
      </select>
    </label>
  );
}
function Pages({
  data,
  shown,
  cursor,
  change,
  disabled,
}: {
  data: Paging | null;
  shown: number;
  cursor: string;
  change: (value: string) => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  return (
    <nav className="online-toolbar" aria-label={t('History pages')}>
      <button
        className="button secondary"
        disabled={!cursor || disabled}
        onClick={() => change('')}
      >
        {t('First page')}
      </button>
      <span>
        {t('Showing {shown} of {matching} matching games', {
          shown,
          matching: data?.matching ?? 0,
        })}
      </span>
      <button
        className="button secondary"
        disabled={!data?.next_cursor || disabled}
        onClick={() => change(data!.next_cursor!)}
      >
        {t('Next page')}
      </button>
    </nav>
  );
}
function AccountReview({ id, accountId }: { id: string; accountId: string }) {
  const { t } = useI18n();
  const load = useCallback(
    (signal: AbortSignal) =>
      api<AccountReviewData>(`/personal/single-player/${id}/replay`, undefined, undefined, signal),
    [id],
  );
  const poll = useOnlinePoll(`personal-account-review:${accountId}:${id}`, load);
  const [cursor, setCursor] = useState(0);
  const data = poll.data;
  const index = Math.min(cursor, Math.max(0, (data?.states.length ?? 1) - 1));
  const position = data?.states[index];
  return (
    <section className="personal-dashboard">
      <LocalLink href="/dashboard">{t('Back to dashboard')}</LocalLink>
      <h1>{t('Account game review')}</h1>
      <p>{t('Read-only review. Replay controls change only your view.')}</p>
      <Health phase={poll.phase} refresh={() => void poll.refresh()} />
      {data && position && data.validated && (
        <>
          <p>
            {t(data.game.ruleset)} ·{' '}
            {t(data.game.status === 'completed' ? 'Completed' : 'In progress')}
          </p>
          <p>
            {t('Recorded result')}:{' '}
            {data.game.winner
              ? t(
                  data.game.winner === 'draw'
                    ? 'Draw'
                    : data.game.winner === 'attackers'
                      ? 'Attackers win'
                      : 'Defenders win',
                )
              : t('Unfinished record')}
          </p>
          <div className="personal-review-board">
            <Board
              game={{
                ...position,
                size: Math.sqrt(position.board.length),
                moves: data.moves,
                cursor: index,
                legal_moves: [],
              }}
              selected={null}
              onSelect={() => {}}
              readOnly
            />
          </div>
          <ReplayControls
            cursor={index}
            length={data.states.length}
            onChange={setCursor}
            disabled={poll.phase !== 'ready'}
          />
        </>
      )}
    </section>
  );
}
function DashboardHome({ accountId, query }: { accountId: string; query: string }) {
  const { t, locale } = useI18n();
  const params = new URLSearchParams(query);
  const get = (key: string) => params.get(key) ?? '';
  const sr = get('sr'),
    ss = get('ss'),
    sw = get('sw'),
    sc = get('sc');
  const or = get('or'),
    os = get('os'),
    oo = get('oo'),
    oc = get('oc'),
    ov = get('ov') || 'your_turn';
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(location.search);
    value ? next.set(key, value) : next.delete(key);
    if (key !== 'sc' && key.startsWith('s')) next.delete('sc');
    if (key !== 'oc' && key.startsWith('o')) next.delete('oc');
    navigate('/dashboard' + (next.size ? '?' + next.toString() : ''));
  };
  const accountQuery = new URLSearchParams({ limit: '10' });
  for (const [key, value] of [
    ['ruleset', sr],
    ['status', ss],
    ['winner', sw],
    ['cursor', sc],
  ])
    if (value) accountQuery.set(key, value);
  const onlineQuery = new URLSearchParams({ limit: '10', section: ov });
  for (const [key, value] of [
    ['ruleset', or],
    ['status', os],
    ['outcome', oo],
    ['cursor', oc],
  ])
    if (value) onlineQuery.set(key, value);
  const localUrl = '/personal/single-player?' + accountQuery;
  const onlineUrl = '/personal/online?' + onlineQuery;
  const loadAccount = useCallback(
    (signal: AbortSignal) => api<AccountHistory>(localUrl, undefined, undefined, signal),
    [localUrl],
  );
  const loadOnline = useCallback(
    (signal: AbortSignal) => api<PersonalOnlineHistory>(onlineUrl, undefined, undefined, signal),
    [onlineUrl],
  );
  const account = useOnlinePoll(`personal-account:${accountId}:${localUrl}`, loadAccount);
  const online = useOnlinePoll(`personal-online:${accountId}:${onlineUrl}`, loadOnline);
  const date = (value: string) =>
    Number.isNaN(Date.parse(value))
      ? t('Date unavailable')
      : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
          new Date(value),
        );
  const sections = [
    ['your_turn', 'Awaiting your move'],
    ['other_active', 'Other active games'],
    ['waiting', 'Waiting games you created'],
    ['current', 'All current online games'],
    ['history', 'Online history'],
  ];
  return (
    <section className="personal-dashboard">
      <h1>{t('Your games')}</h1>
      <p>{t('Private account records. Single-player and online results are kept separate.')}</p>
      <p>
        <LocalLink href="/guest">{t('Guest play on this device')}</LocalLink> ·{' '}
        {t('Guest games stay in the device slot and never appear in account history.')}
      </p>
      <div className="personal-sections">
        <section className="panel personal-section" aria-labelledby="personal-account-heading">
          <h2 id="personal-account-heading">{t('Account single-player')}</h2>
          <p>
            {t('All your account single-player records')} ·{' '}
            {t('Totals are independent of the page filters.')}
          </p>
          {account.data && (
            <dl className="personal-totals">
              {(
                [
                  ['total_games', 'Games'],
                  ['completed_games', 'Completed'],
                  ['in_progress', 'In progress'],
                  ['attackers_wins', 'Attacker wins'],
                  ['defenders_wins', 'Defender wins'],
                  ['draws', 'Draws'],
                ] as const
              ).map(([key, label]) => (
                <div key={key}>
                  <dt>{t(label)}</dt>
                  <dd>{account.data!.totals[key]}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="subtle">
            {t('Account totals describe recorded sides, not personal wins or losses.')}
          </p>
          <div className="personal-filters">
            <RuleFilter value={sr} change={(v) => update('sr', v)} />
            <label>
              {t('Status')}
              <select value={ss} onChange={(e) => update('ss', e.target.value)}>
                <option value="">{t('All statuses')}</option>
                <option value="in_progress">{t('In progress')}</option>
                <option value="completed">{t('Completed')}</option>
              </select>
            </label>
            <label>
              {t('Recorded result')}
              <select value={sw} onChange={(e) => update('sw', e.target.value)}>
                <option value="">{t('All results')}</option>
                <option value="attackers">{t('Attackers win')}</option>
                <option value="defenders">{t('Defenders win')}</option>
                <option value="draw">{t('Draw')}</option>
              </select>
            </label>
          </div>
          <Health
            phase={account.phase}
            refresh={() => (sc ? update('sc', '') : void account.refresh())}
          />
          {account.phase === 'ready' && !account.data?.games.length && (
            <p>{t('No account games match these filters.')}</p>
          )}
          <ul className="personal-cards">
            {account.data?.games.map((game) => (
              <li key={game.id}>
                <div>
                  <strong>{t(game.ruleset)}</strong> · {t(statusNames[game.status])}
                </div>
                <p>
                  {game.players.attackers.name} · {game.players.defenders.name}
                </p>
                <p>
                  {game.winner
                    ? t(
                        game.winner === 'draw'
                          ? 'Draw'
                          : game.winner === 'attackers'
                            ? 'Attackers win'
                            : 'Defenders win',
                      )
                    : t('Unfinished record')}{' '}
                  · {game.plies} {t('moves')}
                </p>
                <time dateTime={game.created_at}>{date(game.created_at)}</time>
                <LocalLink href={`/dashboard/single-player/${encodeURIComponent(game.id)}`}>
                  {t('Review account game')}
                </LocalLink>
              </li>
            ))}
          </ul>
          <Pages
            data={account.data}
            shown={account.data?.games.length ?? 0}
            cursor={sc}
            change={(v) => update('sc', v)}
            disabled={account.phase !== 'ready'}
          />
        </section>
        <section className="panel personal-section" aria-labelledby="personal-online-heading">
          <h2 id="personal-online-heading">{t('Online multiplayer')}</h2>
          <p>
            {t('All your eligible online game results')} ·{' '}
            {t('Totals are independent of the page filters.')}
          </p>
          {online.data && (
            <dl className="personal-totals">
              {(
                [
                  ['played', 'Played'],
                  ['wins', 'Wins'],
                  ['losses', 'Losses'],
                  ['draws', 'Draws'],
                  ['abandoned_games', 'Timed-out games'],
                ] as const
              ).map(([key, label]) => (
                <div key={key}>
                  <dt>{t(label)}</dt>
                  <dd>{online.data!.totals[key]}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="subtle">
            {t(
              'Per-game eligible results only. Cancelled and voided games do not count; concession is not abandonment.',
            )}
          </p>
          {online.data && (
            <nav className="online-toolbar" aria-label={t('Terminal online records')}>
              {(['completed', 'cancelled', 'voided'] as const).map((status) => (
                <button
                  className="button secondary"
                  key={status}
                  aria-pressed={ov === 'history' && os === status}
                  onClick={() => {
                    const next = new URLSearchParams(location.search);
                    next.set('ov', 'history');
                    next.set('os', status);
                    next.delete('oc');
                    next.delete('oo');
                    navigate('/dashboard?' + next);
                  }}
                >
                  {t(statusNames[status])} ({online.data!.overview[status]})
                </button>
              ))}
            </nav>
          )}
          <div className="personal-filters">
            <label>
              {t('Online section')}
              <select
                value={ov}
                onChange={(e) => {
                  const next = new URLSearchParams(location.search);
                  next.set('ov', e.target.value);
                  for (const k of ['oc', 'os', 'oo']) next.delete(k);
                  navigate('/dashboard?' + next);
                }}
              >
                {sections.map(([key, label]) => (
                  <option key={key} value={key}>
                    {t(label)}
                    {online.data && key in online.data.overview
                      ? ` (${online.data.overview[key as keyof typeof online.data.overview]})`
                      : ''}
                  </option>
                ))}
              </select>
            </label>
            <RuleFilter value={or} change={(v) => update('or', v)} />
            <label>
              {t('Lifecycle')}
              <select value={os} onChange={(e) => update('os', e.target.value)}>
                <option value="">{t('All statuses')}</option>
                {(ov === 'history'
                  ? ['completed', 'cancelled', 'voided']
                  : ['waiting', 'active']
                ).map((status) => (
                  <option key={status} value={status}>
                    {t(statusNames[status])}
                  </option>
                ))}
              </select>
            </label>
            {ov === 'history' && (
              <label>
                {t('Your online outcome')}
                <select value={oo} onChange={(e) => update('oo', e.target.value)}>
                  <option value="">{t('All outcomes')}</option>
                  {Object.entries(outcomeNames).map(([key, label]) => (
                    <option key={key} value={key}>
                      {t(label)}
                    </option>
                  ))}
                  <option value="abandoned">{t('Timed out by you')}</option>
                </select>
              </label>
            )}
          </div>
          <Health
            phase={online.phase}
            refresh={() => (oc ? update('oc', '') : void online.refresh())}
          />
          {online.phase === 'ready' && !online.data?.games.length && (
            <p>{t('No online games match these filters.')}</p>
          )}
          <ul className="personal-cards">
            {online.data?.games.map((game) => {
              const terminal = ['completed', 'cancelled', 'voided'].includes(game.status);
              return (
                <li key={game.id}>
                  <div>
                    <strong>{t(game.ruleset)}</strong> · {t(statusNames[game.status])}
                    {game.your_turn ? ` · ${t('Awaiting your move')}` : ''}
                  </div>
                  <p>
                    {game.players.attackers ?? t('Unassigned')} ·{' '}
                    {game.players.defenders ?? t('Unassigned')}
                  </p>
                  <p>
                    {t(modeNames[game.match.mode])} · {t('Game {leg}', { leg: game.match.leg })}
                  </p>
                  {game.outcome && (
                    <p>
                      {t(outcomeNames[game.outcome])}
                      {game.abandoned ? ` · ${t('Timed out by you')}` : ''}
                    </p>
                  )}
                  {game.result && <p>{t(game.result.reason.replaceAll('_', ' '))}</p>}
                  {game.deadline && (
                    <p>
                      {t('Move deadline')}:{' '}
                      <time dateTime={game.deadline}>{date(game.deadline)}</time>
                      {game.overdue ? ` · ${t('Awaiting adjudication')}` : ''}
                    </p>
                  )}
                  <LocalLink href={`/online/games/${game.id}${terminal ? '/review' : ''}`}>
                    {t(terminal ? 'Review online game' : 'Open online game')}
                  </LocalLink>
                  {game.match.games.length > 1 && (
                    <nav aria-label={t('Match games')}>
                      {game.match.games.map((sibling) => (
                        <LocalLink
                          key={sibling.id}
                          href={`/online/games/${sibling.id}${['completed', 'cancelled', 'voided'].includes(sibling.status) ? '/review' : ''}`}
                        >
                          {t('Game {leg}', { leg: sibling.leg })} · {t(statusNames[sibling.status])}
                        </LocalLink>
                      ))}
                    </nav>
                  )}
                </li>
              );
            })}
          </ul>
          <Pages
            data={online.data}
            shown={online.data?.games.length ?? 0}
            cursor={oc}
            change={(v) => update('oc', v)}
            disabled={online.phase !== 'ready'}
          />
          <p>
            {t(
              'Deadlines and results come from the server. Opening a game does not grant permission to move after its deadline.',
            )}
          </p>
        </section>
      </div>
    </section>
  );
}
export default function PersonalDashboard() {
  const { account } = useAuth();
  const [path, setPath] = useState(location.pathname + location.search);
  useEffect(() => {
    const changed = () => setPath(location.pathname + location.search);
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  if (!account) return null;
  const [pathname, query = ''] = path.split('?');
  const match = /^\/dashboard\/single-player\/([A-Za-z0-9_-]{1,100})$/.exec(pathname);
  return match ? (
    <AccountReview key={account.id + ':' + match[1]} id={match[1]} accountId={account.id} />
  ) : (
    <DashboardHome key={account.id} accountId={account.id} query={query} />
  );
}
