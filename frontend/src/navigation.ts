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
/**
 * Canonical route for a location that is not one: `/` opens the dashboard, and the
 * legacy `/dashboard?setup=online` deep link opens the online creation dialog on
 * the Play entry, which owns online creation. Both keep their query and fragment,
 * and the authenticated lobby listing keeps its own `/online` route instead of
 * being rewritten.
 */
export function canonicalLocation() {
  const url = new URL(location.href);
  if (url.pathname === '/') {
    url.pathname = '/dashboard';
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  } else if (url.pathname === '/dashboard' && url.searchParams.get('setup') === 'online') {
    url.pathname = '/play';
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
