import type { APIRequestContext } from '@playwright/test';
/** Out-of-band historical setup; new creation/security tests always use the real API. */
export const historicalGame = (request: APIRequestContext, options: { data: unknown }) =>
  request.post('http://127.0.0.1:8002/historical-game', options);
