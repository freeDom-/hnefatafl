import { useEffect, useId, useRef, useState } from 'react';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';

export default function AccountMenu({
  username,
  logout,
}: {
  username: string;
  logout?: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLAnchorElement | HTMLButtonElement | null)[]>([]);
  const initialItem = useRef(0);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    items.current[initialItem.current]?.focus();
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  return (
    <div
      className="account-menu"
      ref={container}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="button small secondary account-trigger"
        aria-label={t('Account menu for {username}', { username })}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          initialItem.current = 0;
          setOpen((current) => !current);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            initialItem.current = event.key === 'ArrowUp' ? 1 : 0;
            setOpen(true);
          }
        }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4 22v-3a8 8 0 0 1 16 0v3" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        <span className="account-name" title={username}>
          {username}
        </span>
        <span className="account-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div
          id={id}
          role="menu"
          aria-label={t('Account')}
          className="account-dropdown panel"
          onKeyDown={(event) => {
            if (event.key === 'Tab' && event.shiftKey) {
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
              return;
            }
            const index = items.current.indexOf(document.activeElement as HTMLButtonElement);
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? items.current.length - 1
                  : event.key === 'ArrowDown'
                    ? (index + 1) % items.current.length
                    : event.key === 'ArrowUp'
                      ? (index + items.current.length - 1) % items.current.length
                      : null;
            if (next !== null) {
              event.preventDefault();
              items.current[next]?.focus();
            }
          }}
        >
          <a
            ref={(node) => {
              items.current[0] = node;
            }}
            role="menuitem"
            tabIndex={-1}
            className="button secondary"
            href="/profile"
            onKeyDown={(event) => {
              if (event.key === ' ') {
                event.preventDefault();
                event.currentTarget.click();
              }
            }}
            onClick={(event) => {
              setOpen(false);
              surfaceLink(event);
            }}
          >
            {t('My Profile')}
          </a>
          <div role="separator" />
          <button
            ref={(node) => {
              items.current[1] = node;
            }}
            role="menuitem"
            tabIndex={-1}
            type="button"
            className="button secondary"
            onClick={() => {
              setOpen(false);
              void logout?.();
            }}
          >
            {t('Log out')}
          </button>
        </div>
      )}
    </div>
  );
}
