import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import Board, { coordinate } from './Board';
import ReplayControls from './ReplayControls';
import { useReplayPlayback } from './useReplayPlayback';
import { useOnlinePoll } from './useOnlinePoll';
import { useI18n } from './i18n';
import AgentAnalysis, { useGameAnalysis } from './AgentAnalysis';
import { AnalyticsSettingsPanel } from './AdvisorSelect';
import type { AIProfile, Game, GameMove, Model, PlayerConfig, Side } from './types';
import type { OnlinePosition, OnlineReplay } from './onlineTypes';
import type { AccountReviewData } from './personalTypes';
import { resultName } from './RecordedGames';
import { modeNames as onlineModeNames } from './Online';

const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const statusNames: Record<string, string> = {
  completed: 'Completed',
  active: 'Active',
  waiting: 'Waiting',
  cancelled: 'Cancelled',
  voided: 'Voided',
  in_progress: 'In progress',
};
const modeNames: Record<string, string> = {
  'human-ai': 'Human vs AI',
  'ai-ai': 'AI vs AI',
  'human-human': 'Human vs Human',
  ...onlineModeNames,
};
const sideGlyph = (side: Side) => (
  <span className={`mini-piece ${side === 'attackers' ? 'black' : 'ivory'}`} aria-hidden="true" />
);

type ReviewParticipant = { name: string; kind: 'human' | 'ai' };
type ReviewGame = {
  id: string;
  category: 'online' | 'account-single-player';
  ruleset: string;
  mode: string;
  status: string;
  winner: Side | 'draw' | null;
  reason: string | null;
  version: number;
  players: Record<Side, ReviewParticipant>;
};

function normalize(data: OnlineReplay | AccountReviewData): {
  game: ReviewGame;
  moves: GameMove[];
  states: OnlinePosition[];
} {
  if (data.game.category === 'online') {
    const replay = data as OnlineReplay;
    const online = replay.game;
    const participants = online.participants;
    return {
      game: {
        id: online.id,
        category: 'online',
        ruleset: online.ruleset,
        mode: online.match.mode,
        status: online.status,
        winner: online.result ? online.result.winner : online.position.winner,
        reason: online.result?.termination_reason ?? online.position.reason,
        version: online.version,
        players: {
          attackers: {
            name: participants.attackers?.name ?? '',
            kind: 'human',
          },
          defenders: {
            name: participants.defenders?.name ?? '',
            kind: 'human',
          },
        },
      },
      moves: online.moves.map((move, index) => ({
        source: move.source,
        target: move.target,
        side: index % 2 ? 'defenders' : 'attackers',
      })),
      states: replay.states,
    };
  }
  const account = (data as AccountReviewData).game;
  return {
    game: {
      id: account.id,
      category: 'account-single-player',
      ruleset: account.ruleset,
      mode: account.mode,
      status: account.status,
      winner: account.winner,
      reason: null,
      version: (data as AccountReviewData).version,
      players: {
        attackers: { name: account.players.attackers.name, kind: account.players.attackers.kind },
        defenders: { name: account.players.defenders.name, kind: account.players.defenders.kind },
      },
    },
    moves: (data as AccountReviewData).moves.map((move, index) => ({
      source: move.source,
      target: move.target,
      side: index % 2 ? 'defenders' : 'attackers',
    })),
    states: (data as AccountReviewData).states,
  };
}

export default function RecordedReview({
  id,
  category,
  accountId,
  personal = false,
}: {
  id: string;
  category: 'online' | 'account-single-player';
  accountId: string;
  personal?: boolean;
}) {
  const { t } = useI18n();
  const url = personal
    ? `/personal/single-player/${id}/replay`
    : `/history/${category}/${id}/replay`;
  const load = useCallback(
    (signal: AbortSignal) =>
      api<OnlineReplay | AccountReviewData>(url, undefined, undefined, signal),
    [url],
  );
  const poll = useOnlinePoll(`review:${accountId}:${url}`, load);
  const data = poll.data;
  const normalized = useMemo(() => (data ? normalize(data) : null), [data]);
  const playback = useReplayPlayback(normalized?.states.length ?? 1);
  const setCursor = playback.setCursor;
  const index = playback.cursor;
  const position = normalized?.states[index];
  const accountReview = normalized?.game.category === 'account-single-player';

  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  useEffect(() => {
    if (!accountReview) return;
    const controller = new AbortController();
    void Promise.all([
      api<AIProfile[]>('/ai/profiles', undefined, undefined, controller.signal),
      api<Model[]>('/models', undefined, undefined, controller.signal),
    ])
      .then(([nextProfiles, nextModels]) => {
        if (!controller.signal.aborted) {
          setProfiles(nextProfiles);
          setModels(nextModels);
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [accountReview]);

  const game = useMemo<Game | null>(() => {
    if (!normalized || !position) return null;
    const size = Math.round(Math.sqrt(position.board.length));
    const initial = normalized.states[0]?.board ?? position.board;
    const count = (board: string[], piece: string) =>
      board.filter((value) => value === piece).length;
    const side = (value: string): Side => (value === 'attackers' ? 'attackers' : 'defenders');
    const players = {
      attackers: {
        name: normalized.game.players.attackers.name,
        kind: normalized.game.players.attackers.kind,
        config: {},
      },
      defenders: {
        name: normalized.game.players.defenders.name,
        kind: normalized.game.players.defenders.kind,
        config: {},
      },
    } as Record<Side, PlayerConfig>;
    return {
      id: normalized.game.id,
      ruleset: normalized.game.ruleset,
      size,
      board: position.board,
      turn: side(position.turn),
      ply: position.ply,
      winner: position.winner,
      reason: position.reason,
      moves: normalized.moves,
      cursor: index,
      total_moves: normalized.moves.length,
      legal_moves: [],
      mode: accountReview ? (normalized.game.mode as Game['mode']) : 'human-human',
      players,
      version: normalized.game.version,
      captured: {
        attackers: count(initial, 'A') - count(position.board, 'A'),
        defenders:
          count(initial, 'D') +
          count(initial, 'K') -
          count(position.board, 'D') -
          count(position.board, 'K'),
      },
      created_at: '',
    };
  }, [normalized, position, index, accountReview]);

  // Playback must not trigger analysis requests; the advisor resumes when stopped.
  const analysis = useGameAnalysis(accountReview ? game : null, accountReview && !playback.playing);

  if (poll.phase === 'loading') {
    return (
      <section className="recorded-review panel" aria-label={t('Selected game review')}>
        <p role="status">{t('Loading your games…')}</p>
      </section>
    );
  }
  if (poll.phase === 'stale' || !data?.validated || !game || !position || !normalized) {
    return (
      <section className="recorded-review panel" aria-label={t('Selected game review')}>
        <p role="alert">{t('Review unavailable. Check your access or retry.')}</p>
        <button className="button secondary" onClick={() => void poll.refresh()}>
          {t('Retry')}
        </button>
      </section>
    );
  }

  return (
    <section className="recorded-review" aria-label={t('Selected game review')}>
      <div className="game-layout">
        <aside className="game-sidebar">
          <section className="panel game-details">
            <div className="variant-heading">
              <h2>{t(ruleNames[normalized.game.ruleset] ?? normalized.game.ruleset)}</h2>
              <span className="badge">
                {game.size} × {game.size}
              </span>
            </div>
            <p>{t('Read-only review. Replay controls change only your view.')}</p>
            <p className="subtle">{normalized.game.id}</p>
            <div className="divider" />
            <div className="detail-row">
              <span>{t('Game mode')}</span>
              <strong>{t(modeNames[normalized.game.mode] ?? normalized.game.mode)}</strong>
            </div>
            <div className="detail-row">
              <span>{t('Lifecycle')}</span>
              <strong>{t(statusNames[normalized.game.status] ?? normalized.game.status)}</strong>
            </div>
            <div className="detail-row">
              <span>{t('Move')}</span>
              <strong>{index}</strong>
            </div>
            <div className="detail-row">
              <span>{t('Recorded result')}</span>
              <strong>
                {t(resultName(normalized.game.winner))}
                {normalized.game.reason
                  ? ` · ${t(normalized.game.reason.replaceAll('_', ' '))}`
                  : ''}
              </strong>
            </div>
          </section>
          <section className="panel armies">
            {(['attackers', 'defenders'] as Side[]).map((side) => (
              <div className="army" key={side}>
                <div className="army-symbol">{sideGlyph(side)}</div>
                <div>
                  <h3>{normalized.game.players[side].name || t('Unassigned')}</h3>
                  <p>
                    {t(titles[side])} ·{' '}
                    {normalized.game.players[side].kind === 'ai' ? t('Computer') : t('Human')}
                  </p>
                </div>
              </div>
            ))}
            <div className="captured-row">
              <span>{t('Captured')}</span>
              <span>
                {sideGlyph('attackers')} {game.captured.attackers}
              </span>
              <span>
                {sideGlyph('defenders')} {game.captured.defenders}
              </span>
            </div>
          </section>
          {accountReview ? (
            <AgentAnalysis game={game} {...analysis} />
          ) : (
            <section className="panel agent-analysis" aria-label={t('Agent evaluations')}>
              <h2>{t('Winning chances')}</h2>
              <p className="subtle">
                {t(
                  'Winning chances are unavailable for recorded online games. Online analysis stays disabled for every role.',
                )}
              </p>
            </section>
          )}
        </aside>
        <section className="battlefield" aria-label={t('Game')}>
          <div className={`turn-banner ${position.winner ? 'finished' : ''}`} aria-live="polite">
            <div>
              {position.winner ? (
                <>
                  <strong>
                    {position.winner === 'draw'
                      ? t('An evenly matched saga')
                      : t('{side} win', { side: t(titles[position.winner]) })}
                  </strong>
                  <span>{position.reason ? t(position.reason.replaceAll('_', ' ')) : ''}</span>
                </>
              ) : (
                <>
                  {sideGlyph(game.turn)}
                  <strong>{t('{side} to move', { side: t(titles[game.turn]) })}</strong>
                  <span>{t('Replay position {move}', { move: index })}</span>
                </>
              )}
            </div>
          </div>
          <Board
            game={game}
            selected={null}
            onSelect={() => {}}
            readOnly
            hint={analysis.report?.hint?.move ?? null}
          />
          <ReplayControls
            cursor={index}
            length={normalized.states.length}
            onChange={setCursor}
            disabled={poll.phase !== 'ready'}
            playback={{ playing: playback.playing, toggle: playback.toggle }}
          />
        </section>
        <aside className="panel history-panel">
          <div className="panel-title">
            <h2>{t('The saga so far')}</h2>
            <span className="badge">{normalized.moves.length}</span>
          </div>
          <p className="subtle">{t('A record of every move')}</p>
          <div className="history-columns">
            <span>{t('MOVE')}</span>
            <span>{t('JOURNEY')}</span>
            <span>{t('SIDE')}</span>
          </div>
          <ol className="move-list">
            {normalized.moves.map((move, moveIndex) => (
              <li
                key={moveIndex}
                className={moveIndex === index - 1 ? 'current' : moveIndex >= index ? 'future' : ''}
              >
                <button
                  onClick={() => setCursor(moveIndex + 1)}
                  aria-label={t('Replay to move {move}', { move: moveIndex + 1 })}
                >
                  <span>{String(moveIndex + 1).padStart(2, '0')}</span>
                  <strong>
                    {move.notation ??
                      `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`}
                  </strong>
                  {sideGlyph(move.side)}
                </button>
              </li>
            ))}
          </ol>
          {normalized.moves.length === 0 && (
            <div className="history-empty">
              <span aria-hidden="true">ᛝ</span>
              <p>
                {t('Every saga starts')}
                <br />
                {t('with a first move.')}
              </p>
            </div>
          )}
          <button
            className="history-start text-button"
            onClick={() => setCursor(0)}
            disabled={index === 0}
          >
            {t('↤ Starting position')}
          </button>
          <div className="history-footer">
            <span className="status-dot" /> {t('Read-only replay')}
          </div>
          {accountReview ? (
            <AnalyticsSettingsPanel
              game={game}
              profiles={profiles}
              models={models}
              settings={analysis.settings}
              onChange={analysis.updateSettings}
              pending={analysis.settingsPending}
              error={analysis.settingsError}
            />
          ) : (
            <section className="panel analytics-settings" aria-label={t('Analytics')}>
              <h2>{t('Analytics')}</h2>
              <p className="subtle">{t('Analysis is unavailable for recorded online games.')}</p>
            </section>
          )}
        </aside>
      </div>
    </section>
  );
}
