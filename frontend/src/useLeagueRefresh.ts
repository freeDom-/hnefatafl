import { createContext, useContext, useEffect, useRef } from 'react';
import { api, ApiError } from './api';

export const LeagueRefresh = createContext(0);

export function useLeagueRefresh(refresh: () => unknown) {
  const revision = useContext(LeagueRefresh);
  const previous = useRef(revision);
  useEffect(() => {
    if (previous.current === revision) return;
    previous.current = revision;
    void refresh();
  }, [refresh, revision]);
}

export async function leagueApi<T>(
  accountId: string,
  path: string,
  body?: unknown,
  method?: string,
  signal?: AbortSignal,
): Promise<T> {
  try {
    return await api<T>(path, body, method, signal, { 'X-League-Account': accountId });
  } catch (error) {
    if (error instanceof ApiError && error.code === 'league_session_changed')
      window.dispatchEvent(new Event('permissions-changed'));
    throw error;
  }
}
