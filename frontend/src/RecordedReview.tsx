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
import { sideGlyph } from './sideGlyph';
import { modeNames as onlineModeNames } from './Online';
import PlayPage from './PlayPage';
import GameLayout from './GameLayout';
import GameDetailsPanel from './GameDetailsPanel';
import ArmiesPanel from './ArmiesPanel';
import HistoryPanel from './HistoryPanel';
import Battlefield from './Battlefield';
import ActiveGameSwitcher from './ActiveGameSwitcher';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';

const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
const titles: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };
const modeNames: Record<string, string> = {
  'human-ai': 'Human vs AI',
  'ai-ai': 'AI vs AI',
  'human-human': 'Pass & play',
  ...onlineModeNames,
};
const statusNames: Record<string, string> = {
  completed: 'Completed',
  active: 'Active',
  waiting: 'Waiting',
  cancelled: 'Cancelled',
  voided: 'Voided',
  in_progress: 'In progress',
};

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

/** Reconstruct the captured pieces for the board overlay without trusting moves. */
function capturedBetween(
  before: OnlinePosition,
  after: OnlinePosition,
): { square: number; piece: string }[] {
  const captured: { square: number; piece: string }[] = [];
  for (let square = 0; square < before.board.length; square++) {
    if (before.board[square] !== '.' && after.board[square] === '.') {
      captured.push({ square, piece: before.board[square] });
    }
  }
  return captured;
}

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
        captured:
          replay.states[index] && replay.states[index + 1]
            ? capturedBetween(replay.states[index], replay.states[index + 1])
            : [],
      })),
      states: replay.states,
    };
  }
  const account = (data as AccountReviewData).game;
  const accountStates = (data as AccountReviewData).states;
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
      captured:
        accountStates[index] && accountStates[index + 1]
          ? capturedBetween(accountStates[index], accountStates[index + 1])
          : [],
    })),
    states: accountStates,
  };
}

export default function RecordedReview({
  id,
  category,
  accountId,
  personal = false,
  backTo,
}: {
  id: string;
  category: 'online' | 'account-single-player';
  accountId: string;
  personal?: boolean;
  backTo?: string;
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
  const onlineReview = normalized?.game.category === 'online';
  const analysisAllowed =
    accountReview || (onlineReview && normalized?.game.status === 'completed');

  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  useEffect(() => {
    if (!analysisAllowed) return;
    const controller = new AbortController();
    void Promise.all([
      api<AIProfile[]>('/ai/profiles', undefined, undefined, controller.signal),
      api<Model[]>('/models', undefined, undefined, controller.signal),
    ])
      .then(([nextProfiles, nextModels]) => {
        if (!controller.signal.aborted) {
          setProfiles(Array.isArray(nextProfiles) ? nextProfiles : []);
          setModels(Array.isArray(nextModels) ? nextModels : []);
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [analysisAllowed]);

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
  // Completed online reviews reuse the same analysis engine through the
  // online-scoped adapter; ongoing online games never reach this component.
  const analysis = useGameAnalysis(
    analysisAllowed ? game : null,
    !!analysisAllowed && !playback.playing,
    accountReview,
    onlineReview ? '/history/online' : '/games',
  );

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

  const back = backTo ?? (personal ? '/dashboard' : '/archive');
  const backLabel = personal ? 'Back to dashboard' : 'Back to Hall of records';
  const reviewRows = [
    {
      label: t('Lifecycle'),
      value: t(statusNames[normalized.game.status] ?? normalized.game.status),
    },
    {
      label: t('Recorded result'),
      value: (
        <>
          {t(resultName(normalized.game.winner))}
          {normalized.game.reason ? ` · ${t(normalized.game.reason.replaceAll('_', ' '))}` : ''}
        </>
      ),
    },
  ];
  const gameDetails = (
    <GameDetailsPanel
      ruleset={t(ruleNames[game.ruleset] ?? game.ruleset)}
      size={game.size}
      header={
        <a className="text-button review-back" href={back} onClick={surfaceLink}>
          {t(backLabel)}
        </a>
      }
      rows={[
        {
          label: t('Game mode'),
          value: t(modeNames[normalized.game.mode] ?? normalized.game.mode),
        },
        ...reviewRows,
      ]}
      desktopRows={reviewRows}
    />
  );
  const armies = (
    <ArmiesPanel
      rows={(['attackers', 'defenders'] as Side[]).map((side) => ({
        side,
        symbol: sideGlyph(side),
        name: game.players[side].name || t('Unassigned'),
        detail: `${t(titles[side])} · ${t(game.players[side].kind === 'ai' ? 'Computer' : 'Human')}`,
        toMove: position.turn === side && !position.winner,
      }))}
      captured={game.captured}
      metadata={[
        {
          label: t('Game mode'),
          value: t(modeNames[normalized.game.mode] ?? normalized.game.mode),
        },
      ]}
    />
  );
  const evaluations = analysisAllowed ? <AgentAnalysis game={game} {...analysis} /> : null;
  const settings = analysisAllowed ? (
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={models}
      settings={analysis.settings}
      onChange={analysis.updateSettings}
      pending={analysis.settingsPending}
      error={analysis.settingsError}
    />
  ) : undefined;
  const history = (
    <HistoryPanel
      count={normalized.moves.length}
      status={
        <div className="history-review-status review-history-status">
          <span>{t('Read-only replay')}</span>
          <ReplayControls
            cursor={index}
            length={normalized.states.length}
            onChange={setCursor}
            disabled={poll.phase !== 'ready'}
            playback={{ playing: playback.playing, toggle: playback.toggle }}
          />
        </div>
      }
      entries={normalized.moves.map((move, moveIndex) => ({
        ply: moveIndex + 1,
        notation:
          move.notation ??
          `${coordinate(move.source, game.size)} → ${coordinate(move.target, game.size)}`,
        side: move.side,
        current: moveIndex === index - 1,
        future: moveIndex >= index,
        onSeek: () => setCursor(moveIndex + 1),
      }))}
      startAction={{ onClick: () => setCursor(0), disabled: index === 0 }}
    >
      {settings}
    </HistoryPanel>
  );
  return (
    <PlayPage
      className="play-page"
      heading={t('Selected game review')}
      label={t('Selected game review')}
      gameId={game.id}
    >
      <GameLayout
        key={game.id}
        sidebar={
          <>
            {gameDetails}
            {armies}
            {evaluations}
          </>
        }
        desktop={{
          sidebar: (
            <>
              {gameDetails}
              {armies}
            </>
          ),
          analytics:
            settings !== undefined ? (
              <>
                {evaluations}
                {settings}
              </>
            ) : undefined,
          moveCount: game.total_moves,
        }}
        mobile={{
          title: t(ruleNames[game.ruleset] ?? game.ruleset),
          subtitle: (
            <>
              {t(statusNames[normalized.game.status] ?? normalized.game.status)} ·{' '}
              {t(resultName(normalized.game.winner))}
              {normalized.game.reason ? ` · ${t(normalized.game.reason.replaceAll('_', ' '))}` : ''}
            </>
          ),
          players: armies,
          analytics: settings !== undefined ? evaluations : undefined,
          settings,
          moveCount: game.total_moves,
          back: { href: back, label: backLabel },
        }}
        switcher={
          <ActiveGameSwitcher
            accountId={accountId}
            currentId={game.id}
            open={(entry) =>
              navigate(entry.type === 'online' ? `/online/games/${entry.id}` : `/games/${entry.id}`)
            }
          />
        }
        battlefield={
          <Battlefield
            label={t('Game')}
            board={
              <Board
                game={game}
                selected={null}
                onSelect={() => {}}
                disabled
                readOnly
                hint={analysis.report?.hint?.move ?? null}
              />
            }
          />
        }
        history={history}
      />
    </PlayPage>
  );
}
