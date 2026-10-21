import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import { Metric } from './Archive';
import type { DeviceInfo, Job, Model, Ruleset } from './types';

const defaults: Record<string, string | number | boolean> = {
  ruleset: 'tablut',
  preset: 'smoke',
  channels: 4,
  blocks: 0,
  iterations: 1,
  games: 1,
  epochs: 1,
  batch_size: 8,
  learning_rate: 0.001,
  optimizer: 'adamw',
  mcts_iterations: 2,
  mcts_time_limit: 0.1,
  exploration: 1.4,
  replay_buffer_size: 2048,
  weight_decay: 0.0001,
  checkpoint_frequency: 1,
  evaluation_frequency: 1,
  evaluation_games: 2,
  evaluation_iterations: 2,
  seed: 42,
  device: 'auto',
  mixed_precision: true,
  max_game_plies: 12,
  evaluation_max_plies: 12,
  model_name: 'My first strategist',
  temperature: 1,
  cpu_threads: 2,
  dirichlet_alpha: 0.3,
  dirichlet_fraction: 0.25,
};
const metricText = (value: unknown) =>
  typeof value === 'number'
    ? Number.isInteger(value)
      ? String(value)
      : value.toFixed(4)
    : typeof value === 'string'
      ? value
      : '—';

export default function Training({
  rulesets,
  models,
  onModelsChanged,
  onError,
}: {
  rulesets: Ruleset[];
  models: Model[];
  onModelsChanged: () => void;
  onError: (error: string) => void;
}) {
  const [device, setDevice] = useState<DeviceInfo>({});
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [config, setConfig] = useState(defaults);
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
  const refresh = useCallback(async () => {
    const [hardware, runs] = await Promise.all([
      api<DeviceInfo>('/training/device'),
      api<Job[]>('/training/jobs'),
    ]);
    setDevice(hardware);
    setJobs(runs);
  }, []);
  useEffect(() => {
    void refresh().catch((e) => onError(e.message));
  }, [refresh, onError]);
  useEffect(() => {
    if (!jobs.some((job) => ['running', 'pending', 'queued', 'cancelling'].includes(job.status)))
      return;
    const timer = window.setInterval(() => void refresh().catch((e) => onError(e.message)), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, refresh, onError]);
  useEffect(() => {
    if (
      !selectedJob ||
      !['running', 'pending', 'queued', 'cancelling'].includes(selectedJob.status)
    )
      return;
    const timer = window.setInterval(
      () =>
        void api<Job>(`/training/jobs/${selectedJob.id}`)
          .then((job) => {
            setSelectedJob(job);
            if (job.status === 'completed') onModelsChanged();
          })
          .catch((e) => onError(e.message)),
      2000,
    );
    return () => window.clearInterval(timer);
  }, [selectedJob, onModelsChanged, onError]);
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
          : { ...config, kind };
      const job = await api<Job>('/training/jobs', params);
      setJobs((previous) => [job, ...previous]);
      setSelectedJob(job);
      setNotice('Your training job has started. You can keep playing while it runs.');
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async (id: string) => {
    try {
      const job = await api<Job>(`/training/jobs/${id}/cancel`, {});
      setSelectedJob(job);
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const resume = async (job: Job) => {
    if (job.kind === 'tune') {
      setBusy(true);
      try {
        const next = await api<Job>('/training/jobs', {
          ...job.config,
          kind: 'tune',
          resume_study: job.id,
        });
        setSelectedJob(next);
        await refresh();
      } catch (e) {
        onError((e as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }
    const checkpoint = job.checkpoint ?? job.model_id;
    if (!checkpoint) {
      onError('This job has no saved checkpoint yet.');
      return;
    }
    setBusy(true);
    try {
      const next = await api<Job>('/training/jobs', {
        ...job.config,
        iterations: Number(job.config?.iterations ?? 1) + 1,
        resume: checkpoint,
        kind: 'train',
      });
      setSelectedJob(next);
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
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
      setNotice('Model imported. It is now available in compatible games.');
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setImporting(false);
    }
  };
  const inspect = async (job: Job) => {
    try {
      setSelectedJob(await api<Job>(`/training/jobs/${job.id}`));
    } catch (e) {
      onError((e as Error).message);
    }
  };
  const running = jobs.filter((job) =>
    ['running', 'pending', 'queued', 'cancelling'].includes(job.status),
  ).length;
  const metrics = selectedJob?.progress ?? selectedJob?.metrics ?? {};
  const hardwareName = device.default_device ?? device.selected ?? device.device ?? 'CPU';
  return (
    <>
      <section className="page-intro">
        <div>
          <p className="eyebrow">CRAFT A WORTHY OPPONENT</p>
          <h1>The AI workshop.</h1>
          <p>
            From first principles to your own strategist. Train, evaluate, and bring it to the
            table.
          </p>
        </div>
        <span className="device-tag">
          <span className="status-dot" />
          {device.cuda_available ? 'CUDA available' : 'CPU training available'}
        </span>
      </section>
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button aria-label="Dismiss notice" onClick={() => setNotice('')}>
            ×
          </button>
        </div>
      )}
      <div className="metric-grid three">
        <Metric
          label="TRAINING DEVICE"
          value={hardwareName.toUpperCase()}
          detail={
            device.cuda_available
              ? 'NVIDIA acceleration is available'
              : 'Automatic CPU fallback · CUDA supported'
          }
        />
        <Metric
          label="ACTIVE JOBS"
          value={running}
          detail="Training runs independently of your games"
        />
        <Metric
          label="TRAINED MODELS"
          value={models.length}
          detail="Ready to join a compatible battlefield"
        />
      </div>
      <div className="training-layout">
        <section className="panel training-config">
          <div className="panel-title">
            <h2>Forge a new strategist</h2>
            <span className="badge">SELF-PLAY</span>
          </div>
          <p className="subtle">
            A policy and value network learns from its own games. Small runs verify the workflow;
            strength takes time and evaluation.
          </p>
          <form onSubmit={start}>
            <div className="segmented">
              <button
                type="button"
                className={kind === 'train' ? 'active' : ''}
                onClick={() => setKind('train')}
              >
                Train a model
              </button>
              <button
                type="button"
                className={kind === 'tune' ? 'active' : ''}
                onClick={() => setKind('tune')}
              >
                Tune parameters
              </button>
            </div>
            <div className="form-grid">
              <label>
                Battlefield
                <select
                  value={String(config.ruleset)}
                  onChange={(e) => update('ruleset', e.target.value)}
                >
                  {rulesets.map((rule) => (
                    <option value={rule.id} key={rule.id}>
                      {rule.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Training preset
                <select
                  value={String(config.preset)}
                  onChange={(e) => {
                    const preset = e.target.value;
                    setConfig((previous) => ({
                      ...previous,
                      preset,
                      ...(preset === 'smoke'
                        ? {
                            iterations: 1,
                            games: 1,
                            mcts_iterations: 2,
                            batch_size: 8,
                            channels: 4,
                            blocks: 0,
                            max_game_plies: 12,
                            evaluation_max_plies: 12,
                            evaluation_iterations: 2,
                          }
                        : preset === 'beginner'
                          ? {
                              iterations: 10,
                              games: 8,
                              mcts_iterations: 32,
                              batch_size: 64,
                              channels: 32,
                              blocks: 2,
                              max_game_plies: 150,
                              evaluation_max_plies: 150,
                              evaluation_iterations: 16,
                            }
                          : {
                              iterations: 50,
                              games: 32,
                              mcts_iterations: 128,
                              batch_size: 128,
                              channels: 64,
                              blocks: 4,
                              max_game_plies: 300,
                              evaluation_max_plies: 300,
                              evaluation_iterations: 64,
                            }),
                    }));
                  }}
                >
                  <option value="smoke">Quick check · a tiny real training run</option>
                  <option value="beginner">Beginner · a starting point</option>
                  <option value="balanced">Balanced · a longer experiment</option>
                </select>
              </label>
              <label>
                Model name
                <input
                  required
                  maxLength={80}
                  value={String(config.model_name)}
                  onChange={(e) => update('model_name', e.target.value)}
                />
              </label>
              <label>
                Device
                <select
                  value={String(config.device)}
                  onChange={(e) => update('device', e.target.value)}
                >
                  <option value="auto">Automatic</option>
                  <option value="cpu">CPU</option>
                  <option value="cuda" disabled={!device.cuda_available}>
                    NVIDIA CUDA{!device.cuda_available ? ' (unavailable)' : ''}
                  </option>
                  {device.mps_available && <option value="mps">Apple Metal</option>}
                </select>
              </label>
              {numberField('iterations', 'Training iterations', config, update, 1, 10000)}
              {numberField('games', 'Self-play games per iteration', config, update, 1, 10000)}
              {numberField('batch_size', 'Batch size', config, update, 1, 2048)}
              {numberField('mcts_iterations', 'MCTS iterations per move', config, update, 1, 10000)}
            </div>
            <button
              type="button"
              className="text-button advanced-toggle"
              onClick={() => setAdvanced((value) => !value)}
              aria-expanded={advanced}
            >
              {advanced ? '−' : '＋'} Advanced configuration
            </button>
            {advanced && (
              <div className="advanced-config form-grid">
                {numberField('channels', 'Network channels', config, update, 4, 128)}
                {numberField('blocks', 'Residual blocks', config, update, 0, 8)}
                <label>
                  Optimizer
                  <select
                    value={String(config.optimizer)}
                    onChange={(e) => update('optimizer', e.target.value)}
                  >
                    <option value="adamw">AdamW</option>
                    <option value="adam">Adam</option>
                    <option value="sgd">SGD</option>
                  </select>
                </label>
                {numberField('epochs', 'Epochs per iteration', config, update, 1, 100)}
                {numberField('learning_rate', 'Learning rate', config, update, 0.000001, 1, 0.0001)}
                {numberField(
                  'mcts_time_limit',
                  'Search time limit (seconds)',
                  config,
                  update,
                  0.01,
                  30,
                  0.01,
                )}
                {numberField('exploration', 'MCTS exploration', config, update, 0, 10, 0.1)}
                {numberField('temperature', 'Self-play temperature', config, update, 0.01, 3, 0.01)}
                {numberField(
                  'dirichlet_alpha',
                  'Exploration noise alpha',
                  config,
                  update,
                  0.01,
                  10,
                  0.01,
                )}
                {numberField(
                  'dirichlet_fraction',
                  'Exploration noise fraction',
                  config,
                  update,
                  0,
                  1,
                  0.05,
                )}
                {numberField(
                  'replay_buffer_size',
                  'Replay buffer positions',
                  config,
                  update,
                  1,
                  20000,
                )}
                {numberField('weight_decay', 'Weight regularization', config, update, 0, 1, 0.0001)}
                {numberField(
                  'checkpoint_frequency',
                  'Checkpoint every (iterations)',
                  config,
                  update,
                  1,
                  10000,
                )}
                {numberField(
                  'evaluation_frequency',
                  'Evaluate every (iterations)',
                  config,
                  update,
                  1,
                  10000,
                )}
                {numberField(
                  'evaluation_games',
                  'Evaluation games (even)',
                  config,
                  update,
                  2,
                  1000,
                  2,
                )}
                {numberField(
                  'evaluation_iterations',
                  'Evaluation search iterations',
                  config,
                  update,
                  1,
                  10000,
                )}
                {numberField('max_game_plies', 'Self-play move limit', config, update, 10, 2000)}
                {numberField(
                  'evaluation_max_plies',
                  'Evaluation move limit',
                  config,
                  update,
                  10,
                  2000,
                )}
                {numberField('cpu_threads', 'CPU threads', config, update, 1, 64)}
                {numberField('seed', 'Random seed', config, update, 0, 2147483647)}
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={Boolean(config.mixed_precision)}
                    onChange={(e) => update('mixed_precision', e.target.checked)}
                  />
                  Mixed precision when supported
                </label>
              </div>
            )}
            {kind === 'tune' && (
              <div className="tuning-config">
                <p className="subtle">
                  Trials optimize game results against a baseline. Tuning can take much longer than
                  a single run. Resume a saved study from its job below.
                </p>
                <div className="form-grid">
                  <label>
                    Trial budget
                    <input
                      type="number"
                      min="1"
                      max="1000"
                      value={trials}
                      onChange={(e) => setTrials(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Study name
                    <input
                      required
                      value={studyName}
                      onChange={(e) => setStudyName(e.target.value)}
                    />
                  </label>
                </div>
                <label>
                  Parameter search space (JSON)
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
              <span>Start small. Measure improvement.</span>
              <button className="button primary" disabled={busy}>
                {busy ? 'Starting…' : kind === 'train' ? 'Start training' : 'Start tuning'}{' '}
                <span aria-hidden="true">↗</span>
              </button>
            </div>
          </form>
        </section>
        <section className="panel jobs-panel">
          <div className="panel-title">
            <h2>At the forge</h2>
            <button
              className="text-button"
              onClick={() => {
                void refresh().catch((e) => onError(e.message));
                onModelsChanged();
              }}
            >
              Refresh
            </button>
          </div>
          {!jobs.length ? (
            <div className="empty-state compact">
              <span className="empty-rune">ᛏ</span>
              <h3>Your next opponent starts here.</h3>
              <p>
                Start a quick check to run real self-play, training, evaluation, and checkpoint
                creation.
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
                  <span className={`job-status ${job.status}`}>{job.status}</span>
                  <strong>
                    {String(
                      job.config?.model_name ??
                        job.config?.ruleset ??
                        job.ruleset ??
                        'Training job',
                    )}
                  </strong>
                  <small>
                    {job.created_at
                      ? new Date(job.created_at).toLocaleString()
                      : job.id.slice(0, 8)}
                  </small>
                </button>
              ))}
            </div>
          )}
          {selectedJob && (
            <div className="job-detail">
              <div className="panel-title">
                <h3>Training progress</h3>
                <span className={`job-status ${selectedJob.status}`}>{selectedJob.status}</span>
              </div>
              <div className="job-metrics">
                {Object.entries(metrics)
                  .filter(
                    ([key, value]) =>
                      !['checkpoint', 'best_checkpoint', 'inference_checkpoint'].includes(key) &&
                      ['number', 'string'].includes(typeof value),
                  )
                  .map(([key, value]) => (
                    <div key={key}>
                      <span>{key.replaceAll('_', ' ')}</span>
                      <strong>{metricText(value)}</strong>
                    </div>
                  ))}
              </div>
              {metrics.metrics != null && typeof metrics.metrics === 'object' && (
                <details>
                  <summary>Evaluation metrics</summary>
                  <pre className="log-view">{JSON.stringify(metrics.metrics, null, 2)}</pre>
                </details>
              )}
              {selectedJob.error && (
                <p className="form-error" role="alert">
                  {selectedJob.error}
                </p>
              )}
              <div className="job-actions">
                {['running', 'pending', 'queued', 'cancelling'].includes(selectedJob.status) ? (
                  <button
                    className="button secondary small"
                    onClick={() => void cancel(selectedJob.id)}
                    disabled={selectedJob.status === 'cancelling'}
                  >
                    {selectedJob.status === 'cancelling' ? 'Stopping…' : 'Stop training'}
                  </button>
                ) : (
                  <button
                    className="button secondary small"
                    onClick={() => void resume(selectedJob)}
                    disabled={
                      busy ||
                      (selectedJob.kind !== 'tune' &&
                        !(selectedJob.checkpoint || selectedJob.model_id))
                    }
                  >
                    {selectedJob.kind === 'tune' ? 'Resume study' : 'Resume from checkpoint'}
                  </button>
                )}
              </div>
              <details>
                <summary>Training log</summary>
                <pre className="log-view">
                  {Array.isArray(selectedJob.logs)
                    ? selectedJob.logs.join('\n')
                    : selectedJob.logs || 'No log output yet.'}
                </pre>
              </details>
            </div>
          )}
        </section>
      </div>
      <section className="panel model-registry">
        <div className="panel-title">
          <div>
            <p className="eyebrow">YOUR STRATEGISTS</p>
            <h2>Model collection</h2>
          </div>
          <label className="button secondary file-button">
            {importing ? 'Importing…' : '↑ Import model'}
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
          Compatible checkpoints are validated before registration. Select a trained model under
          “Neural network” when starting a game.
        </p>
        {models.length ? (
          <div className="models-grid">
            {models.map((model) => (
              <article key={model.id} className="model-card">
                <div className="model-symbol">ᛉ</div>
                <div>
                  <h3>{model.name}</h3>
                  <p>
                    {model.ruleset} ·{' '}
                    {model.version == null ? 'Policy / value' : String(model.version)}
                  </p>
                  <small>
                    {model.created_at ? new Date(model.created_at).toLocaleDateString() : model.id}
                  </small>
                </div>
                <a
                  className="icon-button"
                  href={`/api/models/${model.id}/export`}
                  download
                  aria-label={`Export ${model.name}`}
                >
                  ↓
                </a>
                {model.evaluation && (
                  <div className="model-evaluation">
                    <span className="eyebrow">
                      AGAINST {model.evaluation.opponent ?? 'BASELINE'}
                    </span>
                    <div>
                      <span>
                        <strong>{model.evaluation.wins ?? 0}</strong> wins
                      </span>
                      <span>
                        <strong>{model.evaluation.draws ?? 0}</strong> draws
                      </span>
                      <span>
                        <strong>{model.evaluation.losses ?? 0}</strong> losses
                      </span>
                    </div>
                    <small>
                      {model.evaluation.truncated ?? 0} unfinished games · limited evaluation
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
                          {key.replaceAll('_', ' ')} <strong>{metricText(value)}</strong>
                        </span>
                      ))}
                  </div>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="empty-copy">Your trained and imported models will live here.</p>
        )}
      </section>
    </>
  );
}
function numberField(
  key: string,
  label: string,
  config: Record<string, string | number | boolean>,
  update: (key: string, value: number) => void,
  min: number,
  max: number,
  step = 1,
) {
  return (
    <label key={key}>
      {label}
      <input
        type="number"
        required
        min={min}
        max={max}
        step={step}
        value={Number(config[key])}
        onChange={(e) => update(key, Number(e.target.value))}
      />
    </label>
  );
}
