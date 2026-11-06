import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import type { Job } from './types';
import { opponentName } from './EvaluationSettings';
import { trainingNumberFields, trainingParameterLabel } from './TrainingFields';
import TrainingDataDisclosure from './TrainingDataDisclosure';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingMetricChart from './TrainingMetricChart';
import TrainingPerformance from './TrainingPerformance';
import TuningCharts from './TuningCharts';
import { PhaseTimings } from './TrainingPhaseTimings';
import TrainingEvaluationStatus, { type Selection } from './TrainingEvaluationStatus';
import {
  evaluationGroups,
  finite,
  jobName,
  latestMetrics,
  latestPerformance,
  object,
  recentHistory,
  safeDiagnostics,
  withinIterationWindow,
  type Values,
  type HistoryPoint,
} from './trainingDashboardData';
import { formatLearningRate } from './trainingLiveProgress';

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
  const { t, locale } = useI18n();
  const last = history.at(-1);
  const metrics = latestMetrics(progress);
  const adaptation = object(progress.adaptation) ?? {};
  const effectiveRate = finite(adaptation.effective_learning_rate)
    ? adaptation.effective_learning_rate
    : last?.learning_rate;
  const replay = useMemo(
    () => [
      { key: 'replay_positions', name: 'Replay buffer positions', color: 'var(--chart-value)' },
    ],
    [],
  );
  // The dashboard hands the view the full history and the selected window, so
  // every metric and the adaptation log share the same iteration boundaries.
  const windowedHistory = useMemo(() => recentHistory(history, range), [history, range]);
  return (
    <>
      <dl className="training-kpis learning-kpis">
        {finite(effectiveRate) && (
          <TrainingKpi
            label="Effective learning rate"
            value={formatLearningRate(effectiveRate, locale)}
          />
        )}
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
              value={
                key === 'learning_rate' ? formatLearningRate(config![key], locale) : config![key]
              }
            />
          ))}
      </dl>
      <TrainingHistoryChart history={windowedHistory} view="learning" range={0} />
      {windowedHistory.some((row) => finite(row.replay_positions)) && (
        <TrainingMetricChart
          title="Replay buffer positions"
          rows={windowedHistory}
          series={replay}
          unit="Positions"
        />
      )}
      <p className="subtle">
        {t('Missing metrics were not recorded; they are never interpreted as zero.')}
      </p>
      <AdaptationHistory progress={progress} history={history} range={range} />
    </>
  );
}

const SKIP_LABELS: Record<string, string> = {
  warmup: 'Warm-up',
  cadence: 'Waiting for cadence',
  dwell: 'Minimum dwell',
  fallback: 'Fallback',
};

function AdaptationDecision({ event, t }: { event: Values; t: (source: string) => string }) {
  if (event.skipped === 'missing_observation') return <>{t('No observation')}</>;
  if (typeof event.skipped === 'string' && SKIP_LABELS[event.skipped])
    return <>{t(SKIP_LABELS[event.skipped])}</>;
  return <>{event.changed === true ? t('Changed') : t('Kept')}</>;
}

/**
 * Bounded controller decision log. Older jobs have no events and render nothing,
 * so a legacy checkpoint never shows an invented history. The log follows the
 * selected iteration window, anchored to the latest recorded training iteration
 * so sparse decisions are never mistaken for the end of the run.
 */
function AdaptationHistory({
  progress,
  history,
  range,
}: {
  progress: Values;
  history: HistoryPoint[];
  range: number;
}) {
  const { t, locale } = useI18n();
  const raw = progress.adaptation_events;
  const events = useMemo(
    () => (Array.isArray(raw) ? raw.filter((event): event is Values => !!object(event)) : []),
    [raw],
  );
  // Only well-formed events can join a numeric window; malformed historical
  // records are still shown under All iterations with a missing iteration.
  const recorded = useMemo(
    () =>
      events.filter((event): event is Values & { iteration: number } => finite(event.iteration)),
    [events],
  );
  const anchor = history.at(-1)?.iteration ?? recorded.at(-1)?.iteration;
  const windowed = useMemo(
    () => (range ? withinIterationWindow(recorded, range, anchor) : events),
    [range, recorded, events, anchor],
  );
  if (!windowed.length) return null;
  const rate = (value: unknown) => formatLearningRate(value, locale) ?? '—';
  const observation = (value: unknown) =>
    value === null || value === undefined
      ? t('Unavailable')
      : finite(value)
        ? new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value)
        : '—';
  return (
    <TrainingDataDisclosure title="Automatic hyperparameter updates">
      {() => (
        <div className="table-scroll bounded-table">
          <table aria-label={t('Automatic hyperparameter updates')}>
            <thead>
              <tr>
                {[
                  'Iteration',
                  'Used learning rate',
                  'Selected learning rate',
                  'Observation',
                  'Decision',
                ].map((label) => (
                  <th key={label} scope="col">
                    {t(label)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {windowed.map((event, index) => (
                <tr key={index}>
                  <th scope="row">{finite(event.iteration) ? String(event.iteration) : '—'}</th>
                  <td>{rate(event.previous_learning_rate)}</td>
                  <td>{rate(event.selected_learning_rate)}</td>
                  <td>{observation(event.observation)}</td>
                  <td>
                    <AdaptationDecision event={event} t={t} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </TrainingDataDisclosure>
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
export function TrainingTuningView({
  job,
  progress,
  trainingJobs,
  busy,
  onUseBestParameters,
  onContinueTrial,
  onApplyToJob,
  onDownloadBestConfig,
}: {
  job: Job;
  progress: Values;
  trainingJobs: Job[];
  busy: boolean;
  onUseBestParameters: () => void;
  onContinueTrial: (additionalIterations: number) => void;
  onApplyToJob: (targetJobId: string, additionalIterations: number) => void;
  onDownloadBestConfig: () => void;
}) {
  const { t, locale } = useI18n();
  const trials = useMemo(
    () =>
      (Array.isArray(progress.trials)
        ? progress.trials.map(object).filter((row): row is Values => !!row)
        : []) as Values[],
    [progress.trials],
  );
  const bestConfig = object(progress.best_configuration);
  const verification = object(progress.verification_result);
  const bestOpponents = object(progress.best_opponents);
  const sourceConfig = object(progress.source_configuration);
  const comparison = useMemo(() => {
    if (!bestConfig || !sourceConfig) return [];
    const keys = Array.from(new Set([...Object.keys(sourceConfig), ...Object.keys(bestConfig)]))
      .filter((key) => !['output_dir', 'registry_dir', 'storage', 'resume'].includes(key))
      .sort();
    return keys.map((key) => ({
      key,
      source: sourceConfig[key],
      winning: bestConfig[key],
      changed: JSON.stringify(sourceConfig[key]) !== JSON.stringify(bestConfig[key]),
    }));
  }, [bestConfig, sourceConfig]);
  const [additional, setAdditional] = useState(5);
  const [targetJob, setTargetJob] = useState('');
  const [sortKey, setSortKey] = useState<'number' | 'score' | 'duration'>('number');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(0);
  const bestTrial = finite(progress.best_trial) ? progress.best_trial : undefined;
  const statuses = useMemo(
    () => Array.from(new Set(trials.map((row) => String(row.state ?? '—')))).sort(),
    [trials],
  );
  const rows = useMemo(() => {
    const filtered =
      statusFilter === 'all'
        ? trials
        : trials.filter((row) => String(row.state ?? '—') === statusFilter);
    const key = (row: Values) => {
      if (sortKey === 'score')
        return row.state === 'COMPLETE' && finite(row.score) ? row.score : -Infinity;
      if (sortKey === 'duration')
        return finite(row.duration) ? (row.duration as number) : -Infinity;
      return finite(row.number) ? (row.number as number) : 0;
    };
    return [...filtered].sort((a, b) => key(b) - key(a));
  }, [trials, sortKey, statusFilter]);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 50) - 1));
  const pageRows = rows.slice(currentPage * 50, (currentPage + 1) * 50);
  const weighted = !!bestOpponents || trials.some((row) => finite(row.aggregate_score));
  const number = (value: unknown) =>
    finite(value) ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value) : '—';
  const candidates = trainingJobs.filter(
    (entry) =>
      entry.kind !== 'tune' &&
      !['running', 'pending', 'queued', 'cancelling'].includes(entry.status) &&
      entry.checkpoint &&
      entry.resumable !== false,
  );
  return (
    <div className="training-tuning">
      <section aria-label={t('Study progress')}>
        <h4>{String(progress.study_name ?? job.config?.study_name ?? t('Tune parameters'))}</h4>
        <dl className="training-kpis">
          <TrainingKpi label="Completed trials" value={progress.completed_trials} />
          <TrainingKpi label="Failed trials" value={progress.failed_trials} />
          <TrainingKpi label="Best trial" value={progress.best_trial} />
          <TrainingKpi label="Best trial score" value={progress.best_score} />
          <TrainingKpi label="Best checkpoint iteration" value={progress.best_iteration} />
          <TrainingKpi label="Study sampler seed" value={progress.sampler_seed} />
          <TrainingKpi label="Source training seed" value={progress.source_seed} />
          <TrainingKpi label="Source checkpoint best score" value={progress.source_best_score} />
          <TrainingKpi
            label="Winning checkpoint source"
            value={progress.best_source ? t(String(progress.best_source)) : undefined}
          />
        </dl>
        <p className="subtle">
          {t(
            'Optuna optimizes the objective score: the best newly evaluated candidate benchmark, or its weighted aggregate when fixed opponents are configured. Historical source scores are separate references.',
          )}
        </p>
        {!progress.objective_policy && (
          <p className="subtle">
            {t(
              'Historical objective policy: retained trial scores and rankings are shown unchanged. Further trials require verified study compatibility.',
            )}
          </p>
        )}
        {finite(progress.source_best_score) &&
          finite(progress.best_score) &&
          progress.source_best_score > progress.best_score &&
          !weighted && (
            <p className="dashboard-message" role="status">
              {t(
                'The source benchmark is higher than the winning candidate benchmark. The objective still measures the newly trained candidate; the source remains unchanged.',
              )}
            </p>
          )}
      </section>

      {comparison.length > 0 && (
        <section aria-label={t('Source versus winning settings')}>
          <h4>{t('Source versus winning settings')}</h4>
          <div className="table-scroll bounded-table">
            <table aria-label={t('Source versus winning settings')}>
              <thead>
                <tr>
                  {['Setting', 'Source checkpoint', 'Winning trial', 'Change'].map((label) => (
                    <th key={label} scope="col">
                      {t(label)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">{t(trainingParameterLabel(row.key))}</th>
                    <td>{String(row.source ?? '—')}</td>
                    <td>{String(row.winning ?? '—')}</td>
                    <td>{t(row.changed ? 'Changed' : 'Unchanged')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!!bestOpponents && (
        <section aria-label={t('Weighted opponent aggregate')}>
          <h4>{t('Weighted opponent aggregate')}</h4>
          <div className="table-scroll bounded-table">
            <table aria-label={t('Weighted opponent aggregate')}>
              <thead>
                <tr>
                  {['Opponent', 'Score', 'Wins', 'Losses', 'Draws', 'Unfinished'].map((label) => (
                    <th key={label} scope="col">
                      {t(label)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(bestOpponents).map(([opponent, raw]) => {
                  const row = object(raw) ?? {};
                  return (
                    <tr key={opponent}>
                      <th scope="row">{t(opponentName(opponent))}</th>
                      <td>{number(row.score)}</td>
                      <td>{number(row.wins)}</td>
                      <td>{number(row.losses)}</td>
                      <td>{number(row.draws)}</td>
                      <td>{number(row.truncated)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!!progress.verification_status && (
        <section aria-label={t('Verification status')}>
          <TrainingKpi
            label="Verification status"
            value={t(String(progress.verification_status))}
          />
          {!!progress.verification_error && (
            <p className="form-error" role="status">
              {String(progress.verification_error)}
            </p>
          )}
        </section>
      )}
      {!!verification && (
        <section aria-label={t('Held-out verification')}>
          <h4>{t('Held-out verification')}</h4>
          <dl className="training-kpis">
            <TrainingKpi
              label="Verification opponent"
              value={t(opponentName(String(verification.opponent ?? 'shield_bearer')))}
            />
            <TrainingKpi label="Verification score" value={verification.score} />
            <TrainingKpi label="Verification games" value={verification.games} />
            <TrainingKpi label="Verification seed" value={verification.seed} />
          </dl>
          <p className="subtle">
            {t(
              'The verification stage uses a seed the trials never used, so it is an independent check of the winning checkpoint.',
            )}
          </p>
        </section>
      )}

      {!!bestConfig && (
        <section aria-label={t('Best configuration')}>
          <h4>{t('Best configuration')}</h4>
          <DiagnosticDisclosure title="Winning trial configuration" value={bestConfig} />
          <div className="tuning-actions">
            <button
              className="button secondary small"
              disabled={busy}
              onClick={onUseBestParameters}
            >
              {t('Train with best parameters')}
            </button>
            <button
              className="button secondary small"
              disabled={busy || !finite(progress.best_trial)}
              onClick={() => onContinueTrial(additional)}
            >
              {t('Continue winning trial')}
            </button>
            <button className="button secondary small" onClick={onDownloadBestConfig}>
              {t('Download best configuration')}
            </button>
            <label className="training-field">
              {t('Additional iterations')}
              <input
                type="number"
                min={1}
                max={100000}
                value={additional}
                onChange={(event) => setAdditional(Number(event.target.value))}
              />
            </label>
          </div>
          {!!candidates.length && (
            <div className="tuning-actions">
              <label className="training-field">
                {t('Apply to existing training')}
                <select value={targetJob} onChange={(event) => setTargetJob(event.target.value)}>
                  <option value="">{t('Select a training job')}</option>
                  {candidates.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {`${jobName(entry)} (${entry.id.slice(0, 8)})`}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button secondary small"
                disabled={busy || !targetJob}
                onClick={() => onApplyToJob(targetJob, additional)}
              >
                {t('Apply best parameters')}
              </button>
            </div>
          )}
        </section>
      )}

      {!!trials.length && (
        <TuningCharts trials={trials} importance={object(progress.parameter_importance)} />
      )}
      {!!trials.length && (
        <TrainingDataDisclosure title="Trial history">
          {() => (
            <>
              <div className="tuning-actions">
                <label className="training-field">
                  {t('Sort trials by')}
                  <select
                    value={sortKey}
                    onChange={(event) => {
                      setPage(0);
                      setSortKey(event.target.value as 'number' | 'score' | 'duration');
                    }}
                  >
                    <option value="number">{t('Trial number')}</option>
                    <option value="score">{t('Optuna objective score')}</option>
                    <option value="duration">{t('Duration')}</option>
                  </select>
                </label>
                <label className="training-field">
                  {t('Filter by status')}
                  <select
                    value={statusFilter}
                    onChange={(event) => {
                      setStatusFilter(event.target.value);
                      setPage(0);
                    }}
                  >
                    <option value="all">{t('All statuses')}</option>
                    {statuses.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="table-scroll bounded-table">
                <table aria-label={t('Trial history')}>
                  <thead>
                    <tr>
                      {[
                        'Trial',
                        'Status',
                        'Optuna objective score',
                        'Best in-training benchmark score',
                        'Final iteration score',
                        ...(weighted ? ['Weighted aggregate score'] : []),
                        'Best iteration',
                        'Source',
                        'Duration',
                        'Parameters',
                        'Details',
                      ].map((label) => (
                        <th key={label} scope="col">
                          {t(label)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((row, index) => (
                      <tr
                        key={String(row.number ?? index)}
                        className={row.number === bestTrial ? 'winning-trial' : undefined}
                      >
                        <th scope="row">{String(row.number ?? '—')}</th>
                        <td>{String(row.state ?? '—')}</td>
                        <td>{number(row.score)}</td>
                        <td>{number(row.best_score)}</td>
                        <td>{number(row.final_score)}</td>
                        {weighted && <td>{number(row.aggregate_score)}</td>}
                        <td>{number(row.best_iteration)}</td>
                        <td>{row.best_source ? t(String(row.best_source)) : '—'}</td>
                        <td>{finite(row.duration) ? `${number(row.duration)} s` : '—'}</td>
                        <td>
                          <code>{JSON.stringify(safeDiagnostics(row.parameters))}</code>
                        </td>
                        <td>
                          {row.error ? (
                            <span className="tuning-trial-error">{String(row.error)}</span>
                          ) : row.opponents ? (
                            <code>{JSON.stringify(safeDiagnostics(row.opponents))}</code>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <nav className="online-toolbar" aria-label={t('Trial pages')}>
                <button
                  type="button"
                  className="button secondary small"
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  {t('Previous page')}
                </button>
                <span>
                  {t('Page {page}', { page: currentPage + 1 })} · {rows.length} {t('trials')}
                </span>
                <button
                  type="button"
                  className="button secondary small"
                  disabled={(currentPage + 1) * 50 >= rows.length}
                  onClick={() => setPage(currentPage + 1)}
                >
                  {t('Next page')}
                </button>
                <button
                  type="button"
                  className="button secondary small"
                  disabled={bestTrial === undefined}
                  onClick={() => {
                    setStatusFilter('all');
                    setSortKey('score');
                    setPage(0);
                  }}
                >
                  {t('Locate winning trial')}
                </button>
              </nav>
              <p className="subtle">
                {t(
                  'The winning trial is highlighted. A small evaluation sample does not establish a statistically significant improvement.',
                )}
              </p>
            </>
          )}
        </TrainingDataDisclosure>
      )}
    </div>
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
