let csrf = '';
export function setCSRF(value: string) {
  csrf = value;
}

export async function apiFetch(path: string, body?: unknown, method?: string): Promise<Response> {
  const headers: Record<string, string> = { 'X-CSRF-Token': csrf };
  if (!(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (path.startsWith('/auth/')) headers.Origin = location.origin;
  const response = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  if (response.status === 403 && !path.startsWith('/auth/'))
    window.dispatchEvent(new Event('permissions-changed'));
  if (response.status === 401 && !path.startsWith('/auth/'))
    window.dispatchEvent(new Event('authentication-required'));
  return response;
}

export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const response = await apiFetch(path, body, method);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(
      typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail ?? detail),
    );
  }
  return response.json() as Promise<T>;
}
