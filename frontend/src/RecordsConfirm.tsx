import { useEffect, useRef } from 'react';

export default function RecordsConfirm({
  title,
  children,
  onCancel,
  onConfirm,
  busy,
  error,
  confirmDisabled = false,
}: {
  title: string;
  children: React.ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  error?: string;
  confirmDisabled?: boolean;
}) {
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
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <div className="modal-top">
        <h2>{title}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Close confirmation"
          disabled={busy}
          onClick={onCancel}
        >
          ×
        </button>
      </div>
      <div className="confirmation-copy">{children}</div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" className="button secondary" disabled={busy} onClick={onCancel}>
          Keep it
        </button>
        <button
          type="button"
          className="button danger"
          disabled={busy || confirmDisabled}
          onClick={onConfirm}
        >
          {busy ? 'Deleting…' : 'Delete'}
        </button>
      </div>
    </dialog>
  );
}
