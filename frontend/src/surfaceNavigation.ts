import type { MouseEvent } from 'react';
export function surfaceLink(event: MouseEvent<HTMLAnchorElement>) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
    return;
  event.preventDefault();
  history.pushState(null, '', event.currentTarget.getAttribute('href')!);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
