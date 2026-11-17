import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import Board from './Board';
import DashboardCountdown from './DashboardCountdown';
import ThemeSelect from './ThemeSelect';
import { LanguageSelect, useI18n } from './i18n';
import { portraitAssetSrc } from './portraitAssets';
import { formatRemaining } from './remainingTime';
import {
  PublicOnlineError,
  publicGame,
  publicGames,
  publicReplay,
  publicRetryDelay,
  RETRY_TIMER_CHUNK_MS,
} from './publicOnlineApi';
import {
  publicBoardView,
  type PublicFilters,
  type PublicGame,
  type PublicGamePage,
  type PublicGameSummary,
  type PublicParticipant,
  type PublicReplay,
} from './publicOnlineTypes';
import './arenaTopBar.css';
import './publicArena.css';

const noop = () => {};
/**
 * Run `onDeadline` after `delay` while preserving the absolute deadline. A long
 * Retry-After is split into bounded chunks so it cannot overflow the platform
 * timer and fire immediately; `setTimer` stores the latest handle for cleanup.
 */
function scheduleDeadline(
  delay: number,
  onDeadline: () => void,
  setTimer: (timer: ReturnType<typeof setTimeout>) => void,
  canRun: () => boolean,
) {
  const deadline = Date.now() + delay;
  const tick = () => {
    if (!canRun()) return;
    const remaining = deadline - Date.now();
    if (remaining > RETRY_TIMER_CHUNK_MS) setTimer(setTimeout(tick, RETRY_TIMER_CHUNK_MS));
    else if (remaining > 0) setTimer(setTimeout(onDeadline, remaining));
    else onDeadline();
  };
  tick();
}
const rulesetNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
const statusLabels = {
  waiting: 'Waiting',
  active: 'Live',
  completed: 'Completed',
  cancelled: 'Cancelled',
  voided: 'Voided',
};
const GAME_ID = '[A-Za-z0-9_-]{1,128}';
const GAME_ROUTE = new RegExp(`^/watch/games/(${GAME_ID})(/replay)?$`);
export type PublicRoute =
  | { kind: 'discovery' }
  | { kind: 'game'; id: string; replay: boolean }
  | { kind: 'not-found' };
/** Only the three documented public shapes are valid; everything else is a 404. */
export function matchPublicRoute(pathname: string): PublicRoute {
  const clean = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (clean === '/watch') return { kind: 'discovery' };
  const match = clean.match(GAME_ROUTE);
  if (match) return { kind: 'game', id: match[1], replay: Boolean(match[2]) };
  return { kind: 'not-found' };
}
function PublicPlayer({ player }: { player: PublicParticipant | null }) {
  const { t } = useI18n();
  const id = player?.portrait_id;
  const src = id ? portraitAssetSrc(id) : null;
  return (
    <span className="public-player">
      {src && (
        <img
          // Remount when the resolved portrait changes so a previously failed
          // image does not stay hidden after its source updates.
          key={id}
          src={src}
          alt=""
          width={48}
          height={48}
          loading="lazy"
          onError={(event) => {
            event.currentTarget.hidden = true;
          }}
        />
      )}
      <span>
        <strong>{player?.display_name || t('Open seat')}</strong>
        {player?.current_rating != null && (
          <small>
            {t('Rating')}: {Math.round(player.current_rating)}
          </small>
        )}
      </span>
    </span>
  );
}
function PublicMatch({ game }: { game: PublicGameSummary }) {
  const { t } = useI18n();
  const winner =
    game.result?.winner === 'attackers' || game.result?.winner === 'defenders'
      ? game.result.winner
      : null;
  const replay = game.status === 'completed';
  return (
    <article className={`public-match arena-surface match-${game.status}`}>
      <div className="public-match-meta">
        <span className="public-match-ruleset">
          {t(rulesetNames[game.ruleset] ?? game.ruleset)}
        </span>
        <span className="badge public-status">{t(statusLabels[game.status])}</span>
      </div>
      <div className="public-match-sides">
        {(['attackers', 'defenders'] as const).map((side) => (
          <div
            key={side}
            className={`public-side public-side-${side}${winner === side ? ' is-winner' : ''}`}
          >
            <div className="public-side-heading">
              <span className="public-side-label">{t(sideLabel(side))}</span>
              {winner === side && <span className="public-winner-tag">{t('Winner')}</span>}
            </div>
            <PublicPlayer player={game.participants[side]} />
          </div>
        ))}
      </div>
      {game.result && (
        <p className="public-match-result">
          {game.result.winner === 'draw'
            ? t('Draw')
            : game.result.winner
              ? t('{side} won', { side: t(sideLabel(game.result.winner)) })
              : t('No official result')}
        </p>
      )}
      <a
        className={`button public-match-action${replay ? ' primary' : ''}`}
        href={`/watch/games/${encodeURIComponent(game.id)}${replay ? '/replay' : ''}`}
      >
        {t(replay ? 'Watch replay' : 'Watch game')}
      </a>
    </article>
  );
}
function errorLabel(error: unknown) {
  return error instanceof PublicOnlineError
    ? error.message
    : 'Public games could not be loaded. Please retry.';
}
function sideLabel(side: 'attackers' | 'defenders') {
  return side === 'attackers' ? 'Attackers' : 'Defenders';
}
/** Terminal status message for a game that is no longer polling. */
function finalStatusLabel(status: PublicGame['status']) {
  if (status === 'cancelled') return 'Waiting game cancelled.';
  if (status === 'voided') return 'Game voided.';
  return statusLabels[status];
}
/** Shared error/cooldown notice so both viewers expose identical retry behavior. */
function RetryNotice({
  error,
  cooldown,
  onRetry,
}: {
  error: string;
  cooldown: boolean;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const id = useId();
  return (
    <div role="alert" className="arena-surface public-notice">
      <p>{t(error)}</p>
      {cooldown && (
        <p id={id}>{t('Retry is temporarily unavailable. The viewer will retry automatically.')}</p>
      )}
      <button
        className="button secondary"
        aria-disabled={cooldown}
        aria-describedby={cooldown ? id : undefined}
        onClick={onRetry}
      >
        {t('Retry')}
      </button>
    </div>
  );
}
function Discovery() {
  const { t } = useI18n();
  const id = useId();
  const [filters, setFilters] = useState<PublicFilters>({
    ruleset: '',
    status: '',
    outcome: '',
    player: '',
  });
  const [applied, setApplied] = useState(filters);
  const [cursor, setCursor] = useState<string | null>(null);
  const [previous, setPrevious] = useState<(string | null)[]>([]);
  const [page, setPage] = useState<PublicGamePage | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const cooldown = useRef(0);
  const revision = useRef(0);
  const retry = () => {
    if (Date.now() < cooldown.current) return;
    setAttempt((v) => v + 1);
  };
  useEffect(() => {
    let alive = true;
    const generation = ++revision.current;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    let stopped = false;
    let failures = 0;
    let epoch = 0;
    const schedule = (delay: number) => {
      clearTimeout(timer);
      scheduleDeadline(
        delay,
        () => void load(),
        (next) => {
          timer = next;
        },
        () => alive && !stopped && !document.hidden,
      );
    };
    const load = async () => {
      if (!alive || running || stopped || document.hidden) return;
      // A server Retry-After cooldown blocks automatic and manual retries alike:
      // reschedule the remaining wait instead of issuing a request.
      const wait = cooldown.current - Date.now();
      if (wait > 0) {
        schedule(wait);
        return;
      }
      // The cooldown has elapsed: clear the notice before requesting so Retry is
      // advertised as available again, even if this request fails later.
      if (cooldown.current !== 0) {
        cooldown.current = 0;
        setCooldownUntil(0);
      }
      running = true;
      const sequence = ++epoch;
      const requestController = new AbortController();
      controller = requestController;
      try {
        const value = await publicGames(applied, cursor, requestController.signal);
        if (alive && sequence === epoch && revision.current === generation) {
          failures = 0;
          cooldown.current = 0;
          setCooldownUntil(0);
          setPage(value);
          setError('');
          setLoading(false);
          schedule(15000);
        }
      } catch (e) {
        if (alive && sequence === epoch && !requestController.signal.aborted) {
          const delay = publicRetryDelay(e, failures, 15000);
          failures += 1;
          setError(errorLabel(e));
          setLoading(false);
          if (delay === null) {
            stopped = true;
          } else {
            if (e instanceof PublicOnlineError && e.failure === 'busy') {
              cooldown.current = Date.now() + delay;
              setCooldownUntil(cooldown.current);
            } else {
              cooldown.current = 0;
              setCooldownUntil(0);
            }
            schedule(delay);
          }
        }
      } finally {
        if (sequence === epoch) running = false;
      }
    };
    setPage(null);
    setError('');
    setLoading(true);
    const visible = () => {
      if (document.hidden) {
        clearTimeout(timer);
        epoch++;
        controller?.abort();
        running = false;
      } else if (!stopped) {
        clearTimeout(timer);
        const remaining = cooldown.current - Date.now();
        if (remaining > 0) schedule(remaining);
        else void load();
      }
    };
    document.addEventListener('visibilitychange', visible);
    void load();
    return () => {
      alive = false;
      controller?.abort();
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [applied, cursor, attempt]);
  return (
    <>
      <section className="arena-surface public-intro">
        <p className="arena-kicker">{t('THE WATCH HALL')}</p>
        <h1>{t('Every table tells a saga.')}</h1>
        <p>{t('Watch live online games and explore completed replays. No account needed.')}</p>
      </section>
      <form
        className="public-filters arena-surface"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ ...filters, player: filters.player.trim() });
          setCursor(null);
          setPrevious([]);
        }}
      >
        <label htmlFor={`${id}-ruleset`}>
          {t('Ruleset')}
          <select
            id={`${id}-ruleset`}
            value={filters.ruleset}
            onChange={(e) => setFilters({ ...filters, ruleset: e.target.value })}
          >
            <option value="">{t('All rulesets')}</option>
            {['brandubh', 'tablut', 'copenhagen'].map((v) => (
              <option key={v} value={v}>
                {t(rulesetNames[v])}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-status`}>
          {t('Status')}
          <select
            id={`${id}-status`}
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <option value="">{t('All statuses')}</option>
            {Object.entries(statusLabels).map(([v, label]) => (
              <option key={v} value={v}>
                {t(label)}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-result`}>
          {t('Result')}
          <select
            id={`${id}-result`}
            value={filters.outcome}
            onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}
          >
            <option value="">{t('All results')}</option>
            <option value="attackers">{t('Attackers win')}</option>
            <option value="defenders">{t('Defenders win')}</option>
            <option value="draw">{t('Draw')}</option>
          </select>
        </label>
        <label htmlFor={`${id}-player`}>
          {t('Public player')}
          <input
            id={`${id}-player`}
            value={filters.player}
            maxLength={148}
            onChange={(e) => setFilters({ ...filters, player: e.target.value })}
          />
        </label>
        <button className="button primary" type="submit">
          {t('Apply filters')}
        </button>
      </form>
      {loading && <p role="status">{t('Loading public games…')}</p>}
      {error && <RetryNotice error={error} cooldown={cooldownUntil > 0} onRetry={retry} />}
      {page && (
        <section aria-label={t('Public games')} className="public-games">
          {page.items.length ? (
            page.items.map((game) => <PublicMatch key={game.id} game={game} />)
          ) : (
            <div className="arena-surface public-empty">
              <h2>{t('The tables are quiet.')}</h2>
              <p>
                {t(
                  'No public games match these filters. Try another ruleset or return when the next saga begins.',
                )}
              </p>
              <a href="/register">{t('Create an account to play')}</a>
            </div>
          )}
        </section>
      )}
      {page && (previous.length > 0 || page.next_cursor) && (
        <nav aria-label={t('Public game pages')} className="public-pagination">
          <button
            className="button secondary"
            disabled={!previous.length}
            onClick={() => {
              setCursor(previous[previous.length - 1]);
              setPrevious(previous.slice(0, -1));
            }}
          >
            {t('Previous page')}
          </button>
          <button
            className="button secondary"
            disabled={!page.next_cursor}
            onClick={() => {
              setPrevious([...previous, cursor]);
              setCursor(page.next_cursor);
            }}
          >
            {t('Next page')}
          </button>
        </nav>
      )}
    </>
  );
}
function GameView({ id, replayRequested }: { id: string; replayRequested: boolean }) {
  const { t, locale } = useI18n();
  const [game, setGame] = useState<PublicGame | null>(null);
  const [replay, setReplay] = useState<PublicReplay | null>(null);
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const cooldown = useRef(0);
  const retry = () => {
    if (Date.now() < cooldown.current) return;
    setAttempt((v) => v + 1);
  };
  useEffect(() => {
    let alive = true;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    let stopped = false;
    let failures = 0;
    let epoch = 0;
    const schedule = (delay: number) => {
      clearTimeout(timer);
      scheduleDeadline(
        delay,
        () => void load(),
        (next) => {
          timer = next;
        },
        () => alive && !stopped && !document.hidden,
      );
    };
    const load = async () => {
      if (!alive || running || stopped || document.hidden) return;
      // A server Retry-After cooldown blocks automatic and manual retries alike:
      // reschedule the remaining wait instead of issuing a request.
      const wait = cooldown.current - Date.now();
      if (wait > 0) {
        schedule(wait);
        return;
      }
      // The cooldown has elapsed: clear the notice before requesting so Retry is
      // advertised as available again, even if this request fails later.
      if (cooldown.current !== 0) {
        cooldown.current = 0;
        setCooldownUntil(0);
      }
      running = true;
      const sequence = ++epoch;
      const requestController = new AbortController();
      controller = requestController;
      try {
        const current = replayRequested ? null : await publicGame(id, requestController.signal);
        if (replayRequested || current?.status === 'completed') {
          const record = await publicReplay(id, requestController.signal);
          if (!record.validated || !record.states.length) throw new PublicOnlineError(409);
          if (alive && sequence === epoch) {
            cooldown.current = 0;
            setCooldownUntil(0);
            setGame(record.game);
            setReplay(record);
            // An explicit replay link starts at the first position; a live game
            // that just completed keeps showing its final position.
            setCursor(replayRequested ? 0 : record.states.length - 1);
            stopped = true;
            setError('');
          }
        } else if (alive && sequence === epoch && current) {
          failures = 0;
          cooldown.current = 0;
          setCooldownUntil(0);
          setGame(current);
          setError('');
          if (current.status === 'cancelled' || current.status === 'voided') stopped = true;
          else schedule(current.status === 'waiting' ? 15000 : 5000);
        }
      } catch (e) {
        if (alive && sequence === epoch && !requestController.signal.aborted) {
          const delay = publicRetryDelay(e, failures);
          failures += 1;
          setError(errorLabel(e));
          if (delay === null) {
            stopped = true;
          } else {
            if (e instanceof PublicOnlineError && e.failure === 'busy') {
              cooldown.current = Date.now() + delay;
              setCooldownUntil(cooldown.current);
            } else {
              cooldown.current = 0;
              setCooldownUntil(0);
            }
            schedule(delay);
          }
        }
      } finally {
        if (sequence === epoch) running = false;
      }
    };
    setGame(null);
    setReplay(null);
    setError('');
    const visible = () => {
      if (document.hidden) {
        clearTimeout(timer);
        epoch++;
        controller?.abort();
        running = false;
      } else if (!stopped) {
        clearTimeout(timer);
        const remaining = cooldown.current - Date.now();
        if (remaining > 0) schedule(remaining);
        else void load();
      }
    };
    document.addEventListener('visibilitychange', visible);
    void load();
    return () => {
      alive = false;
      controller?.abort();
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [id, replayRequested, attempt]);
  const last = replay ? replay.states.length - 1 : 0;
  const moveCursor = (value: number) => setCursor(Math.max(0, Math.min(last, value)));
  const keyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (!replay || (event.target as HTMLElement).closest('input, select, textarea, [role="grid"]'))
      return;
    const next = { ArrowLeft: cursor - 1, ArrowRight: cursor + 1, Home: 0, End: last }[event.key];
    if (next !== undefined) {
      event.preventDefault();
      // An endpoint shortcut can disable the focused step button. Keep focus on
      // its navigation group so the next shortcut still reaches this viewer.
      (event.target as HTMLElement).closest<HTMLElement>('.public-replay-controls')?.focus();
      moveCursor(next);
    }
  };
  return (
    <>
      <a href="/watch">← {t('All public games')}</a>
      {error && <RetryNotice error={error} cooldown={cooldownUntil > 0} onRetry={retry} />}
      {!game && !error && <p role="status">{t('Loading public game…')}</p>}
      {game && (
        <section
          className="public-view arena-surface"
          onKeyDown={keyboard}
          aria-label={t('Public game viewer')}
        >
          <div className="public-view-heading">
            <div>
              <p className="arena-kicker">{t(replay ? 'REPLAY' : 'WATCHING')}</p>
              <h1>
                {t(rulesetNames[game.ruleset] ?? game.ruleset)} · {t(statusLabels[game.status])}
              </h1>
            </div>
            <span className="public-readonly">{t('Read-only spectator')}</span>
          </div>
          <div className="public-players">
            <div>
              <p>{t('Attackers')}</p>
              <PublicPlayer player={game.participants.attackers} />
            </div>
            <span aria-hidden="true">VS</span>
            <div>
              <p>{t('Defenders')}</p>
              <PublicPlayer player={game.participants.defenders} />
            </div>
          </div>
          {!replay && game.status === 'active' && (
            <p className="public-live-note">
              {t('{side} to move', { side: t(sideLabel(game.position.turn)) })}
            </p>
          )}
          <p className="public-live-note">
            {t('Time control')}: {t(game.time_control === 'total' ? 'Total time' : 'Per move')} ·{' '}
            {formatRemaining(game.time_per_move)}
            {game.increment_seconds > 0 && <> + {formatRemaining(game.increment_seconds)}</>}
          </p>
          {game.result && (
            <p className="public-live-note">
              {game.result.winner === 'draw'
                ? t('Draw')
                : game.result.winner
                  ? t('{side} won', { side: t(sideLabel(game.result.winner)) })
                  : t('No official result')}
            </p>
          )}
          <div className="public-board">
            <Board
              game={publicBoardView(game, replay, cursor)}
              readOnly
              selected={null}
              onSelect={noop}
            />
          </div>
          {replay ? (
            <div
              className="public-replay-controls"
              role="group"
              tabIndex={0}
              aria-keyshortcuts="ArrowLeft ArrowRight Home End"
              aria-label={t('Replay controls')}
            >
              <button
                className="button secondary"
                disabled={cursor === 0}
                onClick={() => moveCursor(0)}
              >
                {t('First move')}
              </button>
              <button
                className="button secondary"
                disabled={cursor === 0}
                onClick={() => moveCursor(cursor - 1)}
              >
                {t('Previous move')}
              </button>
              <output aria-live="polite">
                {t('Move {current} of {total}', { current: cursor, total: last })}
              </output>
              <button
                className="button secondary"
                disabled={cursor === last}
                onClick={() => moveCursor(cursor + 1)}
              >
                {t('Next move')}
              </button>
              <button
                className="button secondary"
                disabled={cursor === last}
                onClick={() => moveCursor(last)}
              >
                {t('Last move')}
              </button>
            </div>
          ) : game.status === 'waiting' || game.status === 'active' ? (
            <p className="public-live-note">
              {t('Live positions refresh automatically while this page is visible.')}
              {game.status === 'active' && game.deadline && (
                <span>
                  {' '}
                  {t('Time remaining')}:{' '}
                  <DashboardCountdown key={game.id} deadline={game.deadline} />
                </span>
              )}
              {game.deadline && (
                <>
                  {' '}
                  {t('Move deadline')}:{' '}
                  <time dateTime={game.deadline}>
                    {new Date(game.deadline).toLocaleTimeString(locale)}
                  </time>
                </>
              )}
            </p>
          ) : (
            <p className="public-live-note">{t(finalStatusLabel(game.status))}</p>
          )}
          {replay && (
            <p className="public-live-note">
              {t(
                'Use Left/Right arrows or Home/End in the replay controls. Board arrows navigate squares.',
              )}
            </p>
          )}
          <p>{t('Watching never provides move advice or changes the game.')}</p>
        </section>
      )}
    </>
  );
}
function PublicNotFound() {
  const { t } = useI18n();
  return (
    <section className="arena-surface public-empty">
      <h1>{t('Public page not found.')}</h1>
      <p>
        {t(
          'This public link does not exist. Return to the Watch Hall to browse live games and replays.',
        )}
      </p>
      <a className="button" href="/watch">
        {t('Back to public games')}
      </a>
    </section>
  );
}
/** Mounted by the entry router before Auth; intentionally no account hooks. */
export default function PublicArena() {
  const { t } = useI18n();
  const route = matchPublicRoute(location.pathname);
  return (
    <div className="public-arena">
      <a className="skip-link" href="#public-content">
        {t('Skip to content')}
      </a>
      <header className="public-header arena-surface">
        <a href="/watch" className="public-brand">
          HNEFATAFL <small>{t('THE KING’S TABLE')}</small>
        </a>
        <nav className="public-header-nav" aria-label={t('Public navigation')}>
          <div className="public-header-preferences">
            <ThemeSelect />
            <LanguageSelect />
          </div>
          <div className="public-header-actions">
            <a className="public-header-login" href="/login">
              {t('Log in')}
            </a>
            <a className="public-header-register" href="/register">
              {t('Create account')}
            </a>
            <a className="button primary public-header-play" href="/play">
              {t('Play')}
            </a>
          </div>
        </nav>
      </header>
      <main id="public-content" tabIndex={-1}>
        {route.kind === 'game' ? (
          <GameView id={route.id} replayRequested={route.replay} />
        ) : route.kind === 'discovery' ? (
          <Discovery />
        ) : (
          <PublicNotFound />
        )}
      </main>
      <footer>
        <p>{t('Three battlefields. Real players. New sagas.')}</p>
        <a href="/register">{t('Join the table')}</a>
      </footer>
    </div>
  );
}
