import { useId } from 'react';
import { useI18n } from './i18n';
import type { Job, TrainingWorker } from './types';

export function workerEligible(worker: TrainingWorker) {
  return worker.status !== 'revoked' && worker.compatibility?.eligible === true;
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
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  workers: TrainingWorker[];
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const warningId = useId();
  const available = workers.filter((worker) => worker.status !== 'revoked');
  const unavailable =
    value.startsWith('worker:') && !available.some((worker) => `worker:${worker.id}` === value);
  const selected = available.find((worker) => `worker:${worker.id}` === value);
  const showWarning = selected && (!workerEligible(selected) || selected.version_override_enabled);
  return (
    <label>
      {t('Run training on')}
      <select
        aria-label={t('Run training on')}
        aria-describedby={showWarning ? warningId : undefined}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="local">{t('This server')}</option>
        <option value="remote">{t('Any remote worker')}</option>
        {available.map((worker) => (
          <option key={worker.id} value={`worker:${worker.id}`} disabled={!workerEligible(worker)}>
            {worker.name} · {t(worker.status)} · {worker.active_jobs}/{worker.capacity}
            {' · '}
            {t(worker.compatibility?.reason ?? 'Missing or unknown application version')}
          </option>
        ))}
        {unavailable && (
          <option value={value} disabled>
            {t('Unavailable worker')}
          </option>
        )}
      </select>
      {selected && showWarning && (
        <small role="status" id={warningId}>
          {t(selected.compatibility?.reason ?? 'Missing or unknown application version')}
          {selected.version_override_enabled && ` · ${t('Unsupported version combination')}`}
        </small>
      )}
    </label>
  );
}
