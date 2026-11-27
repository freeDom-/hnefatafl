import { useState } from 'react';
import { coordinate } from './Board';
import { useI18n } from './i18n';
import type { PositionAnalysis } from './analysisTypes';
import type { Game } from './types';
import Rune from './Rune';
import './analysis.css';
import { useCompactPlay } from './mobilePlay';

export default function AgentAnalysis({
  game,
  report,
  pending,
  failed,
  retry,
  waiting = false,
  local = false,
  stage,
  fallback = false,
  offline = false,
  analysisError,
  downloadProgress,
  cancel,
  cancelled = false,
}: {
  game: Game;
  report: PositionAnalysis | null;
  pending: boolean;
  failed: boolean;
  retry: () => void;
  waiting?: boolean;
  local?: boolean;
  stage?: string;
  fallback?: boolean;
  offline?: boolean;
  analysisError?: string;
  downloadProgress?: { transferred: number; total: number };
  cancel?: () => void;
  cancelled?: boolean;
}) {
  const { t, locale } = useI18n();
  const compact = useCompactPlay();
  const [selectedAgent, setSelectedAgent] = useState('advisor');
  const evaluations = report?.evaluations ?? [];
  const selected = evaluations.find((agent) => agent.agent_id === selectedAgent) ?? evaluations[0];
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
  const failureMessages: Record<string, string> = {
    'unsupported-agent': t('This advisor cannot run on this device.'),
    'unsupported-budget': t('This analysis setting is not available on this device.'),
    'model-unavailable': t('A compatible model is not available for this advisor.'),
    'browser-unsupported': t('Local analysis is not available in this browser.'),
    busy: t('Analysis is already running on this device.'),
    initialization: t('The local analysis engine could not start.'),
    download: t('Required analysis resources could not be downloaded.'),
    integrity: t('Analysis resources failed a safety check.'),
    offline: t('This position is not available offline yet.'),
    unauthorized: t('Analysis authorization is no longer valid.'),
    cancelled: t('Analysis was cancelled.'),
    'policy-changed': t('Analysis settings changed. Retry to use the updated policy.'),
    'revision-stale': t('Analysis settings changed. Retry to use the updated policy.'),
    'stale-game': t('This game changed. Reload it before analyzing again.'),
    'server-busy': t('Analysis capacity is busy. Retry shortly.'),
    'server-failed': t('The analysis service is unavailable.'),
  };
  const pendingMessages: Record<string, string> = {
    preparing: t('Preparing analysis…'),
    downloading: t('Downloading required analysis resources…'),
    initializing: t('Starting the analysis engine…'),
    'loading-model': t('Loading the analysis model…'),
    searching: t('Evaluating this position…'),
    fallback: t('Waiting for analysis capacity…'),
  };
  return (
    <section
      className={`panel agent-analysis${compact ? ' compact-analysis' : ''}`}
      aria-label={t('Agent evaluations')}
      aria-busy={pending}
    >
      {!compact && <h2>{t('Winning chances')}</h2>}
      <>
        <p className="analysis-perspective">
          {t('For the current player: {side}', { side: sides[game.turn] })}
        </p>
        {pending && (
          <p className="analysis-status" role="status">
            <i className="analysis-spinner" />
            {waiting
              ? t('Waiting for analysis capacity…')
              : fallback
                ? t('Running analysis on the server…')
                : (stage && pendingMessages[stage]) || t('Evaluating this position…')}
          </p>
        )}
        {!failed && !cancelled && local && !fallback && (
          <p className="subtle">
            {pending
              ? offline
                ? t('Analysis is running on this device without a network connection.')
                : t('Analysis runs on this device.')
              : offline
                ? t('Analysis completed on this device offline.')
                : t('Analysis completed on this device.')}
          </p>
        )}
        {!failed && !cancelled && fallback && report?.status === 'complete' && !pending && (
          <p className="subtle">{t('Analysis completed on the server.')}</p>
        )}
        {downloadProgress && downloadProgress.total > 0 && pending && stage === 'downloading' && (
          <label className="subtle">
            {t('Downloading analysis resources…')}
            <progress max={downloadProgress.total} value={downloadProgress.transferred} />
          </label>
        )}
        {pending && local && !fallback && cancel && (
          <button className="text-button" onClick={cancel}>
            {t('Cancel analysis')}
          </button>
        )}
        {cancelled ? (
          <div className="analysis-status">
            <p>{t('Analysis was cancelled.')}</p>
            <button className="text-button" onClick={retry}>
              {t('Retry evaluation')}
            </button>
          </div>
        ) : !pending && (failed || report?.status === 'partial') ? (
          <div className="analysis-status">
            <p>
              {report?.status === 'partial'
                ? t('The analysis could not be completed. Retry to try again.')
                : t('The evaluation is unavailable. You can keep playing.')}
            </p>
            {analysisError && (
              <p className="subtle">
                {failureMessages[analysisError] ??
                  t('The analysis could not be completed. Retry to try again.')}
              </p>
            )}
            <button className="text-button" onClick={retry}>
              {t('Retry evaluation')}
            </button>
          </div>
        ) : (!report || report.unavailable_reason === 'not_recorded') && !pending ? (
          <p className="subtle">{t('No evaluation was recorded for this position.')}</p>
        ) : null}
        {!cancelled &&
          (!failed || report?.status === 'unavailable') &&
          (compact ? (selected ? [selected] : []) : evaluations).map((agent) => (
            <article className="agent-estimate" key={agent.agent_id}>
              <div className="agent-estimate-heading">
                <div>
                  {compact && evaluations.length > 1 ? (
                    <select
                      aria-label={t('Agent evaluations')}
                      value={selected?.agent_id}
                      onChange={(event) => setSelectedAgent(event.target.value)}
                    >
                      {evaluations.map((entry) => (
                        <option key={entry.agent_id} value={entry.agent_id}>
                          {entry.role === 'advisor' ? t('Move advisor') : sides[entry.role]} ·{' '}
                          {entry.model_name ?? entry.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <>
                      <small>
                        {agent.role === 'advisor'
                          ? t('Move advisor')
                          : t('{side} agent', { side: sides[agent.role] })}
                      </small>
                      <strong>
                        <Rune rune={agent.rune} />
                        {agent.model_name ?? agent.name}
                      </strong>
                    </>
                  )}
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
          ))}
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
