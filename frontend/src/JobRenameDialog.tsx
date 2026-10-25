import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, errorMessage } from './api';
import { notify } from './notifications';
import { useI18n } from './i18n';
import type { Job } from './types';
import { jobName } from './trainingDashboardData';

/**
 * Rename a training job's display metadata only. The job ID, output path,
 * checkpoint, training parameters, worker assignment and execution state are
 * never touched; the server persists a separate `name` field.
 */
export default function JobRenameDialog({
  job,
  onClose,
  onRenamed,
}: {
  job: Job;
  onClose: () => void;
  onRenamed: (job: Job) => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(jobName(job));
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const next = name.trim();
    if (!next || next.length > 100) return;
    setBusy(true);
    try {
      const updated = await api<Job>(`/training/jobs/${job.id}`, { name: next }, 'PATCH');
      onRenamed(updated);
    } catch (error) {
      // Keep the entered text so the user can retry after a failure.
      notify.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal model-rename-dialog"
      aria-label={t('Rename training job')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-top">
          <h2>{t('Rename training job')}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close rename training job')}
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <label>
          {t('Job name')}
          <input
            autoFocus
            required
            maxLength={100}
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <p className="subtle">
          {t(
            'Renaming changes only the displayed job name. Its ID, checkpoint, parameters and execution are unchanged.',
          )}
        </p>
        <div className="modal-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            {t('Cancel')}
          </button>
          <button type="submit" className="button primary" disabled={busy || !name.trim()}>
            {busy ? t('Saving…') : t('Save name')}
          </button>
        </div>
      </form>
    </dialog>
  );
}
