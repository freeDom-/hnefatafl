import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, apiFetch } from './api';
import type { DeviceInfo, Job, Model, Ruleset, TrainingWorker } from './types';
import { suggestVikingName } from './vikingNames';
import RecordsConfirm from './RecordsConfirm';
import StudyDeletionDialog from './StudyDeletionDialog';
import { useAuth } from './Auth';
import './features.css';
import './workshopManagement.css';
import { getLocale, t, useI18n } from './i18n';
import {
  trainingConfig,
  recordedTrainingConfig,
  recordedTrainingParameters,
  type TrainingConfig,
} from './trainingPresets';
import { recordedJobParameters, tunable } from './trainingReuse';
import { trainingParameterLabel } from './TrainingFields';
import TrainingResumeDialog from './TrainingResumeDialog';
import TrainingTuneDialog from './TrainingTuneDialog';
import { readImportedConfig, ConfigImportError } from './trainingConfigImport';
import { opponentName } from './EvaluationSettings';
import Rune from './Rune';
import ModelRenameDialog from './ModelRenameDialog';
import JobRenameDialog from './JobRenameDialog';
import ModelTrainingSummary from './ModelTrainingSummary';
import { modelIteration } from './modelLabel';
import TrainingWorkers from './TrainingWorkers';
import {
  TrainingInputError,
  fieldIdentity,
  launchPayload,
  rejectedTrainingFields,
  tuneResumePayload,
} from './trainingPayload';
import TrainingCreationPanel from './TrainingCreationPanel';
import PageHeading from './PageHeading';
import TrainingJobNavigator from './TrainingJobNavigator';
import TrainingJobDashboard from './TrainingJobDashboard';
import { availableJobTabs, defaultJobTab, resolveJobTab, type JobTab } from './jobTabs';
import { activeJob, sortJobs, jobName, jobRuleset, mergeJobSummary } from './trainingDashboardData';
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
  running_optimizations?: { id: string; name: string; status: string }[];
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
  const { account } = useAuth();
  const [creationOpen, setCreationOpen] = useState<boolean | null>(null);
  const [modelsOpen, setModelsOpen] = useState<boolean | null>(null);
  const [workersOpen, setWorkersOpen] = useState(false);
  const [navigatorOpen, setNavigatorOpen] = useState(true);
  const [dashboardRevision, setDashboardRevision] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const monitoring = useRef<HTMLElement>(null);
  // Local training runs on the same server, which always supports random D4.
  // Seed the capability before the first device response so a new job is not
  // transiently blocked while the request is in flight.
  const [device, setDevice] = useState<DeviceInfo>({
    training_symmetry_modes: ['off', 'random_d4'],
  });
  const [workers, setWorkers] = useState<TrainingWorker[]>([]);
  const [target, setTarget] = useState('local');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [jobTab, setJobTab] = useState<JobTab>(defaultJobTab);
  const [resumeJob, setResumeJob] = useState<Job | null>(null);
  const [resumePrefill, setResumePrefill] = useState<TrainingConfig | null>(null);
  const [resumeAdditional, setResumeAdditional] = useState<number | undefined>(undefined);
  const [tuneJob, setTuneJob] = useState<Job | null>(null);
  const [renameModel, setRenameModel] = useState<Model | null>(null);
  const [renameJob, setRenameJob] = useState<Job | null>(null);
  const [deleteJob, setDeleteJob] = useState<Job | null>(null);
  const [deleteStudyJob, setDeleteStudyJob] = useState<Job | null>(null);
  const [deletingJob, setDeletingJob] = useState(false);
  const [deleteJobError, setDeleteJobError] = useState('');
  const trainingForm = useRef<HTMLElement>(null);
  const [config, setConfig] = useState<Record<string, string | number | boolean>>(() => ({
    ...defaults,
    model_name: suggestVikingName(models.map((model) => model.name)),
  }));
  const [kind, setKind] = useState<'train' | 'tune'>('train');
  const [trials, setTrials] = useState(3);
  const [studySeed, setStudySeed] = useState(0);
  const [studyName, setStudyName] = useState('hnefatafl-search');
  const [space, setSpace] = useState(
    '{\n  "learning_rate": {"type": "float", "low": 0.0001, "high": 0.003, "log": true},\n  "mcts_iterations": {"type": "int", "low": 4, "high": 16}\n}',
  );
  const [strategy, setStrategy] = useState('');
  const [busy, setBusy] = useState(false);
  const [importingConfig, setImportingConfig] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importingJob, setImportingJob] = useState(false);
  const [exportingJob, setExportingJob] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [notice, setNotice] = useState('');
  /** Full paths of the training fields the server rejected, cleared on correction. */
  const [rejectedFields, setRejectedFields] = useState<string[]>([]);
  /** Local explanation for a request the form itself could not build. */
  const [rejectedReason, setRejectedReason] = useState('');
  /**
   * Monotonic time of the last successful monitoring read. The live view uses it
   * to tell a silent worker apart from a browser that cannot reach the server.
   */
  const [monitoringObservedAt, setMonitoringObservedAt] = useState<number | undefined>(undefined);
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
  const monitoringMounted = useRef(false);
  const onMonitoringError = useRef(onError);
  onMonitoringError.current = onError;
  const requests = useRef(new Set<AbortController>());
  const jobsRequest = useRef<{ revision: number; promise: Promise<void> } | null>(null);
  const workersRequest = useRef<Promise<void> | null>(null);
  const detailRequests = useRef(new Map<string, Promise<void>>());
  const detailControllers = useRef(new Map<string, AbortController>());
  const summaryRevision = useRef(0);
  const summaries = useRef(new Map<string, Job>());
  const currentSelection = useRef(selectedJob);
  currentSelection.current = selectedJob;
  const selectJob = (job: Job | null) => {
    selectionRevision.current++;
    setSelectedJob((previous) => (job ? mergeJobSummary(previous, job) : null));
  };
  // Switching jobs preserves the active analysis tab. If that tab is not
  // available for the new job, persist the deterministic default fallback so a
  // later switch cannot resurrect an unavailable view.
  useEffect(() => {
    if (selectedJob) setJobTab((current) => resolveJobTab(current, availableJobTabs(selectedJob)));
  }, [selectedJob?.id]);
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
  const loadDetails = useCallback((id: string): Promise<void> => {
    const pending = detailRequests.current.get(id);
    if (pending && !detailControllers.current.get(id)?.signal.aborted) return pending;
    const revision = refreshRevision.current;
    const snapshot = summaryRevision.current;
    const controller = new AbortController();
    detailControllers.current.set(id, controller);
    requests.current.add(controller);
    const promise = api<Job>(`/training/jobs/${id}`, undefined, undefined, controller.signal)
      .then((job) => {
        if (
          !monitoringMounted.current ||
          revision !== refreshRevision.current ||
          controller.signal.aborted
        )
          return;
        const latest = summaries.current.get(id);
        const detail =
          snapshot !== summaryRevision.current && latest ? mergeJobSummary(job, latest) : job;
        setMonitoringObservedAt(performance.now());
        setSelectedJob((current) => (current?.id === id ? { ...detail, summary: false } : current));
      })
      .catch((error: Error) => {
        if (monitoringMounted.current && !controller.signal.aborted)
          onMonitoringError.current(error.message);
      })
      .finally(() => {
        requests.current.delete(controller);
        if (detailRequests.current.get(id) === promise) detailRequests.current.delete(id);
        if (detailControllers.current.get(id) === controller) detailControllers.current.delete(id);
      });
    detailRequests.current.set(id, promise);
    return promise;
  }, []);
  const loadWorkers = useCallback(() => {
    if (workersRequest.current) return;
    const controller = new AbortController();
    requests.current.add(controller);
    const promise = api<TrainingWorker[]>(
      '/training/workers',
      undefined,
      undefined,
      controller.signal,
    )
      .then((connected) => {
        if (monitoringMounted.current && !controller.signal.aborted)
          setWorkers(
            Array.isArray(connected)
              ? connected.filter((worker) => worker.status !== 'revoked')
              : [],
          );
      })
      .catch((error: Error) => {
        if (monitoringMounted.current && !controller.signal.aborted)
          onMonitoringError.current(error.message);
      })
      .finally(() => {
        requests.current.delete(controller);
        if (workersRequest.current === promise) workersRequest.current = null;
      });
    workersRequest.current = promise;
  }, []);
  const refresh = useCallback(
    async function refresh(): Promise<void> {
      if (!monitoringMounted.current) return;
      const pending = jobsRequest.current;
      if (pending) {
        await pending.promise;
        // Mutations invalidate the pending read and need one fresh read after it.
        if (monitoringMounted.current && pending.revision !== refreshRevision.current)
          return refresh();
        return;
      }
      loadWorkers();
      const revision = refreshRevision.current;
      const selection = selectionRevision.current;
      const controller = new AbortController();
      requests.current.add(controller);
      const promise = api<Job[]>(
        '/training/jobs?summary=true',
        undefined,
        undefined,
        controller.signal,
      )
        .then((runs) => {
          if (!monitoringMounted.current || revision !== refreshRevision.current) return;
          if (!observedJobs.current)
            observedJobs.current = new Map(runs.map((job) => [job.id, job.status]));
          summaryRevision.current++;
          summaries.current = new Map(runs.map((job) => [job.id, job]));
          setLoaded(true);
          setLoadFailed(false);
          setMonitoringObservedAt(performance.now());
          setCreationOpen((previous) => previous ?? !runs.length);
          setModelsOpen((previous) => previous ?? !runs.length);
          setJobs(runs);
          if (selectionRevision.current === selection) {
            setSelectedJob((previous) => {
              const next = previous
                ? runs.find((job) => job.id === previous.id)
                : sortJobs(runs)[0];
              return next ? mergeJobSummary(previous, next) : null;
            });
            const id = currentSelection.current?.id;
            if (id && runs.find((job) => job.id === id)?.summary) void loadDetails(id);
          }
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted && revision === refreshRevision.current) throw error;
        })
        .finally(() => {
          requests.current.delete(controller);
          if (jobsRequest.current?.promise === promise) jobsRequest.current = null;
        });
      jobsRequest.current = { revision, promise };
      return promise;
    },
    [loadDetails, loadWorkers],
  );
  useEffect(() => {
    monitoringMounted.current = true;
    void refresh().catch((e) => {
      setLoadFailed(true);
      onMonitoringError.current(e.message);
    });
    const controller = new AbortController();
    requests.current.add(controller);
    // Hardware probing must not hold up job progress and is not repeated by polls.
    void api<DeviceInfo>('/training/device', undefined, undefined, controller.signal)
      .then((hardware) => {
        if (monitoringMounted.current && !controller.signal.aborted)
          setDevice({
            ...hardware,
            training_symmetry_modes: hardware.training_symmetry_modes ?? ['off', 'random_d4'],
          });
      })
      .catch((error: Error) => {
        if (monitoringMounted.current && !controller.signal.aborted)
          onMonitoringError.current(error.message);
      })
      .finally(() => requests.current.delete(controller));
    return () => {
      monitoringMounted.current = false;
      refreshRevision.current++;
      for (const request of requests.current) request.abort();
      jobsRequest.current = null;
      workersRequest.current = null;
      detailRequests.current.clear();
      detailControllers.current.clear();
    };
  }, [refresh]);
  useEffect(() => {
    for (const [id, controller] of detailControllers.current) {
      if (id !== selectedJob?.id) controller.abort();
    }
    if (selectedJob?.summary) void loadDetails(selectedJob.id);
  }, [selectedJob?.id, loadDetails]);
  const polling = jobs.some(activeJob) || workers.some((worker) => worker.status !== 'revoked');
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(
      () => void refresh().catch((e) => onMonitoringError.current(e.message)),
      2500,
    );
    return () => window.clearInterval(timer);
  }, [polling, refresh]);
  /** Drop the rejections that a form input identity owns. */
  const clearRejectedFields = (identity: string) =>
    setRejectedFields((previous) =>
      previous.filter((path) => fieldIdentity(path, kind) !== identity),
    );
  const clearAllRejectedFields = () => {
    setRejectedFields([]);
    setRejectedReason('');
  };
  const update = (key: string, value: string | number | boolean) => {
    setConfig((previous) => ({ ...previous, [key]: value }));
    clearRejectedFields(key);
  };
  const start = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setNotice('');
    clearAllRejectedFields();
    try {
      const params = launchPayload(kind, config, target, {
        trials,
        studyName,
        studySeed,
        space,
        strategy,
      });
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
      // A rejected field is the user's to fix: keep the values, name the input
      // and report it beside the form instead of only in a global notice.
      const rejected = rejectedTrainingFields(e);
      if (rejected.length) {
        setRejectedFields(rejected);
        // A locally detected problem explains itself; a server rejection uses the
        // workshop's own wording for the field it names.
        setRejectedReason(e instanceof TrainingInputError ? e.message : '');
        setNotice('');
      } else {
        onError((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  };
  const cancel = async (id: string) => {
    const revision = selectionRevision.current;
    try {
      const job = await api<Job>(`/training/jobs/${id}/cancel`, {});
      refreshRevision.current++;
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
        // A resumed study must keep its checkpoint source, opponent, search
        // space and strategy. Send the previous configuration unchanged and let
        // the server merge and validate it against the stored study identity;
        // stripping `training.resume` would silently restart from fresh weights.
        const saved = job.config ?? {};
        const trials =
          typeof saved.trials === 'number' && Number.isInteger(saved.trials) && saved.trials >= 1
            ? saved.trials
            : 3;
        const next = await api<Job>('/training/jobs', tuneResumePayload(job.id, saved, trials));
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
      const response = await apiFetch(`/training/jobs/${job.id}/export`);
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
  const inspect = (job: Job) => {
    selectJob(job);
    if (job.summary) void loadDetails(job.id);
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
  /** Replace the whole form configuration; stale field errors no longer apply. */
  const replaceConfig = (next: Record<string, string | number | boolean>) => {
    setConfig(next);
    clearAllRejectedFields();
  };
  const loadParameters = (model: Model) => {
    const loaded = recordedTrainingParameters(config, model, device);
    if (!loaded) return;
    replaceConfig(loaded.config);
    setKind('train');
    setTarget('local');
    setAdvanced(true);
    setCreationOpen(true);
    setNotice(
      t('Training parameters loaded from {name}. Unrecorded inputs keep their current values.', {
        name: model.name,
      }) + parameterWarnings(loaded.warnings),
    );
    requestAnimationFrame(() => trainingForm.current?.scrollIntoView?.({ block: 'start' }));
  };
  const parameterWarnings = (warnings: string[]) =>
    warnings.length
      ? ' ' +
        t('Invalid or unavailable settings kept safe values: {fields}. Review before starting.', {
          fields: warnings.map((key) => t(trainingParameterLabel(key))).join(', '),
        })
      : '';
  const importConfig = async (file: File) => {
    setImportingConfig(true);
    setNotice('');
    try {
      const imported = await readImportedConfig(file, config, { mode: 'create' });
      // Keep the imported target even when the current server or worker cannot
      // provide it, matching the resume dialog. The form shows the unavailable
      // option and starting reports the platform error so the user can adjust it.
      replaceConfig(imported.config);
      setKind(imported.kind);
      if (imported.kind === 'tune') {
        if (imported.trials !== undefined) setTrials(imported.trials);
        if (imported.studySeed !== undefined) setStudySeed(imported.studySeed);
        if (imported.studyName !== undefined) setStudyName(imported.studyName);
        if (imported.search !== undefined) setSpace(JSON.stringify(imported.search, null, 2));
      }
      setAdvanced(true);
      setCreationOpen(true);
      setNotice(
        t('Configuration imported. Review the values before starting.') +
          (imported.ignored.length
            ? ' ' +
              t('Fields that are not training parameters were ignored: {fields}.', {
                fields: imported.ignored.join(', '),
              })
            : ''),
      );
      requestAnimationFrame(() => trainingForm.current?.scrollIntoView?.({ block: 'start' }));
    } catch (error) {
      if (error instanceof ConfigImportError && error.fields.length)
        onError(
          t('Invalid configuration values: {fields}.', {
            fields: error.fields.map((key) => t(trainingParameterLabel(key))).join(', '),
          }),
        );
      else onError((error as Error).message);
    } finally {
      setImportingConfig(false);
    }
  };
  const loadJobParameters = (job: Job) => {
    const loaded = recordedJobParameters(config, job, device);
    if (!loaded) return;
    replaceConfig(loaded.config);
    setKind(loaded.kind);
    setTarget('local');
    if (loaded.kind === 'tune') {
      setTrials(loaded.trials);
      setStudySeed(loaded.seed);
      setStudyName(loaded.studyName);
      if (loaded.search) setSpace(JSON.stringify(loaded.search, null, 2));
      setStrategy(loaded.strategy ?? '');
    }
    setAdvanced(true);
    setCreationOpen(true);
    setNotice(
      t(
        'Parameters copied from job {name}. Unrecorded inputs keep their current values. Starting creates a new job.',
        { name: `${jobName(job)} (${job.id.slice(0, 8)})` },
      ) + parameterWarnings(loaded.warnings),
    );
    requestAnimationFrame(() => trainingForm.current?.scrollIntoView?.({ block: 'start' }));
  };
  /** The winning trial's full configuration, as persisted by the study. */
  const bestConfiguration = (job: Job): Record<string, unknown> | undefined => {
    const result = (job.result ?? {}) as Record<string, unknown>;
    const progress = (job.progress ?? {}) as Record<string, unknown>;
    const best = result.best_configuration ?? progress.best_configuration;
    return best && typeof best === 'object' ? (best as Record<string, unknown>) : undefined;
  };
  const bestTrialNumber = (job: Job): number | undefined => {
    const result = (job.result ?? {}) as Record<string, unknown>;
    const progress = (job.progress ?? {}) as Record<string, unknown>;
    const value = result.best_trial ?? progress.best_trial;
    return typeof value === 'number' && Number.isInteger(value) ? value : undefined;
  };
  /** Server-persisted full checkpoint of the winning trial, wherever it ran. */
  const bestTrialCheckpoint = (job: Job): string | undefined => {
    const result = (job.result ?? {}) as Record<string, unknown>;
    const progress = (job.progress ?? {}) as Record<string, unknown>;
    const value = result.best_trial_checkpoint ?? progress.best_trial_checkpoint;
    return typeof value === 'string' && value ? value : undefined;
  };
  /** Load the winning trial's hyperparameters as a new from-scratch training job. */
  const loadBestParameters = (job: Job) => {
    const best = bestConfiguration(job);
    if (!best) {
      onError(t('This job has no recorded best configuration.'));
      return;
    }
    const loaded = recordedTrainingParameters(
      config,
      {
        name: typeof best.model_name === 'string' ? best.model_name : jobName(job),
        ruleset: typeof best.ruleset === 'string' ? best.ruleset : jobRuleset(job),
        training_config: best,
      },
      device,
    );
    if (!loaded) return;
    replaceConfig(loaded.config);
    setKind('train');
    setTarget('local');
    setAdvanced(true);
    setCreationOpen(true);
    setNotice(
      t('Best parameters loaded from job {name}. Starting creates a new training job.', {
        name: jobName(job),
      }) + parameterWarnings(loaded.warnings),
    );
    requestAnimationFrame(() => trainingForm.current?.scrollIntoView?.({ block: 'start' }));
  };
  /** Launch a new training job that resumes the winning trial's full checkpoint. */
  const continueWinningTrial = async (job: Job, additionalIterations: number) => {
    const best = bestConfiguration(job);
    const trial = bestTrialNumber(job);
    // The winning trial may belong to an earlier job that shares this study, so
    // use the checkpoint the server persisted instead of rebuilding the path
    // from the current job ID.
    const checkpoint = bestTrialCheckpoint(job);
    if (!best || trial === undefined || !checkpoint) {
      onError(t('This job has no recorded winning trial checkpoint.'));
      return;
    }
    setBusy(true);
    setNotice('');
    try {
      const {
        output_dir: _output,
        registry_dir: _registry,
        resume: _resume,
        storage: _storage,
        ...training
      } = best;
      const winningIteration = Number(
        job.result?.best_iteration ?? job.progress?.best_iteration ?? best.iterations ?? 1,
      );
      const iterations = winningIteration + Math.max(1, additionalIterations);
      const next = await api<Job>('/training/jobs', {
        ...training,
        kind: 'train',
        iterations,
        resume: checkpoint,
      });
      refreshRevision.current++;
      selectJob(next);
      showMonitoring();
      setJobs((previous) => [next, ...previous]);
      setNotice(
        t('Continuing the winning trial from its full checkpoint. A new training job has started.'),
      );
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  /**
   * Review the winning settings in the existing Resume Training dialog before
   * they are applied to another job. Nothing is submitted until the user
   * confirms, and immutable checkpoint identity is preserved by the dialog.
   */
  const applyBestToJob = (tuningJob: Job, targetJobId: string, additionalIterations: number) => {
    const best = bestConfiguration(tuningJob);
    if (!best) {
      onError(t('This job has no recorded best configuration.'));
      return;
    }
    const target = jobs.find((entry) => entry.id === targetJobId);
    if (!target) {
      onError(t('The selected training job could not be found.'));
      return;
    }
    const optimized = (tuningJob.result?.best_parameters ??
      tuningJob.progress?.best_parameters ??
      best) as Record<string, unknown>;
    setResumePrefill(
      Object.fromEntries(
        Object.entries(optimized).filter(([key]) => tunable.includes(key)),
      ) as TrainingConfig,
    );
    setResumeAdditional(Math.max(1, additionalIterations));
    setResumeJob(target);
  };
  const downloadBestConfig = (job: Job) => {
    const best = bestConfiguration(job);
    if (!best) {
      onError(t('This job has no recorded best configuration.'));
      return;
    }
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(best, null, 2)], { type: 'application/json' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `hnefatafl-best-config-${job.id.slice(0, 8)}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  /** Tune from a training job's full checkpoint so every trial starts from it. */
  /** Open the checkpoint-tuning review dialog instead of starting immediately. */
  const tuneFromCheckpoint = (job: Job) => {
    setTuneJob(job);
  };
  const removeModel = async () => {
    if (
      !deleteModel ||
      !modelUsage ||
      usageLoading ||
      modelUsage.running_tournaments.length ||
      modelUsage.running_optimizations?.length ||
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
    setJobTab(defaultJobTab);
    setDashboardRevision((value) => value + 1);
    requestAnimationFrame(() => {
      monitoring.current?.focus({ preventScroll: true });
      monitoring.current?.scrollIntoView?.({ block: 'start' });
    });
  };
  const running = jobs.filter(activeJob).length;
  const hardwareName = device.default_device ?? device.selected ?? device.device ?? 'CPU';
  const activeJobTab = selectedJob
    ? resolveJobTab(jobTab, availableJobTabs(selectedJob))
    : defaultJobTab;
  return (
    <div className="training-workshop">
      <section className="page-toolbar workshop-intro">
        <PageHeading title={t('The AI workshop.')} />
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
          onChanged={(revokedId) => {
            if (revokedId)
              setWorkers((previous) => previous.filter((worker) => worker.id !== revokedId));
            void refresh().catch((error) => onError(error.message));
          }}
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
              studySeed,
              setStudySeed,
              studyName,
              setStudyName,
              space,
              setSpace,
              strategy,
              setStrategy,
              advanced,
              setAdvanced,
              busy,
              models,
              rulesets,
              workers,
              device,
              start,
              importingConfig,
              onImportConfig: (file: File) => void importConfig(file),
              rejectedFields,
              rejectedReason,
              onFieldCorrected: clearRejectedFields,
              onFieldsCleared: clearAllRejectedFields,
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
                  key={selectedJob.id}
                  job={selectedJob}
                  observedAt={monitoringObservedAt}
                  tab={activeJobTab}
                  onTabChange={setJobTab}
                  revision={dashboardRevision}
                  busy={busy}
                  exporting={exportingJob}
                  canUseParameters={Boolean(recordedJobParameters(config, selectedJob))}
                  onUseParameters={() => loadJobParameters(selectedJob)}
                  onTuneCheckpoint={() => void tuneFromCheckpoint(selectedJob)}
                  trainingJobs={jobs}
                  onUseBestParameters={() => loadBestParameters(selectedJob)}
                  onContinueTrial={(additional) =>
                    void continueWinningTrial(selectedJob, additional)
                  }
                  onApplyToJob={(targetId, additional) =>
                    void applyBestToJob(selectedJob, targetId, additional)
                  }
                  onDownloadBestConfig={() => downloadBestConfig(selectedJob)}
                  onCancel={() => void cancel(selectedJob.id)}
                  onResume={() => void resume(selectedJob)}
                  onExport={() => void exportTrainingJob(selectedJob)}
                  onRefresh={() => void refresh().catch((error) => onError(error.message))}
                  onDelete={() => {
                    setDeleteJob(selectedJob);
                    setDeleteJobError('');
                  }}
                  onDeleteStudy={
                    account?.permissions.includes('training')
                      ? () => setDeleteStudyJob(selectedJob)
                      : undefined
                  }
                  onRename={() => setRenameJob(selectedJob)}
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
      {renameJob && (
        <JobRenameDialog
          key={renameJob.id}
          job={renameJob}
          onClose={() => setRenameJob(null)}
          onRenamed={(updated) => {
            refreshRevision.current++;
            setRenameJob(null);
            setJobs((previous) => previous.map((job) => (job.id === updated.id ? updated : job)));
            setSelectedJob((previous) => (previous?.id === updated.id ? updated : previous));
            setNotice(
              t(
                'Training job renamed. Its identity, checkpoint, parameters and execution are unchanged.',
              ),
            );
            void refresh().catch((error) => onError(error.message));
          }}
        />
      )}
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
              {t(
                'Shared studies may block individual deletion. Use “Delete entire tuning study” to review and remove the whole study.',
              )}
            </p>
          )}
        </RecordsConfirm>
      )}
      {deleteStudyJob && account?.permissions.includes('training') && (
        <StudyDeletionDialog
          jobId={deleteStudyJob.id}
          onClose={() => setDeleteStudyJob(null)}
          onDeleted={(ids, cleanupPending) => {
            refreshRevision.current++;
            for (const id of ids) {
              detailControllers.current.get(id)?.abort();
              detailControllers.current.delete(id);
              detailRequests.current.delete(id);
              summaries.current.delete(id);
              observedJobs.current?.delete(id);
            }
            setJobs((previous) => previous.filter((job) => !ids.includes(job.id)));
            if (currentSelection.current && ids.includes(currentSelection.current.id))
              selectJob(null);
            setDeleteStudyJob(null);
            setNotice(
              t(
                cleanupPending
                  ? 'Study deleted. Remaining quarantined files will be cleaned up on the next deletion preview or server restart.'
                  : 'Study and contributing tuning jobs deleted. Source jobs, registered models and saved game history have been kept.',
              ),
            );
            void refresh().catch((error: Error) => onError(error.message));
          }}
        />
      )}
      {resumeJob && (
        <TrainingResumeDialog
          localDevice={device}
          job={resumeJob}
          workers={workers}
          prefill={resumePrefill ?? undefined}
          additionalIterations={resumeAdditional}
          onClose={() => {
            setResumeJob(null);
            setResumePrefill(null);
            setResumeAdditional(undefined);
          }}
          onStarted={(next, completed, target) => {
            setResumeJob(null);
            setResumePrefill(null);
            setResumeAdditional(undefined);
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
      {tuneJob && (
        <TrainingTuneDialog
          models={models}
          job={tuneJob}
          onClose={() => setTuneJob(null)}
          onStarted={(next) => {
            setTuneJob(null);
            refreshRevision.current++;
            if (!observedJobs.current) observedJobs.current = new Map();
            observedJobs.current.set(next.id, next.status);
            selectJob(next);
            showMonitoring();
            setJobs((previous) => [next, ...previous]);
            setNotice(
              t(
                'Tuning started from the saved checkpoint. Every trial begins from the same state.',
              ),
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
            !!modelUsage.running_optimizations?.length ||
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
              {!!modelUsage.running_optimizations?.length && (
                <div className="model-usage-warning">
                  <h3>{t('Running optimization studies')}</h3>
                  <p>{t('Stop these studies before deleting their fixed opponent.')}</p>
                  <ul>
                    {modelUsage.running_optimizations.map((study) => (
                      <li key={study.id}>
                        {study.name} ({study.id.slice(0, 8)})
                      </li>
                    ))}
                  </ul>
                </div>
              )}
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
                      disabled={
                        deleting ||
                        !!modelUsage.running_tournaments.length ||
                        !!modelUsage.running_optimizations?.length
                      }
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
