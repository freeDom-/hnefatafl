import type { PublicFilters, PublicGame, PublicGamePage, PublicReplay } from './publicOnlineTypes';

/**
 * Failure classes the public viewers can act on. `unavailable` and `conflict`
 * are permanent for the current view (stop automatic polling); the others are
 * recoverable with a bounded delay.
 */
export type PublicFailure = 'unavailable' | 'conflict' | 'busy' | 'retryable' | 'timeout';

const failureMessages: Record<PublicFailure, string> = {
  unavailable: 'Public viewing is unavailable.',
  conflict: 'This replay could not be validated.',
  busy: 'Public viewing is busy. Please retry shortly.',
  retryable: 'Public games could not be loaded. Please retry.',
  timeout: 'Public viewing timed out. Please retry.',
};

function failureForStatus(status: number): PublicFailure {
  if (status === 409) return 'conflict';
  if (status === 429) return 'busy';
  // Any other client error is treated as a permanent privacy-preserving state
  // so a malformed request cannot trigger an endless retry loop.
  if (status >= 400 && status < 500) return 'unavailable';
  return 'retryable';
}

export class PublicOnlineError extends Error {
  constructor(
    public readonly status: number,
    public readonly retryAfterMs = 0,
    public readonly failure: PublicFailure = failureForStatus(status),
  ) {
    super(failureMessages[failure]);
    this.name = 'PublicOnlineError';
  }
}

/** Bounded anonymous request timeout; long enough for a slow API worker. */
export const PUBLIC_REQUEST_TIMEOUT_MS = 10000;
/** Cap a server `Retry-After` so a hostile/large value cannot stall a viewer. */
const MAX_RETRY_AFTER_MS = 300000;
const BASE_RETRY_MS = 5000;
const MAX_RETRY_MS = 30000;
const MAX_BACKOFF_STEPS = 4;

/**
 * `Retry-After` is either delta-seconds or an HTTP-date. Returns a bounded
 * delay in milliseconds, or `0` when the header is absent or invalid.
 */
export function parseRetryAfter(header: string | null, now = Date.now()): number {
  if (!header) return 0;
  const value = header.trim();
  if (/^\d+$/.test(value)) return Math.min(Number(value) * 1000, MAX_RETRY_AFTER_MS);
  const date = Date.parse(value);
  if (Number.isNaN(date)) return 0;
  return Math.min(Math.max(0, date - now), MAX_RETRY_AFTER_MS);
}

/**
 * Delay before the next automatic public request, or `null` when the failure is
 * permanent for this view. Shared by discovery and detail viewers so their retry
 * policy cannot drift apart.
 */
export function publicRetryDelay(
  error: unknown,
  failures: number,
  baseMs = BASE_RETRY_MS,
  maxMs = MAX_RETRY_MS,
): number | null {
  if (error instanceof PublicOnlineError) {
    if (error.failure === 'unavailable' || error.failure === 'conflict') return null;
    if (error.failure === 'busy' && error.retryAfterMs > 0) return error.retryAfterMs;
  }
  const step = Math.min(Math.max(0, failures), MAX_BACKOFF_STEPS);
  return Math.min(baseMs * 2 ** step, maxMs);
}

/** Separate anonymous transport: no cookies, CSRF, account/session side effects. */
async function readPublic<T>(
  path: string,
  signal: AbortSignal,
  timeoutMs = PUBLIC_REQUEST_TIMEOUT_MS,
): Promise<T> {
  // Compose the caller's signal with a timeout using only AbortController so
  // older Safari (no AbortSignal.any/timeout) keeps working.
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  if (signal.aborted) controller.abort();
  else signal.addEventListener('abort', forwardAbort);
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetch(`/api/public/online/games${path}`, {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new PublicOnlineError(
        response.status,
        parseRetryAfter(response.headers.get('Retry-After')),
      );
    }
    return (await response.json()) as T;
  } catch (error) {
    // A timeout aborts our internal controller, never the caller's signal, so
    // navigation/unmount cancellation keeps its AbortError semantics.
    if (timedOut && !signal.aborted) throw new PublicOnlineError(0, 0, 'timeout');
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', forwardAbort);
    // Release the composed signal once the read settles, matching the other
    // polling transports and making an in-flight cancellation observable.
    controller.abort();
  }
}

function identifier(id: string) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) throw new PublicOnlineError(404);
  return encodeURIComponent(id);
}
export function publicGames(filters: PublicFilters, cursor: string | null, signal: AbortSignal) {
  const query = new URLSearchParams({ limit: '12' });
  for (const key of ['ruleset', 'status', 'outcome', 'player'] as const) {
    if (filters[key]) query.set(key, filters[key].slice(0, key === 'player' ? 148 : 32));
  }
  if (cursor) query.set('cursor', cursor.slice(0, 512));
  return readPublic<PublicGamePage>(`?${query}`, signal);
}
export const publicGame = (id: string, signal: AbortSignal) =>
  readPublic<PublicGame>(`/${identifier(id)}`, signal);
export const publicReplay = (id: string, signal: AbortSignal) =>
  readPublic<PublicReplay>(`/${identifier(id)}/replay`, signal);
