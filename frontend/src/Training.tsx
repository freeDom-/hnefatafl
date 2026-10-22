import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { Metric } from './Archive';
import type { DeviceInfo, Job, Model, Ruleset, TrainingWorker } from './types';
import { sortRulesets } from './sortRulesets';
import { suggestVikingName } from './vikingNames';
import RecordsConfirm from './RecordsConfirm';
import './features.css';
import './workshopManagement.css';
import { getLocale, t, useI18n } from './i18n';
import {
  trainingConfig,
  changeTrainingPlan,
  recordedTrainingConfig,
  type TrainingPreset,
} from './trainingPresets';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingResumeDialog from './TrainingResumeDialog';
import { numberField, SelfPlayModeField } from './TrainingFields';
import TrainingPerformance from './TrainingPerformance';
import Rune from './Rune';
import ModelRenameDialog from './ModelRenameDialog';
import ModelTrainingSummary from './ModelTrainingSummary';
import { modelIteration } from './modelLabel';
import TrainingWorkers from './TrainingWorkers';
import TrainingTarget, { targetRequest } from './TrainingTarget';

interface ModelUsage {
  model_id: string;
  unfinished_games: {
    id: string;
    version: number;
    ruleset: string;
    created_at: string;
    players: { attackers: { name: string }; defenders: { name: string } };
    total_moves: number;
  }[];
  running_tournaments: { id: string; name: string; status: string }[];
  usage_token?: string;
}

const defaults = trainingConfig('tablut', 'recommended');

const metricText = (value: unknown) =>
  typeof value === 'number'
    ? new Intl.NumberFormat(getLocale(), {
        minimumFractionDigits: Number.isInteger(value) ? 0 : 4,
        maximumFractionDigits: Number.isInteger(value) ? 0 : 4,
      }).format(value)
    : typeof value === 'string'
      ? value
      : '—';

export default function Training({
  rulesets,
  models,
  onModelsChanged,
  onError,
  onGamesDeleted,
}: {
  rulesets: Ruleset[];
  models: Model[];
  onModelsChanged: () => void;
  onError: (error: string) => void;
  onGamesDeleted?: (ids: string[]) => void;
}) {
  const { locale } = useI18n();
  const [device, setDevice] = useState<DeviceInfo>({});
  const [workers, setWorkers] = useState<TrainingWorker[]>([]);
  const [target, setTarget] = useState('local');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [resumeJob, setResumeJob] = useState<Job | null>(null);
  const [renameModel, setRenameModel] = useState<Model | null>(null);
  const [deleteJob, setDeleteJob] = useState<Job | null>(null);
  const [deletingJob, setDeletingJob] = useState(false);
  const [deleteJobError, setDeleteJobError] = useState('');
  const trainingForm = useRef<HTMLElement>(null);
  const [config, setConfig] = useState<Record<string, string | number | boolean>>(() => ({
    ...defaults,
    model_name: suggestVikingName(models.map((model) => model.name)),
  }));
  const [kind, setKind] = useState<'train' | 'tune'>('train');
  const [trials, setTrials] = useState(3);
  const [studyName, setStudyName] = useState('hnefatafl-search');
  const [space, setSpace] = useState(
    '{\n  "learning_rate": {"type": "float", "low": 0.0001, "high": 0.003, "log": true},\n  "mcts_iterations": {"type": "int", "low": 4, "high": 16}\n}',
  );
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [notice, setNotice] = useState('');
  const [deleteModel, setDeleteModel] = useState<Model | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [modelUsage, setModelUsage] = useState<ModelUsage | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState('');
  const [usageRevision, setUsageRevision] = useState(0);
  const [discardUnfinished, setDiscardUnfinished] = useState(false);
  const seenBestModels = useRef(new Set(models.map((model) => model.id)));
  const observedJobs = useRef<Map<string, string> | null>(null);
  const refreshRevision = useRef(0);
  const selectionRevision = useRef(0);
  const selectJob = (job: Job | null) => {
    selectionRevision.current++;
    setSelectedJob(job);
  };
  useEffect(() => {
    if (!observedJobs.current) return;
    let changed = false;
    for (const job of jobs) {
      const previous = observedJobs.current.get(job.id);
      if (previous !== job.status && job.status === 'completed') changed = true;
      observedJobs.current.set(job.id, job.status);
      if (job.model_id && !seenBestModels.current.has(job.model_id)) {
        seenBestModels.current.add(job.model_id);
        changed = true;
      }
      const retained = job.progress?.best_models;
      if (!Array.isArray(retained)) continue;
      for (const entry of retained) {
        if (
          entry &&
          typeof entry.model_id === 'string' &&
          !seenBestModels.current.has(entry.model_id)
        ) {
          seenBestModels.current.add(entry.model_id);
          changed = true;
        }
      }
    }
    if (changed) onModelsChanged();
  }, [jobs, onModelsChanged]);
  useEffect(() => {
    if (!deleteModel) return;
    let alive = true;
    setUsageLoading(true);
    setModelUsage(null);
    setUsageError('');
    setDiscardUnfinished(false);
    void api<ModelUsage>(`/models/${deleteModel.id}/usage`)
      .then((usage) => {
        if (!Array.isArray(usage.unfinished_games) || !Array.isArray(usage.running_tournaments))
          throw new Error('The model usage could not be checked. Try again before deleting it.');
        if (alive) setModelUsage(usage);
      })
      .catch((error) => {
        if (alive) setUsageError((error as Error).message);
      })
      .finally(() => {
        if (alive) setUsageLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [deleteModel, usageRevision]);
  const refresh = useCallback(async () => {
    const revision = ++refreshRevision.current;
    const selectedRevision = selectionRevision.current;
    const [hardware, runs, connected] = await Promise.all([
      api<DeviceInfo>('/training/device'),
      api<Job[]>('/training/jobs'),
      api<TrainingWorker[]>('/training/workers'),
    ]);
    if (revision !== refreshRevision.current) return;
    if (!observedJobs.current) {
      observedJobs.current = new Map(runs.map((job) => [job.id, job.status]));
    }
    setDevice(hardware);
    setJobs(runs);
    setWorkers(Array.isArray(connected) ? connected : []);
    if (selectionRevision.current === selectedRevision) {
      setSelectedJob((previous) =>
        previous ? (runs.find((job) => job.id === previous.id) ?? null) : null,
      );
    }
  }, []);
  useEffect(() => {
    void refresh().catch((e) => onError(e.message));
    return () => {
      refreshRevision.current++;
    };
  }, [refresh, onError]);
  useEffect(() => {
    if (
      !jobs.some((job) => ['running', 'pending', 'queued', 'cancelling'].includes(job.status)) &&
      !workers.some((worker) => worker.status !== 'revoked')
    )
      return;
    const timer = window.setInterval(() => void refresh().catch((e) => onError(e.message)), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, workers, refresh, onError]);
  const update = (key: string, value: string | number | boolean) =>
    setConfig((previous) => ({ ...previous, [key]: value }));
  const start = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setNotice('');
    try {
      const params =
        kind === 'tune'
          ? {
              kind,
              trials,
              study_name: studyName,
              search_space: JSON.parse(space),
              training: config,
            }
          : { ...config, kind, ...(target !== 'local' ? targetRequest(target) : {}) };
      const job = await api<Job>('/training/jobs', params);
      refreshRevision.current++;
      if (!observedJobs.current) observedJobs.current = new Map();
      observedJobs.current.set(job.id, job.status);
      setJobs((previous) => [job, ...previous]);
      selectJob(job);
      setNotice(
        t(
          job.execution === 'remote'
            ? 'Training queued. An available remote worker will pick it up.'
            : 'Your training job has started. You can keep playing while it runs.',
        ),
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async (id: string) => {
    const revision = selectionRevision.current;
    try {
      const job = await api<Job>(`/training/jobs/${id}/cancel`, {});
      if (selectionRevision.current === revision) selectJob(job);
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const resume = async (job: Job) => {
    if (job.kind === 'tune') {
      setBusy(true);
      try {
        const saved = job.config ?? {};
        const training =
          saved.training && typeof saved.training === 'object'
            ? Object.fromEntries(
                Object.entries(saved.training).filter(
                  ([key]) => !['output_dir', 'registry_dir', 'resume'].includes(key),
                ),
              )
            : undefined;
        const next = await api<Job>('/training/jobs', {
          ...Object.fromEntries(
            Object.entries(saved).filter(([key]) =>
              ['trials', 'study_name', 'seed', 'search_space'].includes(key),
            ),
          ),
          ...(training ? { training } : {}),
          kind: 'tune',
          resume_study: job.id,
        });
        refreshRevision.current++;
        selectJob(next);
        await refresh();
      } catch (e) {
        onError((e as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }
    setResumeJob(job);
  };
  const importModel = async (file?: File) => {
    if (!file) return;
    setImporting(true);
    const body = new FormData();
    body.append('file', file);
    try {
      await api('/models/import', body);
      onModelsChanged();
      setNotice(t('Model imported. It is now available in compatible games.'));
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setImporting(false);
    }
  };
  const inspect = async (job: Job) => {
    selectJob(job);
    try {
      await refresh();
    } catch (error) {
      onError((error as Error).message);
    }
  };
  const removeTrainingData = async () => {
    if (!deleteJob) return;
    setDeletingJob(true);
    setDeleteJobError('');
    try {
      await api(`/training/jobs/${deleteJob.id}`, undefined, 'DELETE');
      refreshRevision.current++;
      selectionRevision.current++;
      setJobs((previous) => previous.filter((job) => job.id !== deleteJob.id));
      setSelectedJob((previous) => (previous?.id === deleteJob.id ? null : previous));
      setDeleteJob(null);
      setNotice(
        t('Training data deleted. Registered models and saved game history have been kept.'),
      );
    } catch (error) {
      setDeleteJobError((error as Error).message);
    } finally {
      setDeletingJob(false);
    }
  };
  const loadParameters = (model: Model) => {
    const loaded = recordedTrainingConfig(config, model);
    if (!loaded) return;
    setConfig(loaded);
    setKind('train');
    setAdvanced(true);
    setNotice(
      t('Training parameters loaded from {name}. Unrecorded inputs keep their current values.', {
        name: model.name,
      }),
    );
    trainingForm.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };
  const removeModel = async () => {
    if (
      !deleteModel ||
      !modelUsage ||
      usageLoading ||
      modelUsage.running_tournaments.length ||
      (modelUsage.unfinished_games.length && !discardUnfinished)
    )
      return;
    setDeleting(true);
    setDeleteError('');
    try {
      const query = new URLSearchParams();
      if (modelUsage.unfinished_games.length) {
        query.set('discard_unfinished', 'true');
        if (modelUsage.usage_token) query.set('usage_token', modelUsage.usage_token);
      }
      const result = await api<{ discarded_game_ids?: string[] }>(
        `/models/${deleteModel.id}${query.size ? `?${query}` : ''}`,
        undefined,
        'DELETE',
      );
      const discarded = result.discarded_game_ids ?? [];
      if (discarded.length) onGamesDeleted?.(discarded);
      setDeleteModel(null);
      onModelsChanged();
      setNotice(
        discarded.length
          ? t(
              discarded.length === 1
                ? 'Model deleted. {count} unfinished game removed without results. Completed history and training checkpoints have been kept.'
                : 'Model deleted. {count} unfinished games removed without results. Completed history and training checkpoints have been kept.',
              { count: discarded.length },
            )
          : t('Model deleted. Saved game history and training checkpoints have been kept.'),
      );
    } catch (error) {
      setDeleteError((error as Error).message);
      setDiscardUnfinished(false);
      setUsageRevision((value) => value + 1);
    } finally {
      setDeleting(false);
    }
  };
  const running = jobs.filter((job) =>
    ['running', 'pending', 'queued', 'cancelling'].includes(job.status),
  ).length;
  const metrics = selectedJob?.progress ?? selectedJob?.metrics ?? {};
  const isRemote = kind === 'train' && target !== 'local';
  const selectedWorker = workers.find((worker) => `worker:${worker.id}` === target);
  const unavailableTarget =
    isRemote &&
    target.startsWith('worker:') &&
    (!selectedWorker || selectedWorker.status === 'revoked');
  const targetDevice = isRemote ? selectedWorker?.capabilities : device;
  const hardwareName = isRemote
    ? (selectedWorker?.name ?? t('Remote workers'))
    : (device.default_device ?? device.selected ?? device.device ?? 'CPU');
  return (
    <>
      <section className="page-intro">
        <div>
          <h1>{t('The AI workshop.')}</h1>
          <p>
            {t(
              'From first principles to your own strategist. Train, evaluate, and bring it to the table.',
            )}
          </p>
        </div>
        <span className="device-tag">
          <span className="status-dot" />
          {device.cuda_available ? t('CUDA available') : t('CPU training available')}
        </span>
      </section>
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button aria-label={t('Dismiss notice')} onClick={() => setNotice('')}>
            ×
          </button>
        </div>
      )}
      <div className="metric-grid three">
        <Metric
          label={t('TRAINING DEVICE')}
          value={hardwareName.toUpperCase()}
          detail={
            isRemote
              ? t('Training runs on the selected worker')
              : device.cuda_available
                ? t('NVIDIA acceleration is available')
                : t('Automatic CPU fallback · CUDA supported')
          }
        />
        <Metric
          label={t('ACTIVE JOBS')}
          value={running}
          detail={t('Training runs independently of your games')}
        />
        <Metric
          label={t('TRAINED MODELS')}
          value={models.length}
          detail={t('Ready to join a compatible battlefield')}
        />
      </div>
      <TrainingWorkers
        workers={workers}
        onChanged={() => void refresh().catch((error) => onError(error.message))}
      />
      <div className="training-layout">
        <section className="panel training-config" ref={trainingForm}>
          <div className="panel-title">
            <h2>{t('Forge a new strategist')}</h2>
            <span className="badge">{t('SELF-PLAY')}</span>
          </div>
          <p className="subtle">
            {t(
              'A policy and value network learns from its own games. Small runs verify the workflow; strength takes time and evaluation.',
            )}
          </p>
          <form onSubmit={start}>
            <div className="segmented">
              <button
                type="button"
                className={kind === 'train' ? 'active' : ''}
                onClick={() => setKind('train')}
              >
                {t('Train a model')}
              </button>
              <button
                type="button"
                className={kind === 'tune' ? 'active' : ''}
                onClick={() => setKind('tune')}
              >
                {t('Tune parameters')}
              </button>
            </div>
            <div className="form-grid">
              <TrainingTarget
                value={kind === 'tune' ? 'local' : target}
                workers={workers}
                disabled={kind === 'tune'}
                onChange={(value) => {
                  setTarget(value);
                  update('device', 'auto');
                }}
              />
              <label>
                {t('Battlefield')}
                <select
                  value={String(config.ruleset)}
                  onChange={(e) =>
                    setConfig((previous) =>
                      changeTrainingPlan(
                        previous,
                        e.target.value,
                        String(previous.preset) as TrainingPreset,
                      ),
                    )
                  }
                >
                  {sortRulesets(rulesets).map((rule) => (
                    <option value={rule.id} key={rule.id}>
                      {rule.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Training preset')}
                <select
                  value={String(config.preset)}
                  onChange={(e) =>
                    setConfig((previous) =>
                      changeTrainingPlan(
                        previous,
                        String(previous.ruleset),
                        e.target.value as TrainingPreset,
                      ),
                    )
                  }
                >
                  <option value="recommended">
                    {t('Recommended · tailored to this battlefield')}
                  </option>
                  <option value="smoke">{t('Quick check · a tiny real training run')}</option>
                  <option value="beginner">{t('Beginner · a starting point')}</option>
                  <option value="balanced">{t('Balanced · a longer experiment')}</option>
                </select>
              </label>
              <div className="name-suggestion-field">
                <label htmlFor="training-model-name">{t('Model name')}</label>
                <div className="name-input-group">
                  <input
                    id="training-model-name"
                    required
                    maxLength={100}
                    value={String(config.model_name)}
                    onChange={(e) => update('model_name', e.target.value)}
                  />
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={t('Suggest Viking name')}
                    title={t('Suggest a Viking name')}
                    onClick={() =>
                      update(
                        'model_name',
                        suggestVikingName([
                          ...models.map((model) => model.name),
                          String(config.model_name),
                        ]),
                      )
                    }
                  >
                    ⚄
                  </button>
                </div>
              </div>
              <label>
                {t('Device')}
                <select
                  value={String(config.device)}
                  onChange={(e) => update('device', e.target.value)}
                >
                  <option value="auto">{t('Automatic')}</option>
                  <option value="cpu">{t('CPU')}</option>
                  <option
                    value="cuda"
                    disabled={
                      targetDevice?.cuda_available === false ||
                      (!isRemote && !device.cuda_available)
                    }
                  >
                    {t('NVIDIA CUDA')}
                    {targetDevice?.cuda_available === false || (!isRemote && !device.cuda_available)
                      ? ` (${t('unavailable')})`
                      : ''}
                  </option>
                  <option
                    value="mps"
                    disabled={
                      targetDevice?.mps_available === false || (!isRemote && !device.mps_available)
                    }
                  >
                    {t('Apple Metal')}
                    {targetDevice?.mps_available === false || (!isRemote && !device.mps_available)
                      ? ` (${t('unavailable')})`
                      : ''}
                  </option>
                </select>
              </label>
              {numberField('iterations', config, update)}
              {numberField('games', config, update)}
              {numberField('batch_size', config, update)}
              {numberField('mcts_iterations', config, update)}
              <SelfPlayModeField
                value={String(config.self_play_mode)}
                onChange={(value) => update('self_play_mode', value)}
              />
              {numberField('self_play_concurrency', config, update)}
              {numberField('inference_batch_size', config, update)}
            </div>
            {kind === 'tune' && (
              <p className="subtle">
                {t('Parameter tuning runs on this server. Remote workers support model training.')}
              </p>
            )}
            {isRemote && (
              <p className="subtle">
                {t(
                  'Jobs wait in the queue until a compatible worker has capacity. You can queue several runs with different settings.',
                )}
              </p>
            )}
            {config.preset === 'recommended' && (
              <p className="training-plan-note subtle">
                {Number(config.channels)} {t('channels ·')} {Number(config.blocks)}{' '}
                {t('residual blocks ·')} {Number(config.games) * Number(config.iterations)}{' '}
                {t('self-play games.')}{' '}
                {t(
                  'A board-specific starting budget, not a proven strongest configuration. Full runs can take hours or days; choose Quick check to verify your setup.',
                )}
              </p>
            )}
            <label className="checkbox-label training-retain-best">
              <input
                type="checkbox"
                checked={Boolean(config.save_all_best)}
                onChange={(event) => update('save_all_best', event.target.checked)}
              />
              {t('Save every new best model')}
            </label>
            <p className="subtle">
              {t(
                'Retain and register each strict evaluation improvement. Tied scores do not create extra models. Uses additional disk space.',
              )}
            </p>
            <button
              type="button"
              className="text-button advanced-toggle"
              onClick={() => setAdvanced((value) => !value)}
              aria-expanded={advanced}
            >
              {advanced ? '−' : '＋'} {t('Advanced configuration')}
            </button>
            {advanced && (
              <div className="advanced-config form-grid">
                {numberField('channels', config, update)}
                {numberField('blocks', config, update)}
                <label>
                  {t('Optimizer')}
                  <select
                    value={String(config.optimizer)}
                    onChange={(e) => update('optimizer', e.target.value)}
                  >
                    <option value="adamw">{t('AdamW')}</option>
                    <option value="adam">{t('Adam')}</option>
                    <option value="sgd">{t('SGD')}</option>
                  </select>
                </label>
                {numberField('epochs', config, update)}
                {numberField('learning_rate', config, update)}
                {numberField('mcts_time_limit', config, update)}
                {numberField('exploration', config, update)}
                {numberField('temperature', config, update)}
                {numberField('dirichlet_alpha', config, update)}
                {numberField('dirichlet_fraction', config, update)}
                {numberField('replay_buffer_size', config, update)}
                {numberField('weight_decay', config, update)}
                {config.optimizer === 'sgd' && numberField('momentum', config, update)}
                {numberField('checkpoint_frequency', config, update)}
                {numberField('evaluation_frequency', config, update)}
                {numberField('evaluation_games', config, update)}
                {numberField('evaluation_iterations', config, update)}
                {numberField('max_game_plies', config, update)}
                {numberField('evaluation_max_plies', config, update)}
                {numberField('cpu_threads', config, update)}
                {numberField('seed', config, update)}
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={Boolean(config.mixed_precision)}
                    onChange={(e) => update('mixed_precision', e.target.checked)}
                  />
                  {t('Mixed precision when supported')}
                </label>
              </div>
            )}
            {kind === 'tune' && (
              <div className="tuning-config">
                <p className="subtle">
                  {t(
                    'Trials optimize game results against a baseline. Tuning can take much longer than a single run. Resume a saved study from its job below.',
                  )}
                </p>
                <div className="form-grid">
                  <label>
                    {t('Trial budget')}
                    <input
                      type="number"
                      min="1"
                      max="1000"
                      value={trials}
                      onChange={(e) => setTrials(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    {t('Study name')}
                    <input
                      required
                      value={studyName}
                      onChange={(e) => setStudyName(e.target.value)}
                    />
                  </label>
                </div>
                <label>
                  {t('Parameter search space (JSON)')}
                  <textarea
                    value={space}
                    onChange={(e) => setSpace(e.target.value)}
                    rows={7}
                    spellCheck={false}
                  />
                </label>
              </div>
            )}
            <div className="training-start">
              <span>{t('Start small. Measure improvement.')}</span>
              <button className="button primary" disabled={busy || unavailableTarget}>
                {busy
                  ? t('Starting…')
                  : kind === 'train'
                    ? isRemote
                      ? t('Queue training')
                      : t('Start training')
                    : t('Start tuning')}{' '}
                <span aria-hidden="true">↗</span>
              </button>
            </div>
          </form>
        </section>
        <section className="panel jobs-panel">
          <div className="panel-title">
            <h2>{t('At the forge')}</h2>
            <button
              className="text-button"
              onClick={() => {
                void refresh().catch((e) => onError(e.message));
                onModelsChanged();
              }}
            >
              {t('Refresh')}
            </button>
          </div>
          {!jobs.length ? (
            <div className="empty-state compact">
              <span className="empty-rune">ᛏ</span>
              <h3>{t('Your next opponent starts here.')}</h3>
              <p>
                {t(
                  'Start a quick check to run real self-play, training, evaluation, and checkpoint creation.',
                )}
              </p>
            </div>
          ) : (
            <div className="jobs-list">
              {jobs.map((job) => (
                <button
                  key={job.id}
                  className={`job-row ${selectedJob?.id === job.id ? 'selected' : ''}`}
                  onClick={() => void inspect(job)}
                >
                  <span className={`job-status ${job.status}`}>{t(job.status)}</span>
                  <strong>
                    {String(
                      job.config?.model_name ??
                        job.config?.ruleset ??
                        job.ruleset ??
                        t('Training job'),
                    )}
                  </strong>
                  <small>
                    {job.created_at
                      ? new Date(job.created_at).toLocaleString(locale)
                      : job.id.slice(0, 8)}
                    {job.execution === 'remote' && (
                      <span className="job-location">
                        {' '}
                        · {job.worker_name ?? t('Remote queue')}
                      </span>
                    )}
                  </small>
                </button>
              ))}
            </div>
          )}
          {selectedJob && (
            <div className="job-detail">
              <div className="panel-title">
                <h3>{t('Training progress')}</h3>
                <span className={`job-status ${selectedJob.status}`}>{t(selectedJob.status)}</span>
              </div>
              <p className="job-worker">
                {selectedJob.execution === 'remote'
                  ? (selectedJob.worker_name ?? t('Waiting for an available worker'))
                  : t('This server')}
                {selectedJob.last_reported_at
                  ? ` · ${t('Last update {time}', { time: new Date(selectedJob.last_reported_at).toLocaleString(locale) })}`
                  : ''}
              </p>
              <TrainingHistoryChart history={metrics.history} />
              <TrainingPerformance progress={metrics} />
              {Array.isArray(metrics.best_models) && metrics.best_models.length > 0 && (
                <p className="subtle">
                  {metrics.best_models.length} {t('best models retained during this run.')}
                </p>
              )}
              <div className="job-metrics">
                {Object.entries(metrics)
                  .filter(
                    ([key, value]) =>
                      !['checkpoint', 'best_checkpoint', 'inference_checkpoint'].includes(key) &&
                      ['number', 'string'].includes(typeof value),
                  )
                  .map(([key, value]) => (
                    <div key={key}>
                      <span>{t(key.replaceAll('_', ' '))}</span>
                      <strong>{t(metricText(value))}</strong>
                    </div>
                  ))}
              </div>
              {metrics.metrics != null && typeof metrics.metrics === 'object' && (
                <details>
                  <summary>{t('Evaluation metrics')}</summary>
                  <pre className="log-view">{JSON.stringify(metrics.metrics, null, 2)}</pre>
                </details>
              )}
              {selectedJob.error && (
                <p className="form-error" role="alert">
                  {t(selectedJob.error)}
                </p>
              )}
              <div className="job-actions">
                {['running', 'pending', 'queued', 'cancelling'].includes(selectedJob.status) ? (
                  <button
                    className="button secondary small"
                    onClick={() => void cancel(selectedJob.id)}
                    disabled={selectedJob.status === 'cancelling'}
                  >
                    {selectedJob.status === 'cancelling' ? t('Stopping…') : t('Stop training')}
                  </button>
                ) : (
                  <button
                    className="button secondary small"
                    onClick={() => void resume(selectedJob)}
                    disabled={busy || (selectedJob.kind !== 'tune' && !selectedJob.checkpoint)}
                  >
                    {selectedJob.kind === 'tune' ? t('Resume study') : t('Resume from checkpoint')}
                  </button>
                )}
                {!['running', 'pending', 'queued', 'cancelling'].includes(selectedJob.status) && (
                  <button
                    type="button"
                    className="button secondary small danger-text"
                    onClick={() => {
                      setDeleteJob(selectedJob);
                      setDeleteJobError('');
                    }}
                  >
                    {t('Delete training data')}
                  </button>
                )}
              </div>
              <details>
                <summary>{t('Training log')}</summary>
                <pre className="log-view">
                  {Array.isArray(selectedJob.logs)
                    ? selectedJob.logs.join('\n')
                    : selectedJob.logs || t('No log output yet.')}
                </pre>
              </details>
            </div>
          )}
        </section>
      </div>
      <section className="panel model-registry">
        <div className="panel-title">
          <div>
            <h2>{t('Model collection')}</h2>
          </div>
          <label className="button secondary file-button">
            {importing ? t('Importing…') : t('↑ Import model')}
            <input
              type="file"
              accept=".pt,.pth"
              disabled={importing}
              onChange={(e) => {
                void importModel(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
          </label>
        </div>
        <p className="subtle">
          {t(
            'Compatible checkpoints are validated before registration. Select a trained model under “Neural network” when starting a game.',
          )}
        </p>
        {models.length ? (
          <div className="models-grid">
            {models.map((model) => {
              const iteration = modelIteration(model);
              const sourceJob = jobs.find(
                (job) =>
                  job.kind !== 'tune' &&
                  !!job.checkpoint &&
                  (job.model_id === model.id ||
                    (job.result?.model &&
                      typeof job.result.model === 'object' &&
                      'id' in job.result.model &&
                      job.result.model.id === model.id) ||
                    (Array.isArray(job.progress?.best_models) &&
                      job.progress.best_models.some((best) => best?.model_id === model.id))),
              );
              return (
                <article key={model.id} className="model-card" data-model-id={model.id}>
                  <div className="model-symbol">
                    <Rune rune={model.rune} />
                  </div>
                  <div>
                    <h3>{model.name}</h3>
                    <p>
                      {model.ruleset} ·{' '}
                      {iteration === undefined
                        ? t('Policy / value')
                        : t('Iteration {iteration}', { iteration })}
                    </p>
                    <small>
                      {model.created_at
                        ? new Date(model.created_at).toLocaleDateString(locale)
                        : model.id}
                    </small>
                  </div>
                  <div className="model-actions">
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={t('Rename model {name}', { name: model.name })}
                      title={t('Rename model')}
                      onClick={() => setRenameModel(model)}
                    >
                      ✎
                    </button>
                    {sourceJob && (
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={t('Resume training for {name}', { name: model.name })}
                        title={t('Resume training')}
                        disabled={['running', 'pending', 'queued', 'cancelling'].includes(
                          sourceJob.status,
                        )}
                        onClick={() => setResumeJob(sourceJob)}
                      >
                        ↻
                      </button>
                    )}
                    <a
                      className="icon-button"
                      href={`/api/models/${model.id}/export`}
                      download
                      aria-label={t('Export {name}', { name: model.name })}
                    >
                      ↓
                    </a>
                    <button
                      type="button"
                      className="icon-button danger-text"
                      aria-label={t('Delete model {name}', { name: model.name })}
                      title={t('Delete model')}
                      onClick={() => {
                        setDeleteModel(model);
                        setDeleteError('');
                        setModelUsage(null);
                        setUsageLoading(true);
                        setDiscardUnfinished(false);
                      }}
                    >
                      ×
                    </button>
                  </div>
                  <ModelTrainingSummary model={model} />
                  <button
                    type="button"
                    className="button secondary small model-load-parameters"
                    aria-label={t('Load training parameters for {name}', { name: model.name })}
                    disabled={!recordedTrainingConfig(config, model)}
                    title={
                      !recordedTrainingConfig(config, model)
                        ? t('No recorded training parameters are available for this model.')
                        : undefined
                    }
                    onClick={() => loadParameters(model)}
                  >
                    {t('Load training parameters')}
                  </button>
                  {model.evaluation && (
                    <div className="model-evaluation">
                      <span className="eyebrow">
                        {t('AGAINST')} {t(model.evaluation.opponent ?? 'BASELINE')}
                      </span>
                      <div>
                        <span>
                          <strong>{model.evaluation.wins ?? 0}</strong> {t('wins')}
                        </span>
                        <span>
                          <strong>{model.evaluation.draws ?? 0}</strong> {t('draws')}
                        </span>
                        <span>
                          <strong>{model.evaluation.losses ?? 0}</strong> {t('losses')}
                        </span>
                      </div>
                      <small>
                        {model.evaluation.truncated ?? 0}{' '}
                        {t('unfinished games · limited evaluation')}
                      </small>
                    </div>
                  )}
                  {model.metrics && (
                    <div className="model-metrics">
                      {Object.entries(model.metrics)
                        .filter(([, value]) => typeof value === 'number')
                        .slice(0, 3)
                        .map(([key, value]) => (
                          <span key={key}>
                            {t(key.replaceAll('_', ' '))} <strong>{t(metricText(value))}</strong>
                          </span>
                        ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="empty-copy">{t('Your trained and imported models will live here.')}</p>
        )}
      </section>
      {renameModel && (
        <ModelRenameDialog
          key={renameModel.id}
          model={renameModel}
          onClose={() => setRenameModel(null)}
          onRenamed={() => {
            setRenameModel(null);
            onModelsChanged();
            setNotice(t('Model renamed. Its identity and saved history are unchanged.'));
          }}
        />
      )}
      {deleteJob && (
        <RecordsConfirm
          title={t('Delete training data?')}
          busy={deletingJob}
          error={deleteJobError}
          onCancel={() => setDeleteJob(null)}
          onConfirm={() => void removeTrainingData()}
        >
          <p>
            {t(
              'Permanently remove the checkpoints, replay buffer, learning history and logs for this training run?',
            )}
          </p>
          <p>
            <strong>
              {String(
                deleteJob.config?.model_name ?? deleteJob.config?.ruleset ?? t('Training job'),
              )}
            </strong>{' '}
            <span className="subtle">#{deleteJob.id.slice(0, 8)}</span>
          </p>
          <p>
            {t(
              'This run can no longer be resumed. Registered models, their recorded training parameters, saved games and tournaments are kept.',
            )}
          </p>
          {deleteJob.kind === 'tune' && (
            <p>
              {t('A study shared by other saved jobs must be kept until those jobs are removed.')}
            </p>
          )}
        </RecordsConfirm>
      )}
      {resumeJob && (
        <TrainingResumeDialog
          job={resumeJob}
          workers={workers}
          onClose={() => setResumeJob(null)}
          onStarted={(next, completed, target) => {
            setResumeJob(null);
            refreshRevision.current++;
            if (!observedJobs.current) observedJobs.current = new Map();
            observedJobs.current.set(next.id, next.status);
            selectJob(next);
            setJobs((previous) => [next, ...previous]);
            setNotice(
              t('Training resumed from iteration {completed} toward {target}.', {
                completed,
                target,
              }),
            );
            void refresh().catch((error) => onError(error.message));
          }}
        />
      )}
      {deleteModel && (
        <RecordsConfirm
          title={t('Delete model {name}?', { name: deleteModel.name })}
          busy={deleting}
          error={deleteError}
          confirmDisabled={
            usageLoading ||
            !modelUsage ||
            !!modelUsage.running_tournaments.length ||
            (!!modelUsage.unfinished_games.length && !discardUnfinished)
          }
          onCancel={() => setDeleteModel(null)}
          onConfirm={() => void removeModel()}
        >
          <p>
            {t('This removes')} <strong>{deleteModel.name}</strong>{' '}
            {t(
              'from your model collection and opponent selection. Completed game histories, statistics and training checkpoints are kept.',
            )}
          </p>
          {usageLoading && (
            <p className="model-delete-note" role="status">
              {t('Checking saved games and tournaments…')}
            </p>
          )}
          {usageError && (
            <div className="model-usage-error">
              <p className="form-error" role="alert">
                {usageError}
              </p>
              <button
                type="button"
                className="button secondary small"
                onClick={() => setUsageRevision((value) => value + 1)}
              >
                {t('Check again')}
              </button>
            </div>
          )}
          {modelUsage && (
            <>
              {!!modelUsage.running_tournaments.length && (
                <div className="model-usage-warning">
                  <h3>{t('Running tournament')}</h3>
                  <p>{t('Stop these tournaments in Tournaments before deleting this model.')}</p>
                  <ul>
                    {modelUsage.running_tournaments.map((tournament) => (
                      <li key={tournament.id}>{tournament.name}</li>
                    ))}
                  </ul>
                </div>
              )}
              {modelUsage.unfinished_games.length ? (
                <div className="model-usage-warning">
                  <h3>
                    {modelUsage.unfinished_games.length} {t('unfinished')}{' '}
                    {modelUsage.unfinished_games.length === 1 ? t('game') : t('games')}
                  </h3>
                  <p>
                    {t(
                      'Saved games remain on this server even when they are no longer open in your browser. Continue them from the Hall of records, or discard them here.',
                    )}
                  </p>
                  <ul className="model-usage-games">
                    {modelUsage.unfinished_games.map((game) => (
                      <li key={game.id}>
                        <strong>
                          {game.players.attackers.name} {t('vs')} {game.players.defenders.name}
                        </strong>
                        <span>
                          {rulesets.find((rule) => rule.id === game.ruleset)?.name ?? game.ruleset}{' '}
                          · {game.total_moves} {t('moves ·')}{' '}
                          {new Date(game.created_at).toLocaleDateString(locale)}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <label className="model-discard-choice">
                    <input
                      type="checkbox"
                      checked={discardUnfinished}
                      disabled={deleting || !!modelUsage.running_tournaments.length}
                      onChange={(event) => setDiscardUnfinished(event.target.checked)}
                    />
                    <span>{t('Discard unfinished games and delete model')}</span>
                  </label>
                  <p>
                    {t(
                      'Discarded games are permanently removed with no result, history or statistics. Completed games and training checkpoints remain.',
                    )}
                  </p>
                </div>
              ) : (
                <p className="model-delete-note">
                  {t(
                    'No unfinished games use this model. Saved games and training checkpoints remain available.',
                  )}
                </p>
              )}
            </>
          )}
        </RecordsConfirm>
      )}
    </>
  );
}
