import type { MouseEvent as ReactMouseEvent } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { surfaceLink } from './surfaceNavigation';

afterEach(() => history.replaceState(null, '', '/'));

function click(attributes: Record<string, string> = {}, overrides: Record<string, unknown> = {}) {
  const anchor = document.createElement('a');
  anchor.href = '/training#model-collection';
  Object.entries(attributes).forEach(([name, value]) => anchor.setAttribute(name, value));
  const event = {
    currentTarget: anchor,
    button: 0,
    preventDefault: vi.fn(),
    ...overrides,
  };
  surfaceLink(event as unknown as ReactMouseEvent<HTMLAnchorElement>);
  return event;
}

it('uses SPA navigation and notifies listeners even for the same destination', () => {
  const changed = vi.fn();
  window.addEventListener('popstate', changed);
  try {
    for (let count = 1; count <= 2; count++) {
      expect(click().preventDefault).toHaveBeenCalledOnce();
      expect(location.pathname + location.hash).toBe('/training#model-collection');
      expect(changed).toHaveBeenCalledTimes(count);
    }
  } finally {
    window.removeEventListener('popstate', changed);
  }
});

it.each([
  { metaKey: true },
  { ctrlKey: true },
  { shiftKey: true },
  { altKey: true },
  { button: 1 },
  { defaultPrevented: true },
])('preserves native click semantics for %j', (overrides) => {
  expect(click({}, overrides).preventDefault).not.toHaveBeenCalled();
});

it.each<Record<string, string>>([
  { target: '_blank' },
  { download: 'jobs.json' },
  { href: 'https://example.com/' },
])('preserves native anchor behavior for %j', (attributes) => {
  expect(click(attributes).preventDefault).not.toHaveBeenCalled();
});
