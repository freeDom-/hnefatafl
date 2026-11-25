import { createContext, useContext, useEffect, useRef } from 'react';
import { api, ApiError } from './api';

export const LeagueRefresh = createContext({ revision: 0, explicitRevision: 0 });

export function useLeagueRefresh(refresh: (explicit: boolean) => unknown) {
  const signal = useContext(LeagueRefresh);
  const previous = useRef(signal);
  useEffect(() => {
    if (previous.current.revision === signal.revision) return;
    const explicit = previous.current.explicitRevision !== signal.explicitRevision;
    previous.current = signal;
    void refresh(explicit);
  }, [refresh, signal]);
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
