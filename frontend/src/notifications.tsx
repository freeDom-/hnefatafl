import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { t, useI18n } from './i18n';

/**
 * Application-wide transient notifications.
 *
 * The store lives outside React so asynchronous operations and non-component
 * modules can report failures without threading callbacks through the tree. A
 * single `NotificationProvider` renders the viewport once; individual pages
 * never own notification state.
 */
export type NotificationSeverity = 'error' | 'success' | 'info';

export type Notification = {
  id: number;
  key: string;
  message: string;
  severity: NotificationSeverity;
  timeout: number;
};

export type NotificationOptions = {
  /** Milliseconds before auto-dismissal; `0` keeps the notification until dismissed. */
  timeout?: number;
};

const MAX_VISIBLE = 3;
const MAX_QUEUED = 5;
const DEDUPE_WINDOW = 5000;
const DEFAULT_SUCCESS_TIMEOUT = 6000;
const DEFAULT_INFO_TIMEOUT = 6000;

let notifications: Notification[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const recentlyShown = new Map<string, number>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return notifications;
}

function clearTimer(id: number) {
  const timer = timers.get(id);
  if (timer !== undefined) {
    clearTimeout(timer);
    timers.delete(id);
  }
}

function schedule(notification: Notification) {
  clearTimer(notification.id);
  if (notification.timeout <= 0) return;
  timers.set(
    notification.id,
    setTimeout(() => dismissNotification(notification.id), notification.timeout),
  );
}

function defaultTimeout(severity: NotificationSeverity) {
  if (severity === 'error') return 0;
  return severity === 'success' ? DEFAULT_SUCCESS_TIMEOUT : DEFAULT_INFO_TIMEOUT;
}

function defaultMessage(severity: NotificationSeverity) {
  return severity === 'error' ? t('Something went wrong. Please try again.') : t('Done.');
}

/** Remove one notification and its timer. Safe to call for unknown ids. */
export function dismissNotification(id: number) {
  const existing = notifications.find((notification) => notification.id === id);
  if (!existing) return;
  clearTimer(id);
  notifications = notifications.filter((notification) => notification.id !== id);
  emit();
}

/** Remove every notification and cancel all pending timers. */
export function clearNotifications() {
  for (const id of timers.keys()) clearTimeout(timers.get(id));
  timers.clear();
  notifications = [];
  recentlyShown.clear();
  emit();
}

/** Current notifications, oldest first. Stable reference until the next change. */
export function getNotifications() {
  return notifications;
}

export function subscribeNotifications(listener: () => void) {
  return subscribe(listener);
}

function push(
  severity: NotificationSeverity,
  message: unknown,
  options: NotificationOptions = {},
): number {
  const text = String(message ?? '').trim() || defaultMessage(severity);
  const key = `${severity}|${text}`;
  const now = Date.now();
  const existing = notifications.find((notification) => notification.key === key);
  if (existing) {
    schedule(existing);
    return existing.id;
  }
  if ((recentlyShown.get(key) ?? 0) + DEDUPE_WINDOW > now) return -1;
  const notification: Notification = {
    id: nextId++,
    key,
    message: text,
    severity,
    timeout: options.timeout ?? defaultTimeout(severity),
  };
  notifications = [...notifications, notification];
  recentlyShown.set(key, now);
  if (notifications.length > MAX_QUEUED) {
    const removed = notifications[0];
    clearTimer(removed.id);
    notifications = notifications.slice(1);
  }
  emit();
  schedule(notification);
  return notification.id;
}

/** Report a user-facing notification. Errors stay until dismissed. */
export const notify = {
  error: (message?: unknown, options?: NotificationOptions) => push('error', message, options),
  success: (message?: unknown, options?: NotificationOptions) => push('success', message, options),
  info: (message?: unknown, options?: NotificationOptions) => push('info', message, options),
};

/** Subscribe a component to the notification list. */
export function useNotifications() {
  const items = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { notifications: items, dismiss: dismissNotification, notify };
}

function iconFor(severity: NotificationSeverity) {
  if (severity === 'error') return '!';
  if (severity === 'success') return '✓';
  return 'i';
}

/**
 * The topmost open dialog, if any. Native modal dialogs paint above every
 * ordinary fixed layer, so the viewport is portaled into the active dialog to
 * stay visible there. Otherwise it lives directly in the document body.
 */
function topmostDialog(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  const dialogs = document.querySelectorAll('dialog[open]');
  return dialogs.length ? (dialogs[dialogs.length - 1] as HTMLElement) : null;
}

function useNotificationLayer() {
  const [target, setTarget] = useState<HTMLElement | null>(() => topmostDialog());
  useEffect(() => {
    const update = () => setTarget(topmostDialog());
    const observer = new MutationObserver(update);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['open'],
    });
    update();
    return () => observer.disconnect();
  }, []);
  return target;
}

/**
 * Viewport-fixed notification layer. It is portaled into the active dialog (or
 * the document body) so notifications stay above application content and
 * dialogs and are never clipped by a scroll container.
 */
export function NotificationViewport() {
  const { t: translate } = useI18n();
  const items = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const layer = useNotificationLayer();
  const visible = items.slice(0, MAX_VISIBLE);
  const viewport = (
    <div className="notification-viewport" role="region" aria-label={translate('Notifications')}>
      {visible.map((notification) => (
        <div
          key={notification.id}
          className={`notification notification-${notification.severity}`}
          role={notification.severity === 'error' ? 'alert' : 'status'}
        >
          <span className="notification-icon" aria-hidden="true">
            {iconFor(notification.severity)}
          </span>
          <p className="notification-message">{translate(notification.message)}</p>
          <button
            type="button"
            className="notification-close"
            aria-label={translate('Dismiss notification')}
            onClick={() => dismissNotification(notification.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
  return layer ? createPortal(viewport, layer) : viewport;
}

/** Renders the single shared viewport above the application tree. */
export function NotificationProvider({ children }: { children: ReactNode }) {
  return (
    <>
      <NotificationViewport />
      {children}
    </>
  );
}
