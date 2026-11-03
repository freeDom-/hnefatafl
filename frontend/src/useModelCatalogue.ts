import { useCallback, useRef, useState } from 'react';
import { api } from './api';
import type { Model } from './types';

/** A registry entry the UI can rely on. Anything else is an unreadable response. */
function isModel(value: unknown): value is Model {
  if (!value || typeof value !== 'object') return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.id === 'string' &&
    typeof entry.name === 'string' &&
    typeof entry.ruleset === 'string'
  );
}

/**
 * Validate one `/models` payload. A genuinely empty registry is a successful
 * result; anything that is not a list of registry entries is a failure, so a
 * malformed body can never be mistaken for "no models registered".
 */
export function parseModelCatalogue(payload: unknown): Model[] {
  if (!Array.isArray(payload) || !payload.every(isModel))
    throw new Error('The server returned an unreadable model catalogue.');
  return payload;
}

/**
 * The application's single model catalogue.
 *
 * Several callers reload it (entering the training or tournament pages, the New
 * Game dialog's explicit refresh), so completion order is not start order. Each
 * request takes a generation and only the newest generation may publish, which
 * holds even when a caller cannot abort its underlying request. The previous
 * catalogue stays visible until a newer request succeeds.
 */
export function useModelCatalogue() {
  const [models, setModels] = useState<Model[]>([]);
  const generation = useRef(0);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const request = generation.current + 1;
    generation.current = request;
    const saved = await api<unknown>('/models', undefined, undefined, signal);
    const catalogue = parseModelCatalogue(saved);
    // A newer request, or an abort, means this response is no longer current.
    if (request !== generation.current || signal?.aborted) return;
    setModels(catalogue);
  }, []);
  return { models, refresh };
}
