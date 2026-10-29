import { useEffect, useRef, type ReactNode } from 'react';
import { t } from './i18n';
export default function Modal({
  title,
  onClose,
  children,
  initialFocus,
  className,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  initialFocus?: string;
  className?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    const previous = document.activeElement;
    node?.showModal();
    if (initialFocus) node?.querySelector<HTMLElement>(initialFocus)?.focus();
    return () => {
      node?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, [initialFocus]);

  return (
    <dialog
      ref={dialog}
      className={['modal', className].filter(Boolean).join(' ')}
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
          <h2 className="dialog-title">{title}</h2>
        </div>
        <button className="icon-button" aria-label={t('Close dialog')} onClick={onClose}>
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}
