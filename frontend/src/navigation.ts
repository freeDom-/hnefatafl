/** Same-origin application destinations only; search/selection survives authentication. */
export function safeDestination(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u001f]/.test(value))
    return '/dashboard';
  const url = new URL(value, location.origin);
  if (url.origin !== location.origin) return '/dashboard';
  const path = url.pathname;
  return [
    '/',
    '/play',
    '/dashboard',
    '/leaderboard',
    '/training',
    '/tournaments',
    '/archive',
    '/online',
  ].includes(path) ||
    /^\/online\/games\/online-[0-9a-f]{32}(?:\/review)?$/.test(path) ||
    /^\/(?:games|dashboard\/single-player)\/(?!online-)[A-Za-z0-9_-]{1,100}$/.test(path)
    ? path + url.search + url.hash
    : '/dashboard';
}
export function canonicalLocation() {
  const url = new URL(location.href);
  if (['/', '/play', '/online'].includes(url.pathname)) {
    if (url.pathname !== '/' && !url.searchParams.has('setup'))
      url.searchParams.set('setup', url.pathname === '/online' ? 'online' : 'single-player');
    url.pathname = '/dashboard';
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  return location.pathname + location.search;
}
export function navigate(path: string) {
  history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
