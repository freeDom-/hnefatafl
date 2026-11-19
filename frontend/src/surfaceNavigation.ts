import type { MouseEvent } from 'react';
import { navigate } from './navigation';

export function surfaceLink(event: MouseEvent<HTMLAnchorElement>) {
  const anchor = event.currentTarget;
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    (anchor.target && anchor.target !== '_self') ||
    anchor.hasAttribute('download') ||
    anchor.origin !== location.origin
  )
    return;
  event.preventDefault();
  navigate(anchor.getAttribute('href')!);
}
