import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import Board from './Board';
import DashboardCountdown from './DashboardCountdown';
import ThemeSelect from './ThemeSelect';
import { useI18n } from './i18n';
import { PublicOnlineError, publicGame, publicGames, publicReplay } from './publicOnlineApi';
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

const portraits = new Set([
  'shieldbearer',
  'shieldmaiden',
  'iron-helm',
  'wanderer',
  'skald',
  'fjord-scout',
  'raven-seer',
  'berserker',
  'one-eyed-jarl',
  'valkyrie',
  'einherjar',
  'forge-keeper',
]);
const noop = () => {};
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
function PublicPlayer({ player }: { player: PublicParticipant | null }) {
  const { t } = useI18n();
  const id = player?.portrait_id;
  return (
    <span className="public-player">
      {id && portraits.has(id) && (
        <img
          src={`/assets/arena-v2/assets/portraits/${id}.webp`}
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
            {t('Rating')}: {player.current_rating}
          </small>
        )}
      </span>
    </span>
  );
}
function PublicMatch({ game }: { game: PublicGameSummary }) {
  const { t } = useI18n();
  return (
    <article className="public-match arena-surface">
      <div className="public-match-meta">
        <span>{t(rulesetNames[game.ruleset] ?? game.ruleset)}</span>
        <span>{t(statusLabels[game.status])}</span>
      </div>
      <PublicPlayer player={game.participants.attackers} />
      <PublicPlayer player={game.participants.defenders} />
      {game.result && (
        <p>
          {game.result.winner === 'draw'
            ? t('Draw')
            : game.result.winner
              ? t('{side} won', {
                  side: t(game.result.winner === 'attackers' ? 'Attackers' : 'Defenders'),
                })
              : t('No official result')}
        </p>
      )}
      <a
        className="button"
        href={`/watch/games/${encodeURIComponent(game.id)}${game.status === 'completed' ? '/replay' : ''}`}
      >
        {t(game.status === 'completed' ? 'Watch replay' : 'Watch game')}
      </a>
    </article>
  );
}
function errorLabel(error: unknown) {
  return error instanceof PublicOnlineError
    ? error.message
    : 'Public games could not be loaded. Please retry.';
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
  const revision = useRef(0);
  useEffect(() => {
    let alive = true;
    const generation = ++revision.current;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    let epoch = 0;
    const load = async () => {
      if (!alive || running || document.hidden) return;
      running = true;
      const sequence = ++epoch;
      const requestController = new AbortController();
      controller = requestController;
      let delay = 15000;
      try {
        const value = await publicGames(applied, cursor, requestController.signal);
        if (alive && sequence === epoch && revision.current === generation) {
          setPage(value);
          setError('');
        }
      } catch (e) {
        if (alive && sequence === epoch && !requestController.signal.aborted) {
          setError(errorLabel(e));
          delay = Math.max(30000, e instanceof PublicOnlineError ? e.retryAfterMs : 0);
        }
      } finally {
        if (sequence !== epoch) return;
        running = false;
        if (alive) {
          setLoading(false);
          timer = setTimeout(() => void load(), delay);
        }
      }
    };
    setPage(null);
    setError('');
    setLoading(true);
    const visible = () => {
      if (timer) clearTimeout(timer);
      if (document.hidden) {
        epoch++;
        controller?.abort();
        running = false;
      } else void load();
    };
    document.addEventListener('visibilitychange', visible);
    void load();
    return () => {
      alive = false;
      controller?.abort();
      if (timer) clearTimeout(timer);
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
      {error && (
        <div role="alert" className="arena-surface public-notice">
          <p>{t(error)}</p>
          <button className="button secondary" onClick={() => setAttempt((v) => v + 1)}>
            {t('Retry')}
          </button>
        </div>
      )}
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
  useEffect(() => {
    let alive = true;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    let epoch = 0;
    let finished = false;
    const load = async () => {
      if (!alive || running || finished || document.hidden) return;
      running = true;
      const sequence = ++epoch;
      const requestController = new AbortController();
      controller = requestController;
      let delay = 5000;
      try {
        const current = replayRequested ? null : await publicGame(id, requestController.signal);
        if (replayRequested || current?.status === 'completed') {
          const record = await publicReplay(id, requestController.signal);
          if (!record.validated || !record.states.length) throw new Error('Invalid replay');
          if (alive && sequence === epoch) {
            setGame(record.game);
            setReplay(record);
            setCursor(0);
            finished = true;
            setError('');
          }
        } else if (alive && sequence === epoch && current) {
          setGame(current);
          setError('');
          if (current.status === 'cancelled' || current.status === 'voided') finished = true;
          if (current.status === 'waiting') delay = 15000;
        }
      } catch (e) {
        if (alive && sequence === epoch && !requestController.signal.aborted) {
          setError(errorLabel(e));
          delay = Math.max(30000, e instanceof PublicOnlineError ? e.retryAfterMs : 0);
        }
      } finally {
        if (sequence !== epoch) return;
        running = false;
        if (alive && !finished) timer = setTimeout(() => void load(), delay);
      }
    };
    setGame(null);
    setReplay(null);
    setError('');
    const visible = () => {
      if (timer) clearTimeout(timer);
      if (document.hidden) {
        epoch++;
        controller?.abort();
        running = false;
      } else void load();
    };
    document.addEventListener('visibilitychange', visible);
    void load();
    return () => {
      alive = false;
      controller?.abort();
      if (timer) clearTimeout(timer);
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
      {error && (
        <div role="alert" className="arena-surface public-notice">
          <p>{t(error)}</p>
          <button className="button secondary" onClick={() => setAttempt((v) => v + 1)}>
            {t('Retry')}
          </button>
        </div>
      )}
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
          <p className="public-live-note">
            {t('Time control')}: {t(game.time_control === 'total' ? 'Total time' : 'Per move')} ·{' '}
            {t('{seconds} seconds', { seconds: game.time_per_move })}
            {game.increment_seconds > 0 && (
              <> + {t('{seconds} seconds', { seconds: game.increment_seconds })}</>
            )}
          </p>
          {game.result && (
            <p className="public-live-note">
              {game.result.winner === 'draw'
                ? t('Draw')
                : game.result.winner
                  ? t('{side} won', {
                      side: t(game.result.winner === 'attackers' ? 'Attackers' : 'Defenders'),
                    })
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
          ) : (
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
/** Mounted by the entry router before Auth; intentionally no account hooks. */
export default function PublicArena() {
  const { t, language, setLanguage } = useI18n();
  const match = location.pathname.match(/^\/watch\/games\/([A-Za-z0-9_-]{1,128})(\/replay)?\/?$/);
  return (
    <div className="public-arena">
      <a className="skip-link" href="#public-content">
        {t('Skip to content')}
      </a>
      <header className="public-header arena-surface">
        <a href="/watch" className="public-brand">
          HNEFATAFL <small>{t('THE KING’S TABLE')}</small>
        </a>
        <nav aria-label={t('Public navigation')}>
          <ThemeSelect />
          <label>
            {t('Language')}
            <select value={language} onChange={(e) => setLanguage(e.target.value as 'en' | 'de')}>
              <option value="en">English</option>
              <option value="de">Deutsch</option>
            </select>
          </label>
          <a href="/login">{t('Log in')}</a>
          <a className="button" href="/register">
            {t('Create account')}
          </a>
        </nav>
      </header>
      <main id="public-content" tabIndex={-1}>
        {match ? <GameView id={match[1]} replayRequested={!!match[2]} /> : <Discovery />}
      </main>
      <footer>
        <p>{t('Three battlefields. Real players. New sagas.')}</p>
        <a href="/login">{t('Join the table')}</a>
      </footer>
    </div>
  );
}
