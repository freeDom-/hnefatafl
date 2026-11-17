import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
} from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { useI18n, type Translate } from './i18n';
import { navigate } from './navigation';
import { notify } from './notifications';
import { useOnlinePoll } from './useOnlinePoll';
import Modal from './Modal';
import Portrait from './Portrait';
import LocalDateTimePicker from './LocalDateTimePicker';
import {
  ClockFields,
  MatchModeField,
  defaultClockInput,
  clockRequest,
  matchModeNames,
} from './OnlineGameOptions';
import type { MatchMode } from './onlineTypes';
import type {
  PlayerTournamentBracket,
  PlayerTournamentDetail,
  PlayerTournamentPage,
  PlayerTournamentStatus,
  TournamentMatch,
  TournamentPlayer,
  TournamentRound,
} from './playerTournamentTypes';
import './playerTournaments.css';

const statusLabels: Record<PlayerTournamentStatus, string> = {
  registration: 'Registration',
  running: 'Running',
  complete: 'Completed',
  cancelled: 'Cancelled',
};
const bracketLabels = {
  upper: 'Upper bracket',
  lower: 'Lower bracket',
  grand_final: 'Grand final',
  reset: 'Bracket reset',
};
const matchLabels: Record<TournamentMatch['status'], string> = {
  pending: 'To be decided',
  ready: 'Ready to play',
  complete: 'Completed',
  bye: 'Bye',
  disputed: 'Under review',
};
const decisionLabels = {
  play: 'Decided by play',
  higher_seed: 'Higher seed advances after tied legs',
  undefeated_advantage: 'Undefeated finalist advances after tied legs',
  bye: 'Advances with a bye',
  admin_override: 'Administrator decision',
};
const emptyPage: PlayerTournamentPage = { items: [], next_cursor: null, limit: 20 };
type DetailData = { tournament: PlayerTournamentDetail; bracket: PlayerTournamentBracket };
type Action = 'create' | 'register' | 'withdraw' | 'start' | 'cancel' | 'rebuild' | 'override';
type Dialog = { kind: Action; version?: number; match?: TournamentMatch };
const actionLabels: Record<Action, string> = {
  create: 'Create player tournament',
  register: 'Register for tournament',
  withdraw: 'Withdraw registration',
  start: 'Start tournament',
  cancel: 'Cancel tournament',
  rebuild: 'Rebuild bracket',
  override: 'Override match result',
};
function routeId() {
  const match = location.pathname.match(/^\/player-tournaments\/([^/]+)\/?$/);
  try {
    return match ? decodeURIComponent(match[1]) : '';
  } catch {
    return '';
  }
}
function link(event: MouseEvent<HTMLAnchorElement>) {
  if (event.button || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  event.preventDefault();
  navigate(event.currentTarget.getAttribute('href')!);
}
function playerName(player: TournamentPlayer | null, t: Translate) {
  return player?.name || player?.username || t('Player unavailable');
}
function allMatches(bracket: PlayerTournamentBracket): TournamentMatch[] {
  return [
    ...bracket.upper.flatMap((round) => round.matches),
    ...bracket.lower.flatMap((round) => round.matches),
    ...(bracket.final ? [bracket.final] : []),
    ...(bracket.reset ? [bracket.reset] : []),
  ];
}
function matchName(match: TournamentMatch, t: Translate) {
  if (match.bracket === 'grand_final' || match.bracket === 'reset')
    return t(bracketLabels[match.bracket]);
  return t('{bracket} · Round {round} · Match {match}', {
    bracket: t(bracketLabels[match.bracket]),
    round: match.round,
    match: match.slot + 1,
  });
}
function MatchCard({
  match,
  matches,
  canOverride,
  onOverride,
}: {
  match: TournamentMatch;
  matches: Map<string, TournamentMatch>;
  canOverride: boolean;
  onOverride: (match: TournamentMatch) => void;
}) {
  const { t } = useI18n();
  const unresolved = (source: string) => {
    const split = source.indexOf(':');
    const kind = source.slice(0, split),
      value = source.slice(split + 1);
    const previous = matches.get(value);
    if (kind === 'seed') return t('Seed {seed}', { seed: value });
    if (previous)
      return t(kind === 'winner' ? 'Winner of {match}' : 'Loser of {match}', {
        match: matchName(previous, t),
      });
    return t('To be decided');
  };
  return (
    <article
      className={`arena-surface player-match player-match-${match.status}`}
      data-match-id={match.id}
      aria-label={matchName(match, t)}
    >
      <header>
        <h4>{matchName(match, t)}</h4>
        <span className="player-match-status">{t(matchLabels[match.status])}</span>
      </header>
      <ul className="player-match-seats">
        {(['a', 'b'] as const).map((seat) => {
          const player = match.participants[seat];
          const winner = player && match.winner?.account_id === player.account_id;
          return (
            <li key={seat} className={winner ? 'player-match-winner' : ''}>
              {player ? (
                <>
                  <Portrait id={player.portrait_id} />
                  <span>
                    {playerName(player, t)}
                    {winner && <strong className="player-winner-label">{t('Winner')}</strong>}
                  </span>
                </>
              ) : (
                <span>
                  {match.status === 'bye' ? t('Bye') : unresolved(match[`source_${seat}`])}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {match.decided_by && (
        <p className="player-match-decision">{t(decisionLabels[match.decided_by])}</p>
      )}
      {match.legs.length > 0 && (
        <ol className="player-match-legs" aria-label={t('Match games')}>
          {match.legs.map((leg) => (
            <li key={leg.leg}>
              <a href={`/online/games/${encodeURIComponent(leg.game_id)}`} onClick={link}>
                {t('Game {leg}', { leg: leg.leg })}
              </a>
              <span>
                {leg.outcome === 'a' || leg.outcome === 'b'
                  ? t('Won by {player}', { player: playerName(match.participants[leg.outcome], t) })
                  : leg.outcome === 'draw'
                    ? t('Draw')
                    : leg.outcome === 'void'
                      ? t('Voided')
                      : t(
                          leg.game_status === 'active'
                            ? 'In progress'
                            : leg.game_status === 'completed'
                              ? 'Awaiting settlement'
                              : leg.game_status === 'voided'
                                ? 'Voided'
                                : leg.game_status === 'cancelled'
                                  ? 'Cancelled'
                                  : 'Not started',
                        )}
              </span>
            </li>
          ))}
        </ol>
      )}
      {!!match.pending_games && <p>{t('Next game available after the previous game finishes.')}</p>}
      {canOverride && match.status === 'ready' && (
        <button className="button secondary" type="button" onClick={() => onOverride(match)}>
          {t('Override match result')}
        </button>
      )}
    </article>
  );
}

/** Draw only edges explicitly supplied by the server; never derive a winner. */
function BracketGraph({
  bracket,
  canOverride,
  onOverride,
}: {
  bracket: PlayerTournamentBracket;
  canOverride: boolean;
  onOverride: (match: TournamentMatch) => void;
}) {
  const { t } = useI18n();
  const canvas = useRef<HTMLDivElement>(null);
  const matches = useMemo(() => allMatches(bracket), [bracket]);
  const byId = useMemo(() => new Map(matches.map((match) => [match.id, match])), [matches]);
  const [paths, setPaths] = useState<{ key: string; path: string; loser: boolean }[]>([]);
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = canvas.current;
    if (!node) return;
    const measure = () => {
      const origin = node.getBoundingClientRect();
      const cards = new Map(
        Array.from(node.querySelectorAll<HTMLElement>('[data-match-id]')).map((card) => [
          card.dataset.matchId!,
          card.getBoundingClientRect(),
        ]),
      );
      const next: typeof paths = [];
      for (const match of matches)
        for (const seat of ['a', 'b'] as const) {
          const source = match[`source_${seat}`];
          const split = source.indexOf(':');
          const kind = source.slice(0, split);
          if (kind !== 'winner' && kind !== 'loser') continue;
          const from = cards.get(source.slice(split + 1)),
            to = cards.get(match.id);
          if (!from || !to) continue;
          const x1 = from.right - origin.left,
            y1 = from.top + from.height / 2 - origin.top;
          const x2 = to.left - origin.left,
            y2 = to.top + to.height * (seat === 'a' ? 0.35 : 0.65) - origin.top;
          const middle = (x1 + x2) / 2;
          next.push({
            key: `${match.id}-${seat}`,
            path: `M ${x1} ${y1} H ${middle} V ${y2} H ${x2}`,
            loser: kind === 'loser',
          });
        }
      setPaths((previous) => (JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
      setBounds((previous) =>
        previous.width === node.scrollWidth && previous.height === node.scrollHeight
          ? previous
          : { width: node.scrollWidth, height: node.scrollHeight },
      );
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    node.querySelectorAll('[data-match-id]').forEach((card) => observer?.observe(card));
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [matches]);
  const rounds = (kind: 'upper' | 'lower', entries: TournamentRound[]) => (
    <section
      className={`player-bracket-track player-bracket-${kind}`}
      aria-label={t(bracketLabels[kind])}
    >
      <h3>{t(bracketLabels[kind])}</h3>
      <div className="player-bracket-rounds">
        {entries.map((round) => (
          <section
            className="player-bracket-round"
            key={round.round}
            aria-label={t('Round {round}', { round: round.round })}
          >
            <h4>{t('Round {round}', { round: round.round })}</h4>
            <div className="player-bracket-round-matches">
              {round.matches.map((match) => (
                <MatchCard
                  key={match.id}
                  match={match}
                  matches={byId}
                  canOverride={canOverride}
                  onOverride={onOverride}
                />
              ))}
            </div>
          </section>
        ))}
        {!entries.length && <p>{t('No lower-bracket rounds are needed for this bracket size.')}</p>}
      </div>
    </section>
  );
  return (
    <>
      <p className="player-bracket-help">
        {t(
          'Scroll horizontally to explore the bracket. Solid paths follow winners; dashed paths follow lower-bracket entrants.',
        )}
      </p>
      <div
        className="player-bracket-scroll"
        tabIndex={0}
        role="region"
        aria-label={t('Double-elimination bracket')}
      >
        <div className="player-bracket-canvas" ref={canvas}>
          <svg
            className="player-bracket-paths"
            width={bounds.width}
            height={bounds.height}
            aria-hidden="true"
          >
            {paths.map((path) => (
              <path
                key={path.key}
                d={path.path}
                className={path.loser ? 'player-bracket-loser-path' : undefined}
              />
            ))}
          </svg>
          {rounds('upper', bracket.upper)}
          {rounds('lower', bracket.lower)}
          <section className="player-bracket-finals" aria-label={t('Finals')}>
            {[bracket.final, bracket.reset]
              .filter((match): match is TournamentMatch => match !== null)
              .map((match) => (
                <section key={match.id}>
                  <h3>{t(bracketLabels[match.bracket])}</h3>
                  <MatchCard
                    match={match}
                    matches={byId}
                    canOverride={canOverride}
                    onOverride={onOverride}
                  />
                </section>
              ))}
            {!bracket.reset && (
              <p className="player-reset-explanation">
                {t('A reset is played only if the lower-bracket finalist wins the grand final.')}
              </p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

export default function PlayerTournaments({ tournamentId }: { tournamentId?: string } = {}) {
  const { account } = useAuth();
  const { t, locale } = useI18n();
  const [fromRoute, setFromRoute] = useState(routeId);
  const selectedId = tournamentId ?? fromRoute;
  const owner = account?.id ?? '';
  const canAdmin = account?.permissions.includes('tournaments') ?? false;
  const [status, setStatus] = useState('');
  const [ruleset, setRuleset] = useState('');
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const [mode, setMode] = useState<'graph' | 'list'>('graph');
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [clockInput, setClockInput] = useState(defaultClockInput);
  const [matchMode, setMatchMode] = useState<MatchMode>('single');
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const current = useRef({ owner, selectedId, canAdmin });
  current.current = { owner, selectedId, canAdmin };
  const loadList = useCallback(
    async (signal: AbortSignal) => {
      if (selectedId || !owner) return emptyPage;
      const query = new URLSearchParams({ limit: '20' });
      if (status === 'upcoming') query.set('phase', 'upcoming');
      else if (status) query.set('status', status);
      if (ruleset) query.set('ruleset', ruleset);
      if (cursor) query.set('cursor', cursor);
      return api<PlayerTournamentPage>(
        `/player-tournaments?${query}`,
        undefined,
        undefined,
        signal,
      );
    },
    [owner, selectedId, status, ruleset, cursor],
  );
  const loadDetail = useCallback(
    async (signal: AbortSignal): Promise<DetailData | null> => {
      if (!selectedId || !owner) return null;
      const path = `/player-tournaments/${encodeURIComponent(selectedId)}`;
      const [tournament, bracket] = await Promise.all([
        api<PlayerTournamentDetail>(path, undefined, undefined, signal),
        api<PlayerTournamentBracket>(`${path}/bracket`, undefined, undefined, signal),
      ]);
      if (bracket.version !== undefined && bracket.version !== tournament.version)
        throw new Error('The tournament changed while loading. Refresh to see its latest bracket.');
      return { tournament, bracket };
    },
    [owner, selectedId],
  );
  const listing = useOnlinePoll(
    `player-tournaments:${owner}:${selectedId}:${status}:${ruleset}:${cursor ?? ''}`,
    loadList,
    { intervalMs: selectedId ? 0 : 15000 },
  );
  const detail = useOnlinePoll(`player-tournament:${owner}:${selectedId}`, loadDetail, {
    intervalMs: selectedId ? 5000 : 0,
  });
  useEffect(() => {
    const change = () => setFromRoute(routeId());
    window.addEventListener('popstate', change);
    return () => window.removeEventListener('popstate', change);
  }, []);
  useEffect(() => {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
    setDialog(null);
    setBusy(false);
    setError('');
    return () => {
      generation.current++;
      controller.current?.abort();
      controller.current = null;
    };
  }, [owner, selectedId, canAdmin]);
  useEffect(() => {
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        listing.hold();
        detail.hold();
      } else {
        void listing.resume();
        void detail.resume();
      }
    };
    if (document.visibilityState === 'hidden') visibility();
    document.addEventListener('visibilitychange', visibility);
    return () => document.removeEventListener('visibilitychange', visibility);
  }, [
    owner,
    selectedId,
    status,
    ruleset,
    cursor,
    listing.hold,
    listing.resume,
    detail.hold,
    detail.resume,
  ]);
  const tournament = detail.data?.tournament;
  const bracket = detail.data?.bracket;
  const numbers = new Intl.NumberFormat(locale);
  const date = (value: string) =>
    new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(new Date(value));
  const open = (kind: Action, match?: TournamentMatch) => {
    if (controller.current) return;
    setError('');
    if (kind === 'create') {
      setClockInput(defaultClockInput);
      setMatchMode('single');
    }
    setDialog({ kind, version: tournament?.version, match });
  };
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dialog || !owner || controller.current) return;
    const form = new FormData(event.currentTarget);
    const action = dialog.kind;
    const expected = generation.current;
    const request = new AbortController();
    controller.current = request;
    const valid = () =>
      !request.signal.aborted &&
      current.current.owner === owner &&
      current.current.selectedId === selectedId &&
      generation.current === expected;
    let path = `/player-tournaments/${encodeURIComponent(selectedId)}/${action}`;
    let body: Record<string, unknown> = {};
    if (action === 'create') {
      path = '/player-tournaments';
      const deadline = String(form.get('registration_deadline') || '');
      const plannedStart = String(form.get('scheduled_start_at') || '');
      if ([deadline, plannedStart].some((value) => value && !Number.isFinite(Date.parse(value)))) {
        controller.current = null;
        setError('Enter a valid date and time.');
        return;
      }
      let clock;
      try {
        clock = clockRequest(clockInput);
      } catch (failure) {
        controller.current = null;
        setError(errorMessage(failure));
        return;
      }
      body = {
        name: String(form.get('name')).trim(),
        ruleset: form.get('ruleset'),
        capacity: Number(form.get('capacity')),
        match_mode: matchMode,
        ...clock,
        registration_deadline: deadline ? new Date(deadline).toISOString() : null,
        scheduled_start_at: plannedStart ? new Date(plannedStart).toISOString() : null,
      };
    } else if (action === 'start') {
      const seeds = form.getAll('seed').map(String);
      if (new Set(seeds).size !== seeds.length) {
        controller.current = null;
        setError('Each entrant must have a different seed.');
        return;
      }
      body = { version: dialog.version, seeds };
    } else if (action === 'cancel') body = { version: dialog.version };
    else if (action === 'rebuild' || action === 'override') {
      body = { version: dialog.version, note: String(form.get('note') || '').trim() };
      if (action === 'override') {
        body.winner = form.get('winner');
        path = `/player-tournaments/${encodeURIComponent(selectedId)}/matches/${encodeURIComponent(dialog.match!.id)}/override`;
      }
    }
    setBusy(true);
    setError('');
    listing.hold();
    detail.hold();
    try {
      const updated = await api<PlayerTournamentDetail>(path, body, 'POST', request.signal);
      if (!valid()) return;
      setDialog(null);
      notify.success(t(action === 'create' ? 'Player tournament created.' : 'Tournament updated.'));
      if (action === 'create') navigate(`/player-tournaments/${encodeURIComponent(updated.id)}`);
    } catch (failure) {
      if (valid()) setError(errorMessage(failure));
    } finally {
      if (valid()) {
        controller.current = null;
        setBusy(false);
        void listing.resume();
        void detail.resume();
      }
    }
  }
  const needsReason = dialog?.kind === 'rebuild' || dialog?.kind === 'override';
  const entrant = tournament?.entrants.find((entry) => entry.account_id === owner);
  const active = selectedId ? detail : listing;
  const eligibleRegistration = account?.email_verified !== false;
  const matches = bracket ? allMatches(bracket) : [];
  const byId = new Map(matches.map((match) => [match.id, match]));
  if (!account) return null;
  return (
    <section
      className="player-tournaments"
      aria-labelledby="player-tournaments-heading"
      aria-busy={active.phase === 'loading'}
    >
      <header className="arena-surface player-tournaments-intro">
        <div>
          <p className="arena-kicker">{t('Compete in the Arena')}</p>
          <h1 id="player-tournaments-heading">{t('Player tournaments')}</h1>
          <p>
            {t('Human double-elimination competitions. Every game uses the official online board.')}
          </p>
        </div>
        {(listing.data?.capabilities?.can_create ??
          tournament?.capabilities?.can_create ??
          canAdmin) && (
          <button type="button" className="button primary" onClick={() => open('create')}>
            {t('Create player tournament')}
          </button>
        )}
      </header>
      {selectedId && (
        <a href="/player-tournaments" onClick={link}>
          {t('All player tournaments')}
        </a>
      )}
      {active.phase === 'loading' && <p role="status">{t('Loading player tournaments…')}</p>}
      {active.phase === 'stale' && (
        <div className="player-tournament-error" role="alert">
          <p>
            {t('Tournament data could not be refreshed. Displayed results may be out of date.')}
          </p>
          <button className="button secondary" type="button" onClick={() => void active.refresh()}>
            {t('Refresh')}
          </button>
        </div>
      )}
      {!selectedId && (
        <>
          <div className="player-tournament-filters">
            <label>
              {t('Status')}
              <select
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setCursors([]);
                }}
              >
                <option value="">{t('All statuses')}</option>
                <option value="upcoming">{t('Upcoming')}</option>
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {t(label)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Ruleset')}
              <select
                value={ruleset}
                onChange={(event) => {
                  setRuleset(event.target.value);
                  setCursors([]);
                }}
              >
                <option value="">{t('All rulesets')}</option>
                {['brandubh', 'tablut', 'copenhagen'].map((id) => (
                  <option value={id} key={id}>
                    {t(id === 'brandubh' ? 'Brandubh' : id === 'tablut' ? 'Tablut' : 'Copenhagen')}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {listing.phase !== 'loading' && listing.data?.items.length === 0 && (
            <div className="arena-surface player-tournament-empty">
              <h2>{t('The next saga is still to come')}</h2>
              <p>
                {t(
                  'No tournaments match these filters. Check back for registration, or explore online games while the community grows.',
                )}
              </p>
              <a className="button secondary" href="/online" onClick={link}>
                {t('Open online lobby')}
              </a>
            </div>
          )}
          <ul className="player-tournament-discovery">
            {listing.data?.items.map((item) => (
              <li className="arena-surface" key={item.id}>
                <span className="player-tournament-status">{t(statusLabels[item.status])}</span>
                <h2>
                  <a href={`/player-tournaments/${encodeURIComponent(item.id)}`} onClick={link}>
                    {item.name}
                  </a>
                </h2>
                <p>
                  {t('{ruleset} · {count} / {capacity} entrants', {
                    ruleset: t(
                      item.ruleset === 'brandubh'
                        ? 'Brandubh'
                        : item.ruleset === 'tablut'
                          ? 'Tablut'
                          : 'Copenhagen',
                    ),
                    count: numbers.format(item.entrant_count),
                    capacity: numbers.format(item.capacity),
                  })}
                </p>
                {item.scheduled_start_at && (
                  <p>{t('Planned start: {date}', { date: date(item.scheduled_start_at) })}</p>
                )}
                {item.registration_deadline && (
                  <p>
                    {t('Registration closes: {date}', { date: date(item.registration_deadline) })}
                  </p>
                )}
                {item.review_state === 'disputed' && (
                  <p className="player-review-notice">{t('Bracket under review')}</p>
                )}
                {item.status === 'complete' &&
                  item.review_state === 'ok' &&
                  item.official_winner && (
                    <p className="player-tournament-champion-inline">
                      <Portrait id={item.official_winner.portrait_id} />
                      <span>
                        {t('Champion: {player}', { player: playerName(item.official_winner, t) })}
                      </span>
                    </p>
                  )}
              </li>
            ))}
          </ul>
          <nav className="player-tournament-pagination" aria-label={t('Tournament pages')}>
            <button
              className="button secondary"
              type="button"
              disabled={!cursors.length || listing.phase === 'loading'}
              onClick={() => setCursors((values) => values.slice(0, -1))}
            >
              {t('Previous page')}
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={!listing.data?.next_cursor || listing.phase === 'loading'}
              onClick={() => {
                const next = listing.data?.next_cursor;
                if (next) setCursors((values) => [...values, next]);
              }}
            >
              {t('Next page')}
            </button>
          </nav>
        </>
      )}
      {selectedId && tournament && (
        <>
          <section
            className="arena-surface player-tournament-summary"
            aria-labelledby="player-tournament-name"
          >
            <div>
              <span className="player-tournament-status">{t(statusLabels[tournament.status])}</span>
              <h2 id="player-tournament-name">{tournament.name}</h2>
            </div>
            <dl>
              <div>
                <dt>{t('Ruleset')}</dt>
                <dd>
                  {t(
                    tournament.ruleset === 'brandubh'
                      ? 'Brandubh'
                      : tournament.ruleset === 'tablut'
                        ? 'Tablut'
                        : 'Copenhagen',
                  )}
                </dd>
              </div>
              <div>
                <dt>{t('Format')}</dt>
                <dd>{t('Double elimination')}</dd>
              </div>
              <div>
                <dt>{t('Match mode')}</dt>
                <dd>
                  {tournament.match_mode && tournament.match_mode !== 'legacy'
                    ? t(matchModeNames[tournament.match_mode])
                    : t('Legacy: {count} games per match', { count: tournament.match_legs })}
                </dd>
              </div>
              <div>
                <dt>{t('Time control')}</dt>
                <dd>
                  {t(
                    tournament.time_control === 'total'
                      ? '{seconds} seconds per player'
                      : '{seconds} seconds per move',
                    { seconds: numbers.format(tournament.time_per_move) },
                  )}
                  {tournament.time_control === 'total' && (
                    <>
                      {' '}
                      ·{' '}
                      {t('Increment: {seconds} seconds', {
                        seconds: tournament.increment_seconds ?? 0,
                      })}
                    </>
                  )}
                </dd>
              </div>
            </dl>
            {tournament.scheduled_start_at && (
              <p>
                {t('Planned start: {date}', { date: date(tournament.scheduled_start_at) })}
                {tournament.status === 'registration' && (
                  <>
                    {' '}
                    ·{' '}
                    {t(
                      'Starts automatically when full at the planned time. Otherwise, the creator can start with at least two entrants.',
                    )}
                  </>
                )}
              </p>
            )}
            {tournament.registration_deadline && (
              <p>
                {t('Registration closes: {date}', { date: date(tournament.registration_deadline) })}
              </p>
            )}
            {tournament.status === 'registration' && !tournament.scheduled_start_at && (
              <p>
                {t(
                  'The creator or an administrator can start with at least two registered entrants.',
                )}
              </p>
            )}
            {tournament.started_at && (
              <p>{t('Started: {date}', { date: date(tournament.started_at) })}</p>
            )}
            {tournament.completed_at && (
              <p>{t('Completed: {date}', { date: date(tournament.completed_at) })}</p>
            )}
            <p>
              {t(
                'Two match losses eliminate a player. Individual game losses do not. Match games alternate sides.',
              )}
            </p>
            {tournament.status === 'registration' && (
              <div className="player-tournament-actions">
                <button
                  className="button primary"
                  type="button"
                  disabled={
                    busy ||
                    (!entrant &&
                      (!eligibleRegistration ||
                        tournament.registration_open === false ||
                        tournament.entrant_count >= tournament.capacity)) ||
                    detail.phase !== 'ready'
                  }
                  onClick={() => open(entrant ? 'withdraw' : 'register')}
                >
                  {t(entrant ? 'Withdraw registration' : 'Register for tournament')}
                </button>
                {!eligibleRegistration && (
                  <a href="/profile#email-verification" onClick={link}>
                    {t('Verify your email before registering.')}
                  </a>
                )}
              </div>
            )}
            {tournament.status === 'registration' && tournament.registration_open === false && (
              <p>
                {t(
                  'Registration is closed. Registered entrants may withdraw until the tournament starts.',
                )}
              </p>
            )}
            {tournament.review_state === 'disputed' && (
              <p className="player-review-notice" role="status">
                {t(
                  'This bracket is under review after a result correction. Advancement is paused until an administrator rebuilds it.',
                )}
              </p>
            )}
            {tournament.status === 'complete' &&
              tournament.review_state === 'ok' &&
              tournament.official_winner && (
                <div className="player-tournament-champion">
                  <Portrait id={tournament.official_winner.portrait_id} />
                  <div>
                    <p className="arena-kicker">{t('Champion')}</p>
                    <h3>{playerName(tournament.official_winner, t)}</h3>
                  </div>
                </div>
              )}
            {(tournament.capabilities?.can_start || canAdmin) && (
              <div className="player-tournament-admin" aria-label={t('Tournament administration')}>
                {tournament.status === 'registration' &&
                  (tournament.capabilities?.can_start ?? canAdmin) && (
                    <button
                      type="button"
                      className="button secondary"
                      disabled={busy || tournament.entrant_count < 2 || detail.phase !== 'ready'}
                      onClick={() => open('start')}
                    >
                      {t('Start tournament')}
                    </button>
                  )}
                {canAdmin && ['registration', 'running'].includes(tournament.status) && (
                  <button
                    type="button"
                    className="button secondary"
                    disabled={busy || detail.phase !== 'ready'}
                    onClick={() => open('cancel')}
                  >
                    {t('Cancel tournament')}
                  </button>
                )}

                {canAdmin && tournament.review_state === 'disputed' && (
                  <button
                    type="button"
                    className="button secondary"
                    disabled={busy || detail.phase !== 'ready'}
                    onClick={() => open('rebuild')}
                  >
                    {t('Rebuild bracket')}
                  </button>
                )}
              </div>
            )}
          </section>
          <section
            className="arena-surface player-tournament-entrants"
            aria-labelledby="player-entrants-heading"
          >
            <h2 id="player-entrants-heading">{t('Entrants and seeds')}</h2>
            {!tournament.entrants.length && (
              <p>{t('No entrants yet. Be the first to register.')}</p>
            )}
            <ol>
              {tournament.entrants.map((entry) => (
                <li key={entry.account_id}>
                  <Portrait id={entry.portrait_id} />
                  <div>
                    <strong>{playerName(entry, t)}</strong>
                    <p>
                      {entry.seed === null
                        ? t('Not seeded')
                        : t('Seed {seed}', { seed: entry.seed })}
                      {typeof entry.losses === 'number' && (
                        <>
                          {' '}
                          · {t('Match losses: {count}', { count: numbers.format(entry.losses) })}
                        </>
                      )}
                    </p>
                    {entry.eliminated && <span>{t('Eliminated')}</span>}
                  </div>
                </li>
              ))}
            </ol>
          </section>
          {bracket && tournament.status !== 'registration' && (
            <section
              className="arena-surface player-tournament-bracket"
              aria-labelledby="player-bracket-heading"
            >
              <header>
                <h2 id="player-bracket-heading">{t('Tournament bracket')}</h2>
                <div className="player-bracket-mode" aria-label={t('Bracket display')}>
                  <button
                    className="button secondary"
                    type="button"
                    aria-pressed={mode === 'graph'}
                    onClick={() => setMode('graph')}
                  >
                    {t('Bracket')}
                  </button>
                  <button
                    className="button secondary"
                    type="button"
                    aria-pressed={mode === 'list'}
                    onClick={() => setMode('list')}
                  >
                    {t('Accessible match list')}
                  </button>
                </div>
              </header>
              {mode === 'graph' ? (
                <BracketGraph
                  bracket={bracket}
                  canOverride={
                    canAdmin && tournament.review_state === 'ok' && tournament.status === 'running'
                  }
                  onOverride={(match) => open('override', match)}
                />
              ) : (
                <ol className="player-bracket-list">
                  {matches.map((match) => (
                    <li key={match.id}>
                      <MatchCard
                        match={match}
                        matches={byId}
                        canOverride={
                          canAdmin &&
                          tournament.review_state === 'ok' &&
                          tournament.status === 'running'
                        }
                        onOverride={(entry) => open('override', entry)}
                      />
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
        </>
      )}
      {dialog &&
        (canAdmin ||
          dialog.kind === 'create' ||
          (dialog.kind === 'start' && tournament?.capabilities?.can_start) ||
          dialog.kind === 'register' ||
          dialog.kind === 'withdraw') && (
          <Modal
            title={t(actionLabels[dialog.kind])}
            onClose={() => {
              if (!busy) {
                setDialog(null);
                setError('');
              }
            }}
            initialFocus="input,select,textarea,button[type=submit]"
            className="player-tournament-dialog"
          >
            <form onSubmit={(event) => void submit(event)} aria-busy={busy}>
              <fieldset disabled={busy}>
                {dialog.kind === 'create' && (
                  <>
                    <label>
                      {t('Tournament name')}
                      <input name="name" required maxLength={120} />
                    </label>
                    <label>
                      {t('Ruleset')}
                      <select name="ruleset" defaultValue="tablut">
                        <option value="brandubh">Brandubh</option>
                        <option value="tablut">Tablut</option>
                        <option value="copenhagen">Copenhagen</option>
                      </select>
                    </label>
                    <label>
                      {t('Maximum entrants')}
                      <input
                        name="capacity"
                        type="number"
                        min={2}
                        max={64}
                        step={1}
                        defaultValue={8}
                        required
                      />
                    </label>
                    <MatchModeField value={matchMode} onChange={setMatchMode} />
                    <ClockFields value={clockInput} onChange={setClockInput} />
                    <LocalDateTimePicker
                      name="registration_deadline"
                      label="Registration deadline (your local time, optional)"
                    />
                    <LocalDateTimePicker
                      name="scheduled_start_at"
                      label="Planned start (your local time, optional)"
                    />
                    <p>
                      {t(
                        'Starts automatically when full at the planned time. Otherwise, the creator can start with at least two entrants.',
                      )}
                    </p>
                  </>
                )}
                {dialog.kind === 'register' && (
                  <p>
                    {t(
                      'Register your account for this competition? Games are played through the normal online board.',
                    )}
                  </p>
                )}
                {dialog.kind === 'withdraw' && (
                  <p>
                    {t(
                      'Withdraw before the bracket is seeded? You may register again while registration remains open.',
                    )}
                  </p>
                )}
                {dialog.kind === 'start' && (
                  <>
                    <p>
                      {t(
                        'Review the seed order. Starting closes registration and creates official online games.',
                      )}
                    </p>
                    <ol className="player-seed-editor">
                      {tournament?.entrants.map((entry, index) => (
                        <li key={entry.account_id}>
                          <label>
                            {t('Seed {seed}', { seed: index + 1 })}
                            <select name="seed" defaultValue={entry.account_id}>
                              {tournament.entrants.map((candidate) => (
                                <option key={candidate.account_id} value={candidate.account_id}>
                                  {playerName(candidate, t)}
                                </option>
                              ))}
                            </select>
                          </label>
                        </li>
                      ))}
                    </ol>
                  </>
                )}
                {dialog.kind === 'cancel' && (
                  <p>
                    {t(
                      'Active tournament games will be stopped. Completed results remain in history. Cancel this tournament?',
                    )}
                  </p>
                )}
                {dialog.kind === 'rebuild' && (
                  <p>
                    {t(
                      'Rebuild from official corrected results? Displaced active games will be stopped and previous overrides must be reviewed again.',
                    )}
                  </p>
                )}
                {dialog.kind === 'override' && dialog.match && (
                  <>
                    <p>
                      {t('Choose the official winner. Active games in this match will be stopped.')}
                    </p>
                    <label>
                      {t('Match winner')}
                      <select name="winner" required>
                        {[dialog.match.participants.a, dialog.match.participants.b]
                          .filter((player): player is TournamentPlayer => player !== null)
                          .map((player) => (
                            <option key={player.account_id} value={player.account_id}>
                              {playerName(player, t)}
                            </option>
                          ))}
                      </select>
                    </label>
                  </>
                )}
                {needsReason && (
                  <label>
                    {t('Private administrator reason')}
                    <textarea name="note" required minLength={1} maxLength={1000} />
                  </label>
                )}
                {error && (
                  <p className="player-tournament-error" role="alert">
                    {t(error)}
                  </p>
                )}
                <div className="dialog-actions">
                  <button
                    className="button secondary"
                    type="button"
                    onClick={() => {
                      setDialog(null);
                      setError('');
                    }}
                  >
                    {t('Back')}
                  </button>
                  <button className="button primary" type="submit">
                    {t(busy ? 'Saving…' : actionLabels[dialog.kind])}
                  </button>
                </div>
              </fieldset>
            </form>
          </Modal>
        )}
    </section>
  );
}
