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
    '/statistics',
    '/leaderboard',
    '/profile',
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
  if (['/', '/online'].includes(url.pathname)) {
    if (url.pathname !== '/' && !url.searchParams.has('setup'))
      url.searchParams.set('setup', url.pathname === '/online' ? 'online' : 'single-player');
    url.pathname = '/dashboard';
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  return location.pathname + location.search;
}
/**
 * Same-origin in-app navigation. Ordinary user actions push a new history entry;
 * `replace` rewrites the current entry instead (used for automatic redirects that
 * must not leave an intermediate route behind for Browser Back to land on).
 */
export function navigate(path: string, options: { replace?: boolean } = {}) {
  if (options.replace) history.replaceState(null, '', path);
  else history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
