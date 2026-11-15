import type { PublicFilters, PublicGame, PublicGamePage, PublicReplay } from './publicOnlineTypes';

export class PublicOnlineError extends Error {
  constructor(
    public readonly status: number,
    public readonly retryAfterMs = 0,
  ) {
    super(
      status === 404
        ? 'Public viewing is unavailable.'
        : status === 429
          ? 'Public viewing is busy. Please retry shortly.'
          : 'Public games could not be loaded. Please retry.',
    );
  }
}
/** Separate anonymous transport: no cookies, CSRF, account/session side effects. */
async function readPublic<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/public/online/games${path}`, {
    method: 'GET',
    credentials: 'omit',
    cache: 'no-store',
    signal,
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    const retry = Number(response.headers.get('Retry-After'));
    throw new PublicOnlineError(
      response.status,
      Number.isFinite(retry) ? Math.max(0, retry * 1000) : 0,
    );
  }
  return response.json() as Promise<T>;
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
