import { useCallback, useEffect, useRef, useState } from 'react';

type Snapshot<T> = {
  key: string;
  data: T | null;
  phase: 'loading' | 'ready' | 'stale';
  error: Error | null;
  refreshing: boolean;
};
type Controls<T> = {
  refresh: () => Promise<T | null>;
  hold: () => void;
  accept: (value: T) => void;
  resume: () => Promise<T | null>;
};

/** One in-flight read cycle, one queued refresh, bounded backoff and abort cleanup. */
export function useOnlinePoll<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const empty: Snapshot<T> = { key, data: null, phase: 'loading', error: null, refreshing: false };
  const [snapshot, setSnapshot] = useState<Snapshot<T>>(empty);
  const controls = useRef<Controls<T> | null>(null);
  useEffect(() => {
    let alive = true,
      running = false,
      held = false,
      queued = false,
      failures = 0,
      revision = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let waiters: ((data: T | null) => void)[] = [];
    setSnapshot({ key, data: null, phase: 'loading', error: null, refreshing: false });
    const schedule = (delay: number) => {
      clearTimeout(timer);
      if (alive && !held) timer = setTimeout(() => void run(), delay);
    };
    const run = async () => {
      if (!alive || held) return;
      if (running) {
        queued = true;
        return;
      }
      clearTimeout(timer);
      running = true;
      queued = false;
      const recipients = waiters;
      waiters = [];
      const expected = revision;
      const attempt = new AbortController();
      controller = attempt;
      const deadline = setTimeout(() => attempt.abort(), 10000);
      let accepted: T | null = null;
      setSnapshot((previous) => ({ ...previous, refreshing: true }));
      try {
        const next = await load(attempt.signal);
        if (alive && !held && expected === revision && !attempt.signal.aborted) {
          accepted = next;
          failures = 0;
          setSnapshot({ key, data: next, phase: 'ready', error: null, refreshing: false });
        }
      } catch (error) {
        if (alive && !held && expected === revision) {
          failures = Math.min(failures + 1, 3);
          setSnapshot((previous) => ({
            ...previous,
            phase: 'stale',
            error: error as Error,
            refreshing: false,
          }));
        }
      } finally {
        clearTimeout(deadline);
        attempt.abort();
        recipients.forEach((resolve) => resolve(accepted));
        running = false;
        if (controller === attempt) controller = null;
        if (alive && !held) schedule(queued ? 0 : Math.min(5000 * 2 ** failures, 30000));
      }
    };
    const refresh = () =>
      new Promise<T | null>((resolve) => {
        if (!alive) {
          resolve(null);
          return;
        }
        waiters.push(resolve);
        if (!held) {
          if (running) queued = true;
          else void run();
        }
      });
    const hold = () => {
      held = true;
      revision++;
      clearTimeout(timer);
      controller?.abort();
    };
    const accept = (data: T) => {
      if (!alive) return;
      revision++;
      setSnapshot({ key, data, phase: 'ready', error: null, refreshing: false });
    };
    const active: Controls<T> = {
      refresh,
      hold,
      accept,
      resume: () => {
        held = false;
        return refresh();
      },
    };
    controls.current = active;
    const wake = () => {
      if (held) return;
      if (running) queued = true;
      else void run();
    };
    const offline = () => {
      setSnapshot((previous) => ({
        ...previous,
        phase: 'stale',
        error: new Error('Connection lost. Reconnecting…'),
      }));
      controller?.abort();
    };
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
    window.addEventListener('offline', offline);
    void run();
    return () => {
      alive = false;
      revision++;
      clearTimeout(timer);
      controller?.abort();
      waiters.forEach((resolve) => resolve(null));
      waiters = [];
      window.removeEventListener('focus', wake);
      window.removeEventListener('online', wake);
      window.removeEventListener('offline', offline);
      if (controls.current === active) controls.current = null;
    };
  }, [key, load]);
  const refresh = useCallback(() => controls.current?.refresh() ?? Promise.resolve(null), []);
  const hold = useCallback(() => controls.current?.hold(), []);
  const accept = useCallback((data: T) => controls.current?.accept(data), []);
  const resume = useCallback(() => controls.current?.resume() ?? Promise.resolve(null), []);
  return { ...(snapshot.key === key ? snapshot : empty), refresh, hold, accept, resume };
}
