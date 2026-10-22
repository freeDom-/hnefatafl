import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import type { Model } from './types';

export default function ModelRenameDialog({
  model,
  onClose,
  onRenamed,
}: {
  model: Model;
  onClose: () => void;
  onRenamed: () => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(model.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || name.trim().length > 100) return;
    setBusy(true);
    setError('');
    try {
      await api(`/models/${model.id}`, { name: name.trim() }, 'PATCH');
      onRenamed();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal model-rename-dialog"
      aria-label={t('Rename model')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-top">
          <h2>{t('Rename model')}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close rename model')}
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <label>
          {t('Model name')}
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
            'The model keeps its identity, rune and weights. Saved games and tournaments retain their recorded names.',
          )}
        </p>
        {error && (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        )}
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
