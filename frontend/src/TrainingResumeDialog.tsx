import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import type { Job, TrainingWorker } from './types';
import TrainingTarget, { jobTarget, targetRequest } from './TrainingTarget';
import {
  numberField,
  SelfPlayModeField,
  trainingNumberFields,
  validTrainingNumbers,
} from './TrainingFields';
import { changeTrainingPlan, type TrainingConfig, type TrainingPreset } from './trainingPresets';

export interface ResumeCheckpoint {
  job_id: string;
  checkpoint?: string;
  completed_iteration: number;
  ruleset: string;
  channels: number;
  blocks: number;
  optimizer: string;
  seed?: number;
  config?: TrainingConfig;
  learning_rate?: number;
  weight_decay?: number;
  momentum?: number;
  model_name: string;
  can_resume: boolean;
  reason?: string;
}

export default function TrainingResumeDialog({
  job,
  workers = [],
  onClose,
  onStarted,
}: {
  job: Job;
  workers?: TrainingWorker[];
  onClose: () => void;
  onStarted: (next: Job, completed: number, target: number) => void;
}) {
  const { t } = useI18n();
  const ruleNames: Record<string, string> = {
    brandubh: 'Brandubh',
    tablut: 'Tablut',
    copenhagen: 'Copenhagen',
  };
  const optimizerNames: Record<string, string> = { adamw: 'AdamW', adam: 'Adam', sgd: 'SGD' };
  const ref = useRef<HTMLDialogElement>(null);
  const [checkpoint, setCheckpoint] = useState<ResumeCheckpoint | null>(null);
  const [config, setConfig] = useState<TrainingConfig>({});
  const savedConfig = useRef<TrainingConfig>({});
  const update = (key: string, value: string | number | boolean) =>
    setConfig((previous) => ({ ...previous, [key]: value }));
  const target = Number(config.iterations);
  const [executionTarget, setExecutionTarget] = useState(() => jobTarget(job));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    void api<ResumeCheckpoint>(`/training/jobs/${job.id}/resume`)
      .then((info) => {
        if (!alive) return;
        setCheckpoint(info);
        // The API returns effective checkpoint values, including optimizer groups.
        // Older servers can still supply their original job config and metadata.
        const saved: TrainingConfig = {};
        for (const [key, value] of Object.entries({ ...job.config, ...info.config })) {
          if (['string', 'number', 'boolean'].includes(typeof value))
            saved[key] = value as string | number | boolean;
        }
        saved.ruleset = info.ruleset;
        saved.channels = info.channels;
        saved.blocks = info.blocks;
        saved.optimizer = info.optimizer;
        if (info.seed !== undefined) saved.seed = info.seed;
        saved.learning_rate = info.learning_rate ?? saved.learning_rate ?? 0.001;
        saved.weight_decay = info.weight_decay ?? saved.weight_decay ?? 0.0001;
        saved.momentum = info.momentum ?? saved.momentum ?? 0.9;
        saved.model_name = info.model_name;
        savedConfig.current = saved;
        const priorTarget = Number(saved.iterations);
        setConfig({
          ...saved,
          iterations: Math.max(
            info.completed_iteration + 1,
            Number.isInteger(priorTarget) ? priorTarget : 1,
          ),
        });
      })
      .catch((error) => {
        if (alive) setError((error as Error).message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [job]);
  const valid =
    checkpoint?.can_resume &&
    Number.isInteger(target) &&
    target > checkpoint.completed_iteration &&
    target <= 100000 &&
    validTrainingNumbers(config) &&
    (config.model_name === undefined ||
      (String(config.model_name).length >= 1 && String(config.model_name).length <= 100)) &&
    (!executionTarget.startsWith('worker:') ||
      workers.some(
        (worker) => `worker:${worker.id}` === executionTarget && worker.status !== 'revoked',
      ));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!checkpoint || !valid) return;
    setBusy(true);
    setError('');
    try {
      const next = await api<Job>(`/training/jobs/${job.id}/resume`, {
        iterations: target,
        ...Object.fromEntries(
          Object.entries(config).filter(
            ([key, value]) =>
              !['ruleset', 'channels', 'blocks', 'seed', 'optimizer', 'iterations'].includes(key) &&
              (key in trainingNumberFields ||
                [
                  'model_name',
                  'preset',
                  'device',
                  'mixed_precision',
                  'save_all_best',
                  'self_play_mode',
                ].includes(key)) &&
              value !== savedConfig.current[key],
          ),
        ),
        ...(executionTarget !== jobTarget(job) ? targetRequest(executionTarget) : {}),
      });
      onStarted(next, checkpoint.completed_iteration, target);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal training-resume-dialog"
      aria-label={t('Resume training')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-top">
          <div>
            <h2>{t('Resume training')}</h2>
            <p>{checkpoint?.model_name ?? String(job.config?.model_name ?? t('Training job'))}</p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close resume training')}
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        {loading ? (
          <p role="status">{t('Checking the saved training checkpoint…')}</p>
        ) : (
          checkpoint && (
            <>
              <dl className="training-resume-details">
                <div>
                  <dt>{t('Completed iterations')}</dt>
                  <dd>{checkpoint.completed_iteration}</dd>
                </div>
                <div>
                  <dt>{t('Battlefield')}</dt>
                  <dd>{t(ruleNames[checkpoint.ruleset] ?? checkpoint.ruleset)}</dd>
                </div>
                <div>
                  <dt>
                    {t('Network channels')} · {t('Residual blocks')}
                  </dt>
                  <dd>
                    {t('{channels} channels · {blocks} blocks', {
                      channels: checkpoint.channels,
                      blocks: checkpoint.blocks,
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t('Random seed')}</dt>
                  <dd>{config.seed ?? '—'}</dd>
                </div>
                <div>
                  <dt>{t('Optimizer')}</dt>
                  <dd>{optimizerNames[checkpoint.optimizer] ?? checkpoint.optimizer}</dd>
                </div>
              </dl>
              <p className="subtle">
                {t(
                  'Continues the latest full checkpoint, including optimizer state, replay, random state and learning history.',
                )}
              </p>
              {checkpoint.can_resume ? (
                <>
                  <TrainingTarget
                    value={executionTarget}
                    workers={workers}
                    onChange={setExecutionTarget}
                    disabled={busy}
                  />
                  <div className="form-grid resume-optimizer-settings">
                    {numberField('iterations', config, update, {
                      label: 'Target total iterations',
                      min: checkpoint.completed_iteration + 1,
                      disabled: busy,
                    })}
                    <small>
                      {t('This is the total iteration count, not the number of extra iterations.')}
                    </small>
                    {config.model_name !== undefined && (
                      <label>
                        {t('Model name')}
                        <input
                          required
                          maxLength={100}
                          value={String(config.model_name)}
                          disabled={busy}
                          onChange={(event) => update('model_name', event.target.value)}
                        />
                      </label>
                    )}
                    {config.preset !== undefined && (
                      <label>
                        {t('Training preset')}
                        <select
                          value={String(config.preset)}
                          disabled={busy}
                          onChange={(event) => {
                            const preset = event.target.value as TrainingPreset;
                            setConfig((previous) => {
                              const next = changeTrainingPlan(previous, checkpoint.ruleset, preset);
                              for (const key of [
                                'ruleset',
                                'channels',
                                'blocks',
                                'seed',
                                'optimizer',
                              ])
                                next[key] = savedConfig.current[key];
                              next.iterations = Math.max(
                                checkpoint.completed_iteration + 1,
                                Number(next.iterations),
                              );
                              return next;
                            });
                          }}
                        >
                          <option value="recommended">
                            {t('Recommended · tailored to this battlefield')}
                          </option>
                          <option value="smoke">
                            {t('Quick check · a tiny real training run')}
                          </option>
                          <option value="beginner">{t('Beginner · a starting point')}</option>
                          <option value="balanced">{t('Balanced · a longer experiment')}</option>
                        </select>
                      </label>
                    )}
                    {config.device !== undefined && (
                      <label>
                        {t('Device')}
                        <select
                          value={String(config.device)}
                          disabled={busy}
                          onChange={(event) => update('device', event.target.value)}
                        >
                          <option value="auto">{t('Automatic')}</option>
                          <option value="cpu">{t('CPU')}</option>
                          <option value="cuda">{t('NVIDIA CUDA')}</option>
                          <option value="mps">{t('Apple Metal')}</option>
                        </select>
                      </label>
                    )}
                    {config.self_play_mode !== undefined && (
                      <SelfPlayModeField
                        value={String(config.self_play_mode)}
                        onChange={(value) => update('self_play_mode', value)}
                        disabled={busy}
                      />
                    )}
                    {Object.keys(trainingNumberFields)
                      .filter(
                        (key) =>
                          !['iterations', 'channels', 'blocks', 'seed'].includes(key) &&
                          config[key] !== undefined,
                      )
                      .map((key) =>
                        numberField(key, config, update, {
                          label: key === 'weight_decay' ? 'Weight decay' : undefined,
                          disabled: busy || (key === 'momentum' && checkpoint.optimizer !== 'sgd'),
                        }),
                      )}
                    <small>
                      {t('Momentum applies to SGD only. Adam and AdamW keep their saved moments.')}
                    </small>
                    {config.mixed_precision !== undefined && (
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={Boolean(config.mixed_precision)}
                          disabled={busy}
                          onChange={(event) => update('mixed_precision', event.target.checked)}
                        />
                        {t('Mixed precision when supported')}
                      </label>
                    )}
                    {config.save_all_best !== undefined && (
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={Boolean(config.save_all_best)}
                          disabled={busy}
                          onChange={(event) => update('save_all_best', event.target.checked)}
                        />
                        {t('Save every new best model')}
                      </label>
                    )}
                  </div>
                </>
              ) : (
                <p className="form-error" role="alert">
                  {t(checkpoint.reason ?? 'No resumable full training checkpoint is available.')}
                </p>
              )}
            </>
          )
        )}
        {error && (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            {t('Cancel')}
          </button>
          <button type="submit" className="button primary" disabled={loading || busy || !valid}>
            {busy ? t('Resuming…') : t('Continue training')}
          </button>
        </div>
      </form>
    </dialog>
  );
}
