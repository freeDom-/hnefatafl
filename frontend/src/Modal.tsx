import { useEffect, useRef, type ReactNode } from 'react';
import { t } from './i18n';
export default function Modal({
  title,
  onClose,
  children,
  initialFocus,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  initialFocus?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    if (initialFocus) dialog.current?.querySelector<HTMLElement>(initialFocus)?.focus();
    return () => dialog.current?.close();
  }, [initialFocus]);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onPointerDown={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          onClose();
      }}
    >
      <div className="modal-top">
        <div>
          <h2>{title}</h2>
        </div>
        <button className="icon-button" aria-label={t('Close dialog')} onClick={onClose}>
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}
