import { useEffect, useState } from 'react';
import { api } from './api';
import { coordinate } from './Board';
import { useI18n } from './i18n';
import type { PositionAnalysis } from './analysisTypes';
import type { Game } from './types';
import './analysis.css';

/** Each request is tied to a game revision; late results cannot replace another position. */
export function useGameAnalysis(game: Game | null, visible: boolean) {
  const key = game ? `${game.id}:${game.version}:${game.cursor}` : '';
  const needed =
    !!game &&
    !!game.analysis_enabled &&
    (!!game.advisor || Object.values(game.players).some((player) => player.kind === 'ai'));
  const stored = game?.analysis?.cursor === game?.cursor ? game?.analysis : null;
  const [request, setRequest] = useState<{
    key: string;
    status: 'pending' | 'done' | 'failed';
    report?: PositionAnalysis;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!game || !visible || !needed || stored) return;
    let alive = true;
    setRequest({ key, status: 'pending' });
    void api<PositionAnalysis>(`/games/${game.id}/analysis`, {
      version: game.version,
      cursor: game.cursor,
    })
      .then((report) => {
        if (alive) setRequest({ key, status: 'done', report });
      })
      .catch(() => {
        if (alive) setRequest({ key, status: 'failed' });
      });
    return () => {
      alive = false;
    };
  }, [key, visible, needed, stored, attempt]);
  const current = request?.key === key ? request : null;
  return {
    report: stored ?? current?.report ?? null,
    pending: needed && !stored && (!current || current.status === 'pending'),
    failed: current?.status === 'failed',
    retry: () => setAttempt((value) => value + 1),
    show:
      !!game &&
      (!!game.advisor ||
        !!stored ||
        Object.values(game.players).some((player) => player.kind === 'ai')),
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
      <p className="eyebrow">{t('THROUGH THE AGENT’S EYES')}</p>
      <h2>{t('Winning chances')}</h2>
      <p className="analysis-perspective">
        {t('For the current player: {side}', { side: sides[game.turn] })}
      </p>
      {pending ? (
        <p className="analysis-status" role="status">
          <i className="analysis-spinner" />
          {t('Evaluating this position…')}
        </p>
      ) : failed ? (
        <div className="analysis-status">
          <p>{t('The evaluation is unavailable. You can keep playing.')}</p>
          <button className="text-button" onClick={retry}>
            {t('Retry evaluation')}
          </button>
        </div>
      ) : !report || report.unavailable_reason === 'not_recorded' ? (
        <p className="subtle">{t('No evaluation was recorded for this position.')}</p>
      ) : (
        report?.evaluations.map((agent) => (
          <article className="agent-estimate" key={agent.agent_id}>
            <div className="agent-estimate-heading">
              <div>
                <small>
                  {agent.role === 'advisor'
                    ? t('Move advisor')
                    : t('{side} agent', { side: sides[agent.role] })}
                </small>
                <strong>{agent.name}</strong>
              </div>
              <b>{agent.win_probability === null ? '—' : number.format(agent.win_probability)}</b>
            </div>
            {agent.win_probability !== null && (
              <div
                className="estimate-meter"
                role="meter"
                aria-label={t('{agent} estimate for {side}', {
                  agent: agent.name,
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
                ? ` · ${t('depth {depth}', { depth: agent.completed_depth })}`
                : ''}
            </small>
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
