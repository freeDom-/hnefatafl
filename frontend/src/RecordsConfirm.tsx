import { t, useI18n } from './i18n';
import { useEffect, useRef } from 'react';

export default function RecordsConfirm({
  title,
  children,
  onCancel,
  onConfirm,
  busy,
  error,
  confirmDisabled = false,
  confirmLabel = 'Delete',
  busyLabel = 'Deleting…',
  cancelLabel = 'Keep it',
}: {
  title: string;
  children: React.ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  error?: string;
  confirmDisabled?: boolean;
  confirmLabel?: string;
  busyLabel?: string;
  cancelLabel?: string;
}) {
  useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal confirm-dialog"
      aria-label={t(title)}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <div className="modal-top">
        <h2>{t(title)}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label={t('Close confirmation')}
          disabled={busy}
          onClick={onCancel}
        >
          ×
        </button>
      </div>
      <div className="confirmation-copy">{children}</div>
      {error && (
        <p className="form-error" role="alert">
          {t(error)}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" className="button secondary" disabled={busy} onClick={onCancel}>
          {t(cancelLabel)}
        </button>
        <button
          type="button"
          className="button danger"
          disabled={busy || confirmDisabled}
          onClick={onConfirm}
        >
          {busy ? t(busyLabel) : t(confirmLabel)}
        </button>
      </div>
    </dialog>
  );
}
