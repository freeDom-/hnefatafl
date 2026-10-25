import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import type { Job } from './types';
import { opponentName } from './EvaluationSettings';
import { trainingNumberFields } from './TrainingFields';
import TrainingDataDisclosure from './TrainingDataDisclosure';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingMetricChart from './TrainingMetricChart';
import TrainingPerformance from './TrainingPerformance';
import { PhaseTimings } from './TrainingPhaseTimings';
import TrainingEvaluationStatus, { type Selection } from './TrainingEvaluationStatus';
import {
  evaluationGroups,
  finite,
  latestMetrics,
  latestPerformance,
  object,
  safeDiagnostics,
  type Values,
  type HistoryPoint,
} from './trainingDashboardData';

export function TrainingKpi({
  label,
  value,
  detail,
}: {
  label: string;
  value: unknown;
  detail?: string;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="training-kpi">
      <dt>{t(label)}</dt>
      <dd>
        {finite(value)
          ? new Intl.NumberFormat(locale, { maximumSignificantDigits: 7 }).format(value)
          : typeof value === 'string'
            ? value
            : '—'}
      </dd>
      {detail && <small>{detail}</small>}
    </div>
  );
}
export function TrainingJobOverview({
  job,
  progress,
  history,
}: {
  job: Job;
  progress: Values;
  history: HistoryPoint[];
}) {
  const { t, locale } = useI18n();
  const metrics = latestMetrics(progress);
  const last = history.at(-1);
  const selection = object(progress.evaluation_selection) as Selection | undefined;
  const evaluation =
    selection?.last_evaluation ?? [...history].reverse().find((row) => row.evaluation)?.evaluation;
  const legacy = [...history].reverse().find((row) => finite(row.evaluation_score));
  const score = evaluation?.score ?? legacy?.evaluation_score;
  const opponent =
    evaluation?.opponent ??
    legacy?.evaluation_opponent ??
    legacy?.opponent_identity?.split(':')[0] ??
    'shield_bearer';
  const performance = latestPerformance(progress, history);
  const completedPerformance = object(last?.performance);
  const groups = evaluationGroups(history);
  const trials = Array.isArray(progress.trials)
    ? progress.trials.map(object).filter((row): row is Values => !!row)
    : [];
  const iteration = progress.iteration;
  const total = progress.total_iterations ?? job.config?.iterations;
  const ratio = finite(score)
    ? new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(score)
    : undefined;
  return (
    <div className="training-overview">
      {job.kind === 'tune' && (
        <section className="tuning-summary" aria-label={t('Study progress')}>
          <h4>{String(job.config?.study_name ?? t('Tune parameters'))}</h4>
          <dl className="training-kpis">
            <TrainingKpi label="Completed trials" value={progress.completed_trials} />
            <TrainingKpi
              label="Additional trial budget"
              value={progress.trials_requested ?? job.config?.trials}
            />
            <TrainingKpi label="Best trial score" value={progress.best_score} />
          </dl>
          {finite(progress.trial) && (
            <p>
              {t('Current trial number')}: {progress.trial}
            </p>
          )}
          {!!trials.length && (
            <TrainingDataDisclosure title="Trial history">
              {() => (
                <div className="table-scroll bounded-table">
                  <table aria-label={t('Trial history')}>
                    <thead>
                      <tr>
                        {['Trial', 'Status', 'Evaluation score', 'Parameters'].map((label) => (
                          <th key={label} scope="col">
                            {t(label)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {trials.map((row, index) => (
                        <tr key={index}>
                          <th scope="row">{String(row.number ?? '—')}</th>
                          <td>{String(row.state ?? '—')}</td>
                          <td>
                            {finite(row.score)
                              ? new Intl.NumberFormat(locale, {
                                  maximumFractionDigits: 3,
                                }).format(row.score)
                              : '—'}
                          </td>
                          <td>
                            <code>{JSON.stringify(safeDiagnostics(row.parameters))}</code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TrainingDataDisclosure>
          )}
        </section>
      )}

      <dl className="training-kpis">
        <TrainingKpi
          label="Iteration"
          value={`${finite(iteration) ? iteration : '—'} / ${finite(total) ? total : '—'}`}
          detail={t('Recorded training progress')}
        />
        <TrainingKpi
          label="Policy loss"
          value={last?.policy_loss ?? metrics.policy_loss}
          detail={t('Last completed iteration')}
        />
        <TrainingKpi
          label="Value loss"
          value={last?.value_loss ?? metrics.value_loss}
          detail={t('Last completed iteration')}
        />
        <TrainingKpi
          label="Latest evaluation"
          value={ratio}
          detail={
            finite(score)
              ? `${t(opponentName(opponent))} · ${t('Iteration')} ${evaluation?.iteration ?? legacy?.iteration ?? '—'}`
              : t('No evaluation recorded yet.')
          }
        />
        <TrainingKpi
          label="Simulations / second"
          value={
            completedPerformance?.simulations_per_second ?? performance?.simulations_per_second
          }
          detail={t('Self-play throughput')}
        />
        <TrainingKpi
          label="Iteration duration"
          value={
            finite(last?.iteration_duration ?? completedPerformance?.iteration_wall_seconds)
              ? `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format((last?.iteration_duration ?? completedPerformance?.iteration_wall_seconds) as number)} s`
              : undefined
          }
          detail={t('Last completed iteration')}
        />
      </dl>
      {(job.status === 'failed' || job.status === 'cancelled') && (
        <p className="dashboard-message">
          {t(
            job.status === 'failed'
              ? 'Training failed. Inspect the error above and the diagnostic log in Details.'
              : 'Training stopped. Recorded iterations remain available; resume a full checkpoint to continue.',
          )}
        </p>
      )}
      {!finite(score) && (
        <p className="dashboard-message">
          {t('No evaluation recorded yet.')}{' '}
          {t('Evaluation runs at the configured interval and after the final optimized iteration.')}
        </p>
      )}
      {evaluation?.benchmark_saturated && (
        <p className="dashboard-message" role="status">
          {t(
            'This benchmark is near saturation and provides little discrimination. Consider a stronger evaluation opponent.',
          )}
        </p>
      )}
      {evaluation?.promoted !== undefined && (
        <p className="dashboard-message">
          {t(
            evaluation.promoted
              ? 'Candidate promoted to champion.'
              : 'Champion retained; improvement was not established.',
          )}
        </p>
      )}
      {(evaluation?.truncated ?? legacy?.evaluation_truncated ?? 0) > 0 && (
        <p className="dashboard-message">
          {t(
            'Unfinished evaluation games earn zero points and are separate from rules draws. Review Evaluation for completion counts.',
          )}
        </p>
      )}
      <TrainingHistoryChart history={history} compact />
      <p className="overview-context subtle">
        {t(
          'Loss measures fit to self-play targets; evaluation measures play against a specific opponent.',
        )}
        {groups.length > 1 &&
          ` ${t('Overview merges all recorded evaluation series. Compare individual settings in Evaluation.')}`}
      </p>
      <div className="overview-bottom">
        <PhaseTimings performance={completedPerformance} />
        <div className="checkpoint-summary">
          <h4>{t('Checkpoint selection')}</h4>
          <p>
            {t('Accepted champion iteration')}:{' '}
            <strong>{selection?.champion_iteration ?? '—'}</strong>
          </p>
          {!!selection?.benchmarks?.length && (
            <p>
              {t('Best fixed-benchmark checkpoints')}:{' '}
              {selection.benchmarks
                .map(
                  (best) =>
                    `${t(opponentName(best.context.opponent))} · ${t('Iteration')} ${best.iteration}`,
                )
                .join('; ')}
            </p>
          )}
          {!selection && (
            <p className="subtle">
              {t('Checkpoint selection metadata was not recorded by this worker.')}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
export function TrainingLearningView({
  progress,
  history,
  config,
  range,
}: {
  progress: Values;
  history: HistoryPoint[];
  config?: Values;
  range: number;
}) {
  const { t } = useI18n();
  const last = history.at(-1);
  const metrics = latestMetrics(progress);
  const replay = useMemo(
    () => [
      { key: 'replay_positions', name: 'Replay buffer positions', color: 'var(--chart-value)' },
    ],
    [],
  );
  return (
    <>
      <dl className="training-kpis learning-kpis">
        <TrainingKpi
          label="Replay buffer positions"
          value={last?.replay_positions ?? metrics.replay_positions}
        />
        <TrainingKpi
          label="Optimizer updates"
          value={last?.optimizer_updates ?? last?.updates ?? metrics.updates}
        />
        {[
          'optimizer',
          'learning_rate',
          'batch_size',
          'epochs',
          'channels',
          'blocks',
          'replay_buffer_size',
        ]
          .filter((key) => config?.[key] !== undefined)
          .map((key) => (
            <TrainingKpi
              key={key}
              label={
                trainingNumberFields[key]?.[0] ??
                (key === 'optimizer' ? 'Optimizer' : key.replaceAll('_', ' '))
              }
              value={config![key]}
            />
          ))}
      </dl>
      <TrainingHistoryChart history={history} view="learning" range={range} />
      {history.some((row) => finite(row.replay_positions)) && (
        <TrainingMetricChart
          title="Replay buffer positions"
          rows={history}
          series={replay}
          unit="Positions"
        />
      )}
      <p className="subtle">
        {t('Missing metrics were not recorded; they are never interpreted as zero.')}
      </p>
    </>
  );
}
export function TrainingEvaluationView({
  progress,
  history,
  range,
}: {
  progress: Values;
  history: HistoryPoint[];
  range: number;
}) {
  const { t } = useI18n();
  const selection = object(progress.evaluation_selection);
  const last = [...history].reverse().find((row) => row.evaluation)?.evaluation;
  return (
    <>
      <TrainingEvaluationStatus
        progress={
          selection
            ? progress
            : last
              ? {
                  ...progress,
                  evaluation_selection: {
                    opponent: last.opponent ?? 'shield_bearer',
                    last_evaluation: last,
                  },
                }
              : progress
        }
      />
      {!selection && !last && (
        <p className="dashboard-message">
          {t(
            'Effective evaluation settings were not recorded. Historical results remain available below.',
          )}
        </p>
      )}
      <p className="subtle">
        {t(
          'A high score from a small sample does not establish absolute superiority. Confirm strength with larger, held-out tournaments.',
        )}
      </p>
      <TrainingHistoryChart history={history} view="evaluation" range={range} selectable />
    </>
  );
}
export function DiagnosticDisclosure({ title, value }: { title: string; value: unknown }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState('');
  const content = open
    ? ((typeof value === 'string' ? value : JSON.stringify(safeDiagnostics(value), null, 2)) ?? '—')
    : '';
  return (
    <details
      className="diagnostic-disclosure"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{t(title)}</summary>
      {open && (
        <>
          <button
            className="button secondary small"
            onClick={async () => {
              try {
                if (!navigator.clipboard) throw new Error('Clipboard unavailable');
                await navigator.clipboard.writeText(content);
                setCopied(t('Copied'));
              } catch {
                setCopied(t('Copy failed. Select and copy the text manually.'));
              }
            }}
          >
            {t('Copy')} {t(title)}
          </button>
          <span role="status">{copied}</span>
          <pre className="log-view">{content}</pre>
        </>
      )}
    </details>
  );
}
export function TrainingJobDetails({ job, progress }: { job: Job; progress: Values }) {
  const { t } = useI18n();
  return (
    <div className="training-details">
      <DiagnosticDisclosure title="Training configuration" value={job.config} />
      <DiagnosticDisclosure
        title="Complete evaluation settings"
        value={object(progress.evaluation_selection) ?? object(job.config)?.evaluation_opponent}
      />
      <DiagnosticDisclosure
        title="Raw progress and diagnostics"
        value={job.progress ?? job.metrics}
      />
      <DiagnosticDisclosure
        title="Training log"
        value={Array.isArray(job.logs) ? job.logs.join('\n') : job.logs || t('No log output yet.')}
      />
      <DiagnosticDisclosure
        title="Checkpoint details"
        value={{
          checkpoint: job.checkpoint,
          checkpoint_path: job.checkpoint_path,
          resumable: job.resumable,
          model_id: job.model_id,
          best_models: progress.best_models,
          result: job.result,
        }}
      />
      <DiagnosticDisclosure
        title="Execution and remote-worker details"
        value={{
          execution: job.execution ?? 'local',
          worker_id: job.worker_id,
          target_worker_id: job.target_worker_id,
          worker_name: job.worker_name,
          software: job.software,
          last_reported_at: job.last_reported_at,
          lease_expires_at: job.lease_expires_at,
          created_at: job.created_at,
        }}
      />
      <details>
        <summary>{t('Export and import')}</summary>
        <p>
          {t(
            'Export an inactive job from its header to retain its configuration, history and available checkpoints. Use Import job to restore a portable job archive. Registered models are managed separately in Model collection.',
          )}
        </p>
      </details>
    </div>
  );
}
export { TrainingPerformance };
