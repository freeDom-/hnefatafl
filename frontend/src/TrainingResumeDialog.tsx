import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import type { Job, TrainingWorker } from './types';
import TrainingTarget, { jobTarget, targetRequest } from './TrainingTarget';

export interface ResumeCheckpoint {
  job_id: string;
  checkpoint?: string;
  completed_iteration: number;
  ruleset: string;
  channels: number;
  blocks: number;
  optimizer: string;
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
  const [target, setTarget] = useState(1);
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
        const priorTarget = Number(job.config?.iterations);
        setTarget(
          Math.max(info.completed_iteration + 1, Number.isInteger(priorTarget) ? priorTarget : 1),
        );
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
                  <dt>{t('Network')}</dt>
                  <dd>
                    {t('{channels} channels · {blocks} blocks', {
                      channels: checkpoint.channels,
                      blocks: checkpoint.blocks,
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t('Optimizer')}</dt>
                  <dd>{optimizerNames[checkpoint.optimizer] ?? checkpoint.optimizer}</dd>
                </div>
              </dl>
              <p className="subtle">
                {t(
                  'Continues the latest full checkpoint from this training job, including optimizer, replay, random state and learning history. The saved architecture and training settings are kept.',
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
                  <label>
                    {t('Target total iterations')}
                    <input
                      type="number"
                      aria-label={t('Target total iterations')}
                      aria-describedby="resume-target-help"
                      min={checkpoint.completed_iteration + 1}
                      max={100000}
                      step={1}
                      required
                      value={target}
                      disabled={busy}
                      onChange={(event) => setTarget(Number(event.target.value))}
                    />
                    <small id="resume-target-help">
                      {t('This is the total iteration count, not the number of extra iterations.')}
                    </small>
                  </label>
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
