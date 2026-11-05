import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import RecordsConfirm from './RecordsConfirm';

interface AffectedJob {
  id: string;
  name: string;
  status: string;
  worker_alive?: boolean;
  directory: string;
  trial_directories: string[];
}
interface StudyPreview {
  study_name: string;
  study_identity: string | null;
  owner_job_id: string;
  jobs: AffectedJob[];
  preserved_source_job_ids: string[];
  external_dependencies: { id: string; name: string; kind: string; status: string }[];
  blocked_reasons: string[];
  can_delete: boolean;
  confirmation_token: string;
}

export default function StudyDeletionDialog({
  jobId,
  onClose,
  onDeleted,
}: {
  jobId: string;
  onClose: () => void;
  onDeleted: (ids: string[], cleanupPending: boolean) => void;
}) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<StudyPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => {
    setConfirmed(false);
    setPreview(null);
    setLoading(true);
    setRevision((value) => value + 1);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void api<StudyPreview>(
      `/training/jobs/${jobId}/study-deletion-preview`,
      undefined,
      undefined,
      controller.signal,
    )
      .then((next) => {
        if (!controller.signal.aborted) setPreview(next);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [jobId, revision]);
  const remove = async () => {
    if (!preview?.can_delete || !confirmed || busy || loading) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<{ job_ids: string[]; cleanup_pending: boolean }>(
        `/training/jobs/${jobId}/study?confirmation_token=${encodeURIComponent(preview.confirmation_token)}`,
        undefined,
        'DELETE',
      );
      onDeleted(result.job_ids, result.cleanup_pending);
    } catch (failure) {
      setError((failure as Error).message);
      reload();
    } finally {
      setBusy(false);
    }
  };
  return (
    <RecordsConfirm
      title={t('Delete entire tuning study?')}
      busy={busy}
      error={error}
      onCancel={onClose}
      onConfirm={() => void remove()}
      confirmDisabled={loading || !preview?.can_delete || !confirmed}
      confirmLabel="Delete entire study"
    >
      <p>
        {t('Permanently delete this study and every listed tuning job? This cannot be undone.')}
      </p>
      {loading && <p role="status">{t('Loading study deletion preview…')}</p>}
      {preview && (
        <>
          <h3>{preview.study_name}</h3>
          <dl className="training-resume-details">
            <div>
              <dt>{t('Study owner')}</dt>
              <dd>
                <code>{preview.owner_job_id}</code>
              </dd>
            </div>
            <div>
              <dt>{t('Study identity')}</dt>
              <dd>
                <code>{preview.study_identity ?? t('Legacy identity unavailable')}</code>
              </dd>
            </div>
            {!!preview.preserved_source_job_ids.length && (
              <div>
                <dt>{t('Preserved source jobs')}</dt>
                <dd>{preview.preserved_source_job_ids.join(', ')}</dd>
              </div>
            )}
          </dl>
          <div className="model-usage-warning">
            <h3>{t('Affected tuning jobs')}</h3>
            <ul className="model-usage-games" tabIndex={0} aria-label={t('Affected tuning jobs')}>
              {preview.jobs.map((job) => (
                <li key={job.id}>
                  <strong>
                    {job.name} · {t(job.status)}
                  </strong>
                  <span>
                    <code>{job.directory}</code>
                  </span>
                  {job.worker_alive && (
                    <span>{t('Worker still active; stop it before deletion.')}</span>
                  )}
                  {!!job.trial_directories.length && (
                    <span>{job.trial_directories.join(', ')}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
          <p>
            {t(
              'All files in these directories will be removed, including the study database, trial checkpoints, replay buffers, results, history and logs.',
            )}
          </p>
          <p>
            {t('Source training jobs, registered models, saved games and tournaments are kept.')}
          </p>
          {!!preview.external_dependencies.length && (
            <div className="model-usage-warning">
              <h3>{t('External checkpoint dependencies')}</h3>
              <ul
                className="model-usage-games"
                tabIndex={0}
                aria-label={t('External checkpoint dependencies')}
              >
                {preview.external_dependencies.map((job) => (
                  <li key={job.id}>
                    {job.name} · <code>{job.id}</code> · {t(job.status)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!!preview.blocked_reasons.length && (
            <div className="model-usage-warning" role="status">
              <p>
                {t(
                  'Stop participating jobs and remove or rebase external checkpoint dependencies before deleting this study.',
                )}
              </p>
            </div>
          )}
          <label className="model-discard-choice">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy || loading || !preview.can_delete}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            {t('I confirm deletion of the entire study and all listed tuning jobs.')}
          </label>
        </>
      )}
      <button
        className="button secondary small"
        disabled={busy || loading}
        onClick={() => {
          setError('');
          reload();
        }}
      >
        {t('Check again')}
      </button>
    </RecordsConfirm>
  );
}
