import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { coordinate } from './Board';
import { useI18n } from './i18n';
import {
  defaultAnalyticsSettings,
  type AnalyticsSettings,
  type PositionAnalysis,
} from './analysisTypes';
import type { Game } from './types';
import Rune from './Rune';
import './analysis.css';

/** Background requests are tied to both the position and the advisor revision. */
export function useGameAnalysis(game: Game | null, visible: boolean, historical = false) {
  const gameId = game?.id ?? '';
  const currentGame = useRef(gameId);
  currentGame.current = gameId;
  const [preferences, setPreferences] = useState<{
    gameId: string;
    value: AnalyticsSettings;
    pending: boolean;
    confirmed: boolean;
    error?: string;
  } | null>(null);
  const settingsQueue = useRef<Promise<unknown>>(Promise.resolve());
  const settingsSequence = useRef(0);
  const inlineSettings = game?.analytics;
  const confirmedSettings = useRef<{ gameId: string; value: AnalyticsSettings } | null>(null);
  useEffect(() => {
    if (!gameId) return;
    let alive = true;
    const sequence = ++settingsSequence.current;
    if (inlineSettings) {
      confirmedSettings.current = { gameId, value: inlineSettings };
      setPreferences({ gameId, value: inlineSettings, pending: false, confirmed: true });
      return;
    }
    setPreferences({ gameId, value: defaultAnalyticsSettings, pending: true, confirmed: false });
    void api<AnalyticsSettings>(`/games/${gameId}/analytics`)
      .then((value) => {
        if (alive && settingsSequence.current === sequence) {
          confirmedSettings.current = { gameId, value };
          setPreferences({ gameId, value, pending: false, confirmed: true });
        }
      })
      .catch((error: Error) => {
        if (alive && settingsSequence.current === sequence)
          setPreferences({
            gameId,
            value: defaultAnalyticsSettings,
            pending: false,
            confirmed: false,
            error: error.message,
          });
      });
    return () => {
      alive = false;
    };
    // A settings update is independent of subsequent gameplay responses.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameId]);
  const settings =
    preferences?.gameId === gameId
      ? preferences.value
      : (inlineSettings ?? defaultAnalyticsSettings);
  const settingsPending = !!gameId && (preferences?.gameId !== gameId || preferences.pending);
  const settingsError = preferences?.gameId === gameId ? preferences.error : undefined;
  const settingsConfirmed = preferences?.gameId === gameId && preferences.confirmed;
  const updateSettings = useCallback(
    async (next: AnalyticsSettings) => {
      if (!gameId) return;
      const sequence = ++settingsSequence.current;
      setPreferences({ gameId, value: next, pending: true, confirmed: false });
      // Serialize writes so a slow older request cannot undo the latest selection.
      const operation = settingsQueue.current
        .catch(() => undefined)
        .then(() =>
          api<AnalyticsSettings>(
            `/games/${gameId}/analytics`,
            {
              advisor: next.advisor && {
                name: next.advisor.name,
                profile_id: next.advisor.profile_id,
                config: next.advisor.config,
              },
              time_limit: next.time_limit,
              // Removing the checkbox makes the advisor the single trigger for
              // winning chances, so keep the persisted gate enabled.
              show_win_chances: true,
            },
            'PUT',
          ),
        );
      settingsQueue.current = operation;
      try {
        const value = await operation;
        if (currentGame.current === gameId) confirmedSettings.current = { gameId, value };
        if (currentGame.current === gameId && settingsSequence.current === sequence)
          setPreferences({ gameId, value, pending: false, confirmed: true });
      } catch (error) {
        if (currentGame.current !== gameId || settingsSequence.current !== sequence) return;
        const message = (error as Error).message;
        // A failed response may still have committed. Read the server's effective
        // preferences before showing results or enabling a new analysis request.
        try {
          const value = await api<AnalyticsSettings>(`/games/${gameId}/analytics`);
          if (currentGame.current === gameId && settingsSequence.current === sequence) {
            confirmedSettings.current = { gameId, value };
            setPreferences({ gameId, value, pending: false, confirmed: true, error: message });
          }
        } catch {
          if (currentGame.current === gameId && settingsSequence.current === sequence) {
            const value =
              confirmedSettings.current?.gameId === gameId
                ? confirmedSettings.current.value
                : defaultAnalyticsSettings;
            setPreferences({ gameId, value, pending: false, confirmed: false, error: message });
          }
        }
      }
    },
    [gameId],
  );
  const key = game
    ? `${game.id}:${game.version}:${game.cursor}:${settings.revision}:${JSON.stringify([settings.advisor, settings.time_limit, settings.show_win_chances])}`
    : '';
  // Winning chances and the best-move hint belong to the selected move advisor.
  const needed = !!game && !!settings.advisor;
  const stored =
    !settingsPending &&
    game?.analysis?.cursor === game?.cursor &&
    (game?.analysis?.advisor_revision ?? 0) === settings.revision
      ? game?.analysis
      : null;
  const [request, setRequest] = useState<{
    key: string;
    status: 'pending' | 'done' | 'failed';
    report?: PositionAnalysis;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (
      !game ||
      !visible ||
      !needed ||
      settingsPending ||
      !settingsConfirmed ||
      stored?.status === 'complete'
    )
      return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sequence = settingsSequence.current;
    setRequest({ key, status: 'pending', report: stored ?? undefined });
    const poll = async () => {
      try {
        const report = await api<PositionAnalysis>(`/games/${game.id}/analysis`, {
          version: game.version,
          cursor: game.cursor,
          background: true,
          historical,
        });
        if (!alive) return;
        // A response from before an advisor edit must never redraw its arrow.
        if ((report.advisor_revision ?? 0) !== settings.revision) {
          const value = await api<AnalyticsSettings>(`/games/${game.id}/analytics`);
          if (alive && settingsSequence.current === sequence) {
            confirmedSettings.current = { gameId, value };
            setPreferences({ gameId, value, pending: false, confirmed: true });
          }
          return;
        }
        const pending = report.status === 'pending';
        setRequest({ key, status: pending ? 'pending' : 'done', report });
        if (pending) timer = setTimeout(() => void poll(), 300);
      } catch {
        if (alive)
          setRequest((previous) => ({
            key,
            status: 'failed',
            report: previous?.key === key ? previous.report : undefined,
          }));
      }
    };
    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // Stored reports arrive with gameplay responses; requests are keyed explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, visible, needed, settingsPending, settingsConfirmed, attempt, historical]);
  const current = request?.key === key ? request : null;
  const report = current?.report ?? stored ?? null;
  return {
    report,
    pending:
      needed &&
      !settingsPending &&
      settingsConfirmed &&
      (current ? current.status === 'pending' : !stored || stored.status === 'pending'),
    failed: current?.status === 'failed',
    retry: () => setAttempt((value) => value + 1),
    show: !!game && !!settings.advisor,
    settings,
    updateSettings,
    settingsPending,
    settingsError,
  };
}

export default function AgentAnalysis({
  game,
  report,
  pending,
  failed,
  retry,
}: {
  game: Game;
  report: PositionAnalysis | null;
  pending: boolean;
  failed: boolean;
  retry: () => void;
}) {
  const { t, locale } = useI18n();
  const sides = { attackers: t('Attackers'), defenders: t('Defenders') };
  const sourceLabels = {
    heuristic: t('Heuristic estimate'),
    mcts_rollout: t('MCTS rollouts'),
    neural_value: t('Neural value head'),
    neural_search: t('Neural search estimate'),
    terminal: t('Exact outcome'),
    unavailable: t('Unavailable'),
  };
  const number = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 });
  return (
    <section
      className="panel agent-analysis"
      aria-label={t('Agent evaluations')}
      aria-busy={pending}
    >
      <h2>{t('Winning chances')}</h2>
      <>
        <p className="analysis-perspective">
          {t('For the current player: {side}', { side: sides[game.turn] })}
        </p>
        {pending && (
          <p className="analysis-status" role="status">
            <i className="analysis-spinner" />
            {t('Evaluating this position…')}
          </p>
        )}
        {failed ? (
          <div className="analysis-status">
            <p>{t('The evaluation is unavailable. You can keep playing.')}</p>
            <button className="text-button" onClick={retry}>
              {t('Retry evaluation')}
            </button>
          </div>
        ) : (!report || report.unavailable_reason === 'not_recorded') && !pending ? (
          <p className="subtle">{t('No evaluation was recorded for this position.')}</p>
        ) : (
          (report?.evaluations ?? []).map((agent) => (
            <article className="agent-estimate" key={agent.agent_id}>
              <div className="agent-estimate-heading">
                <div>
                  <small>
                    {agent.role === 'advisor'
                      ? t('Move advisor')
                      : t('{side} agent', { side: sides[agent.role] })}
                  </small>
                  <strong>
                    <Rune rune={agent.rune} />
                    {agent.model_name ?? agent.name}
                  </strong>
                </div>
                <b>{agent.win_probability === null ? '—' : number.format(agent.win_probability)}</b>
              </div>
              {agent.win_probability !== null && (
                <div
                  className="estimate-meter"
                  role="meter"
                  aria-label={t('{agent} estimate for {side}', {
                    agent: agent.model_name ?? agent.name,
                    side: sides[game.turn],
                  })}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(agent.win_probability * 1000) / 10}
                  aria-valuetext={number.format(agent.win_probability)}
                >
                  <span style={{ width: `${agent.win_probability * 100}%` }} />
                </div>
              )}
              <small className="estimate-source">
                {sourceLabels[agent.source]}
                {agent.simulations !== undefined
                  ? ` · ${t('{count} simulations', { count: agent.simulations })}`
                  : ''}
                {agent.completed_depth !== undefined
                  ? ` · ${
                      (agent.config.algorithm === 'alpha_beta' ||
                        agent.config.search_wrapper === 'alpha_beta') &&
                      agent.config.depth !== undefined
                        ? t('depth {completed} / {requested}', {
                            completed: agent.completed_depth,
                            requested: Number(agent.config.depth),
                          })
                        : t('depth {depth}', { depth: agent.completed_depth })
                    }`
                  : ''}
              </small>
              {!pending &&
                (agent.config.algorithm === 'alpha_beta' ||
                  agent.config.search_wrapper === 'alpha_beta') &&
                agent.completed_depth !== undefined &&
                agent.completed_depth < Number(agent.config.depth) && (
                  <p className="subtle">
                    {t('The time limit was reached before the configured depth finished.')}
                  </p>
                )}
              {agent.unavailable_reason && (
                <p className="subtle">
                  {agent.unavailable_reason === 'model_unavailable'
                    ? t('The model is no longer available. Saved evaluations remain in replay.')
                    : t('This agent could not evaluate the position.')}
                </p>
              )}
            </article>
          ))
        )}
      </>
      {report?.hint && (
        <p className="analysis-hint">
          <span aria-hidden="true">↗</span>
          {t('Suggested move: {from} → {to}', {
            from: coordinate(report.hint.move.source, game.size),
            to: coordinate(report.hint.move.target, game.size),
          })}
        </p>
      )}
      <p className="analysis-explanation">
        {t('Agent estimates; draws count as half. These are not calibrated winning probabilities.')}
      </p>
    </section>
  );
}
