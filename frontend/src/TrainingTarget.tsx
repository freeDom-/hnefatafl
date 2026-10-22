import { useI18n } from './i18n';
import type { Job, TrainingWorker } from './types';

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
  const available = workers.filter((worker) => worker.status !== 'revoked');
  const unavailable =
    value.startsWith('worker:') && !available.some((worker) => `worker:${worker.id}` === value);
  return (
    <label>
      {t('Run training on')}
      <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="local">{t('This server')}</option>
        <option value="remote">{t('Any remote worker')}</option>
        {available.map((worker) => (
          <option key={worker.id} value={`worker:${worker.id}`}>
            {worker.name} · {t(worker.status)} · {worker.active_jobs}/{worker.capacity}
          </option>
        ))}
        {unavailable && (
          <option value={value} disabled>
            {t('Unavailable worker')}
          </option>
        )}
      </select>
    </label>
  );
}
