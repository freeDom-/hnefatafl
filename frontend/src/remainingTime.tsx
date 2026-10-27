import { useEffect, useState } from 'react';
import { useI18n } from './i18n';

/**
 * One shared remaining-duration formatter for the online game screen and the
 * Dashboard lobby. Input is authoritative seconds; expired values clamp to zero.
 * The highest unit is not zero-padded, lower units always keep two digits:
 * `45s`, `2m 05s`, `1h 02m 05s`, `1d 03h 02m 05s`.
 */
export function formatRemaining(seconds: number): string {
  const total = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, '0');
  if (days > 0) return `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(secs)}s`;
  if (hours > 0) return `${hours}h ${pad(minutes)}m ${pad(secs)}s`;
  if (minutes > 0) return `${minutes}m ${pad(secs)}s`;
  return `${secs}s`;
}

/** Ticking clock for countdowns; one interval per mounted consumer. */
export function useNow(interval = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(timer);
  }, [interval]);
  return now;
}

/** Seconds remaining until an authoritative deadline, clamped at zero. */
export function remainingSeconds(deadline: string | null, now: number): number {
  if (!deadline) return 0;
  const at = Date.parse(deadline);
  if (Number.isNaN(at)) return 0;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/**
 * Compact duration with an explicit accessible label. Not a live region, so it
 * does not announce every tick; the surrounding status text carries changes.
 */
export function RemainingTime({ seconds, className }: { seconds: number; className?: string }) {
  const { t } = useI18n();
  const value = formatRemaining(seconds);
  return (
    <span className={className} aria-label={t('{value} remaining', { value })}>
      {value}
    </span>
  );
}
