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

const MAX_ERROR_LENGTH = 300;

/**
 * Turn any thrown value into a safe, readable message. Never surfaces objects,
 * stack traces or empty strings; callers can supply a localized fallback.
 */
export function errorMessage(
  error: unknown,
  fallback = 'Something went wrong. Please try again.',
): string {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  const text = message.trim();
  return text || fallback;
}

function cap(text: string) {
  return text.length > MAX_ERROR_LENGTH ? `${text.slice(0, MAX_ERROR_LENGTH - 1)}…` : text;
}

function readableDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => {
        if (typeof entry === 'string') return entry;
        if (
          entry &&
          typeof entry === 'object' &&
          typeof (entry as { msg?: unknown }).msg === 'string'
        )
          return (entry as { msg: string }).msg;
        return undefined;
      })
      .filter((part): part is string => Boolean(part && part.trim()));
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    const nested = readableDetail(detail);
    if (nested) return nested;
  }
  return undefined;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /** Machine-readable server error code, when the response provides one. */
    public readonly code?: string,
    /** Rejected field paths, when the response names them. */
    public readonly fields?: string[],
    /** Server retry guidance, as a delay rather than a response/header dependency. */
    public readonly retryAfterMs?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function retryAfter(response: Response): number | undefined {
  const value = response.headers?.get('Retry-After')?.trim();
  if (!value) return undefined;
  const delay = /^\d+(\.\d+)?$/.test(value) ? Number(value) * 1000 : Date.parse(value) - Date.now();
  return Number.isFinite(delay) ? Math.max(0, delay) : undefined;
}

/** Read the optional machine-readable fields of a structured error payload. */
function errorFields(payload: unknown): string[] | undefined {
  if (!payload || typeof payload !== 'object' || !('fields' in payload)) return undefined;
  const fields = payload.fields;
  if (!Array.isArray(fields)) return undefined;
  const names = fields.filter(
    (entry): entry is string => typeof entry === 'string' && entry.trim().length > 0,
  );
  return names.length ? names : undefined;
}

function errorCode(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || !('code' in payload)) return undefined;
  const code = payload.code;
  return typeof code === 'string' && code.trim() ? code : undefined;
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
  const combined = signal ? AbortSignal.any([controller.signal, signal]) : controller.signal;
  if (protectedRequest) protectedRequests.add(controller);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      headers,
      credentials: 'same-origin',
      signal: combined,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } finally {
    protectedRequests.delete(controller);
  }
  if (combined.aborted || (protectedRequest && expected !== sessionEpoch))
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
    const payload = await response.json().catch(() => null);
    const message =
      readableDetail(payload) ||
      response.statusText?.trim() ||
      `Request failed (${response.status}).`;
    throw new ApiError(
      response.status,
      cap(message),
      errorCode(payload),
      errorFields(payload),
      retryAfter(response),
    );
  }
  const value = await response.json();
  if (!path.startsWith('/auth/') && expected !== sessionEpoch)
    throw new DOMException('Request cancelled', 'AbortError');
  return value as T;
}
