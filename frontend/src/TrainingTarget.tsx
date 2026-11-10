import { useId } from 'react';
import { useI18n } from './i18n';
import { FieldLabel } from './FieldTooltip';
import type { TrainingConfig } from './trainingPresets';
import type { Job, TrainingWorker } from './types';

/**
 * Why a version-compatible worker cannot run the requested configuration. The
 * backend enforces the same rule; this keeps the form honest before submission.
 */
export function workerCapabilityError(worker: TrainingWorker, config: TrainingConfig): string {
  if (
    config.optimizer === 'adamw_schedulefree' &&
    worker.capabilities?.schedule_free_adamw !== true
  )
    return 'This worker does not support Schedule-Free AdamW. Install the updated training dependencies or choose another worker.';
  if (config.adaptation === 'hypercontroller' && worker.capabilities?.hyper_adaptation !== true)
    return 'This worker does not support automatic hyperparameter updates. Choose a newer worker or disable them.';
  if (
    config.adaptation === 'hypercontroller' &&
    config.evaluation_opponent === 'champion' &&
    worker.capabilities?.hyper_adaptation_champion_transition !== true
  )
    return 'This worker does not support automatic hyperparameter updates with a persistent Champion opponent. Choose a newer worker, a fixed opponent, or disable them.';
  return '';
}

export function workerEligible(worker: TrainingWorker, config?: TrainingConfig) {
  return (
    worker.status !== 'revoked' &&
    worker.compatibility?.eligible === true &&
    (!config || !workerCapabilityError(worker, config))
  );
}

export function jobTarget(job: Job) {
  return job.execution === 'remote'
    ? job.target_worker_id
      ? `worker:${job.target_worker_id}`
      : 'remote'
    : 'local';
}

export function targetRequest(target: string) {
  return target === 'local'
    ? { execution: 'local' }
    : {
        execution: 'remote',
        ...(target.startsWith('worker:') ? { worker_id: target.slice(7) } : {}),
      };
}

export default function TrainingTarget({
  value,
  onChange,
  workers,
  config,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  workers: TrainingWorker[];
  /** The requested configuration, so capability gaps disable the worker. */
  config?: TrainingConfig;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const warningId = useId();
  const available = workers.filter((worker) => worker.status !== 'revoked');
  const unavailable =
    value.startsWith('worker:') && !available.some((worker) => `worker:${worker.id}` === value);
  const selected = available.find((worker) => `worker:${worker.id}` === value);
  const capabilityError = selected && config ? workerCapabilityError(selected, config) : '';
  const showWarning =
    selected && (!workerEligible(selected, config) || selected.version_override_enabled);
  const id = useId();
  return (
    <div className="training-field">
      <FieldLabel htmlFor={id} label="Run training on" tooltipKey="target" />
      <select
        id={id}
        data-training-field="target"
        aria-describedby={showWarning ? warningId : undefined}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="local">{t('This server')}</option>
        <option value="remote">{t('Any remote worker')}</option>
        {available.map((worker) => {
          const workerError = config ? workerCapabilityError(worker, config) : '';
          return (
            <option
              key={worker.id}
              value={`worker:${worker.id}`}
              disabled={!workerEligible(worker, config)}
            >
              {worker.name} · {t(worker.status)} · {worker.active_jobs}/{worker.capacity}
              {' · '}
              {workerError
                ? t(workerError)
                : t(worker.compatibility?.reason ?? 'Missing or unknown application version')}
            </option>
          );
        })}
        {unavailable && (
          <option value={value} disabled>
            {t('Unavailable worker')}
          </option>
        )}
      </select>
      {selected && showWarning && (
        <small role="status" id={warningId}>
          {capabilityError
            ? t(capabilityError)
            : t(selected.compatibility?.reason ?? 'Missing or unknown application version')}
          {selected.version_override_enabled && ` · ${t('Unsupported version combination')}`}
        </small>
      )}
    </div>
  );
}
