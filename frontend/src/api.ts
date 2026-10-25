let csrf = '';
let sessionEpoch = 0;
const protectedRequests = new Set<AbortController>();
export function setCSRF(value: string) {
  if (csrf !== value) {
    sessionEpoch++;
    for (const controller of protectedRequests) controller.abort();
    protectedRequests.clear();
  }
  csrf = value;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch(
  path: string,
  body?: unknown,
  method?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const headers: Record<string, string> = { 'X-CSRF-Token': csrf };
  if (!(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (path.startsWith('/auth/') || path.startsWith('/online/')) headers.Origin = location.origin;
  const expected = sessionEpoch;
  const protectedRequest = !path.startsWith('/auth/');
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  if (protectedRequest) protectedRequests.add(controller);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      headers,
      credentials: 'same-origin',
      signal: controller.signal,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } finally {
    protectedRequests.delete(controller);
    signal?.removeEventListener('abort', cancel);
  }
  if (controller.signal.aborted || (protectedRequest && expected !== sessionEpoch))
    throw new DOMException('Request cancelled', 'AbortError');
  if (response.status === 403 && !path.startsWith('/auth/'))
    window.dispatchEvent(new Event('permissions-changed'));
  if (response.status === 401 && !path.startsWith('/auth/'))
    window.dispatchEvent(new Event('authentication-required'));
  return response;
}

export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
  signal?: AbortSignal,
): Promise<T> {
  const expected = sessionEpoch;
  const response = await apiFetch(path, body, method, signal);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(
      response.status,
      typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail ?? detail),
    );
  }
  const value = await response.json();
  if (!path.startsWith('/auth/') && expected !== sessionEpoch)
    throw new DOMException('Request cancelled', 'AbortError');
  return value as T;
}
