import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import type { DeviceInfo, Job, Model, Ruleset, TrainingWorker } from './types';
import { suggestVikingName } from './vikingNames';
import RecordsConfirm from './RecordsConfirm';
import './features.css';
import './workshopManagement.css';
import { getLocale, t, useI18n } from './i18n';
import { trainingConfig, recordedTrainingConfig } from './trainingPresets';
import TrainingResumeDialog from './TrainingResumeDialog';
import { opponentName } from './EvaluationSettings';
import Rune from './Rune';
import ModelRenameDialog from './ModelRenameDialog';
import ModelTrainingSummary from './ModelTrainingSummary';
import { modelIteration } from './modelLabel';
import TrainingWorkers from './TrainingWorkers';
import { targetRequest } from './TrainingTarget';
import TrainingCreationPanel from './TrainingCreationPanel';
import TrainingJobNavigator from './TrainingJobNavigator';
import TrainingJobDashboard from './TrainingJobDashboard';
import { activeJob, sortJobs } from './trainingDashboardData';
import './trainingDashboard.css';

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
    ? new Intl.NumberFormat(getLocale(), { maximumFractionDigits: 4 }).format(value)
    : String(value ?? '—');

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
  const [creationOpen, setCreationOpen] = useState<boolean | null>(null);
  const [modelsOpen, setModelsOpen] = useState<boolean | null>(null);
  const [workersOpen, setWorkersOpen] = useState(false);
  const [navigatorOpen, setNavigatorOpen] = useState(true);
  const [dashboardRevision, setDashboardRevision] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const monitoring = useRef<HTMLElement>(null);
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
  const [importingJob, setImportingJob] = useState(false);
  const [exportingJob, setExportingJob] = useState(false);
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
    setLoaded(true);
    setLoadFailed(false);
    setCreationOpen((previous) => previous ?? !runs.length);
    setModelsOpen((previous) => previous ?? !runs.length);
    setDevice(hardware);
    setJobs(runs);
    setWorkers(Array.isArray(connected) ? connected : []);
    if (selectionRevision.current === selectedRevision) {
      setSelectedJob((previous) =>
        previous
          ? (runs.find((job) => job.id === previous.id) ?? null)
          : (sortJobs(runs)[0] ?? null),
      );
    }
  }, []);
  useEffect(() => {
    void refresh().catch((e) => {
      setLoadFailed(true);
      onError(e.message);
    });
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
      showMonitoring();
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
        showMonitoring();
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
  const importTrainingJob = async (file?: File) => {
    if (!file) return;
    setImportingJob(true);
    setNotice('');
    const body = new FormData();
    body.append('file', file);
    try {
      const imported = await api<Job>('/training/jobs/import', body);
      refreshRevision.current++;
      selectJob(imported);
      showMonitoring();
      setJobs((previous) => [imported, ...previous]);
      onModelsChanged();
      setNotice(t('Job imported. Review its history or resume from its full checkpoint.'));
      await refresh();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setImportingJob(false);
    }
  };
  const exportTrainingJob = async (job: Job) => {
    setExportingJob(true);
    try {
      const response = await fetch(`/api/training/jobs/${job.id}/export`);
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === 'string' ? error.detail : t('Job export failed.'));
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `hnefatafl-job-${job.id}.zip`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setExportingJob(false);
    }
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
    setCreationOpen(true);
    setNotice(
      t('Training parameters loaded from {name}. Unrecorded inputs keep their current values.', {
        name: model.name,
      }),
    );
    requestAnimationFrame(() => trainingForm.current?.scrollIntoView?.({ block: 'start' }));
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
  const showMonitoring = () => {
    setCreationOpen(false);
    setDashboardRevision((value) => value + 1);
    requestAnimationFrame(() => {
      monitoring.current?.focus({ preventScroll: true });
      monitoring.current?.scrollIntoView?.({ block: 'start' });
    });
  };
  const running = jobs.filter(activeJob).length;
  const hardwareName = device.default_device ?? device.selected ?? device.device ?? 'CPU';
  return (
    <div className="training-workshop">
      <section className="page-intro workshop-intro">
        <h1>{t('The AI workshop.')}</h1>
        <div className="workshop-global" aria-label={t('Workshop summary')}>
          <span>
            <strong>{hardwareName.toUpperCase()}</strong> {device.cuda_devices?.join(' · ')}
          </span>
          <span>
            <strong>{running}</strong> {t('active jobs')}
          </span>
          <span>
            <strong>{models.length}</strong> {t('registered models')}
          </span>
          <button
            className="button secondary small"
            aria-expanded={workersOpen}
            aria-controls="workshop-workers"
            onClick={() => setWorkersOpen((value) => !value)}
          >
            {t('Remote workers')} ({workers.length})
          </button>
          <button
            className="button primary small"
            aria-expanded={creationOpen ?? true}
            aria-controls="training-creation"
            onClick={() => {
              setCreationOpen(true);
              requestAnimationFrame(() =>
                trainingForm.current?.scrollIntoView?.({ block: 'start' }),
              );
            }}
          >
            {t('New training')} ＋
          </button>
        </div>
      </section>
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button aria-label={t('Dismiss notice')} onClick={() => setNotice('')}>
            ×
          </button>
        </div>
      )}
      <div id="workshop-workers" hidden={!workersOpen}>
        <TrainingWorkers
          workers={workers}
          onChanged={() => void refresh().catch((error) => onError(error.message))}
        />
      </div>
      <section className="panel training-config workshop-creation" ref={trainingForm}>
        <div className="panel-title">
          <h2>
            <button
              className="disclosure-heading"
              aria-expanded={creationOpen ?? true}
              aria-controls="training-creation"
              onClick={() => setCreationOpen((value) => !(value ?? true))}
            >
              <span aria-hidden="true">{(creationOpen ?? true) ? '−' : '＋'}</span>{' '}
              {t('Forge a new strategist')}
            </button>
          </h2>
          <span className="subtle">
            {t('Train a model')} · {t('Tune parameters')}
          </span>
        </div>
        <div id="training-creation" hidden={creationOpen === false}>
          <TrainingCreationPanel
            {...{
              config,
              setConfig,
              update,
              kind,
              setKind,
              target,
              setTarget,
              trials,
              setTrials,
              studyName,
              setStudyName,
              space,
              setSpace,
              advanced,
              setAdvanced,
              busy,
              models,
              rulesets,
              workers,
              device,
              start,
            }}
          />
        </div>
      </section>
      <section
        className="panel jobs-panel workshop-jobs"
        ref={monitoring}
        tabIndex={-1}
        aria-label={t('At the forge')}
      >
        <div className="panel-title">
          <h2>{t('At the forge')}</h2>
          {!!jobs.length && (
            <button
              className="button secondary small"
              aria-expanded={navigatorOpen}
              aria-controls="job-navigation"
              onClick={() => setNavigatorOpen((value) => !value)}
            >
              {t(navigatorOpen ? 'Hide job list' : 'Show job list')}
            </button>
          )}
          <label className={`button secondary small file-button ${importingJob ? 'disabled' : ''}`}>
            {importingJob ? t('Importing job…') : t('Import job')}
            <input
              type="file"
              accept=".zip,application/zip"
              aria-label={t('Import job')}
              disabled={importingJob}
              onChange={(event) => {
                void importTrainingJob(event.target.files?.[0]);
                event.target.value = '';
              }}
            />
          </label>
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
        {!loaded && !loadFailed && <p role="status">{t('Loading training jobs…')}</p>}
        {!loaded && loadFailed && (
          <p role="alert">{t('Could not load training jobs. Use Refresh to try again.')}</p>
        )}
        {loaded && !jobs.length ? (
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
          !!jobs.length && (
            <div className={`workshop-workspace ${navigatorOpen ? '' : 'navigator-hidden'}`}>
              <div id="job-navigation" hidden={!navigatorOpen}>
                <TrainingJobNavigator
                  jobs={jobs}
                  selected={selectedJob?.id}
                  models={models}
                  onSelect={(job) => void inspect(job)}
                />
              </div>
              {selectedJob ? (
                <TrainingJobDashboard
                  job={selectedJob}
                  revision={dashboardRevision}
                  busy={busy}
                  exporting={exportingJob}
                  onCancel={() => void cancel(selectedJob.id)}
                  onResume={() => void resume(selectedJob)}
                  onExport={() => void exportTrainingJob(selectedJob)}
                  onRefresh={() => void refresh().catch((error) => onError(error.message))}
                  onDelete={() => {
                    setDeleteJob(selectedJob);
                    setDeleteJobError('');
                  }}
                />
              ) : (
                <p className="subtle">{t('Select a training job to inspect its history.')}</p>
              )}
            </div>
          )
        )}
      </section>
      <section className="panel model-registry">
        <div className="panel-title">
          <div>
            <h2>
              <button
                className="disclosure-heading"
                aria-expanded={modelsOpen ?? true}
                aria-controls="model-collection"
                onClick={() => setModelsOpen((value) => !(value ?? true))}
              >
                <span aria-hidden="true">{(modelsOpen ?? true) ? '−' : '＋'}</span>{' '}
                {t('Model collection')} ({models.length})
              </button>
            </h2>
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
        <div id="model-collection" hidden={modelsOpen === false}>
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
                    job.resumable !== false &&
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
                          {t('AGAINST')} {t(opponentName(model.evaluation.opponent ?? 'BASELINE'))}
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
        </div>
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
            showMonitoring();
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
    </div>
  );
}
