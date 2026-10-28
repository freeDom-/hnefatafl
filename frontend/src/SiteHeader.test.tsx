import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SiteHeader from './SiteHeader';
vi.unmock('./Auth');

const account = {
  id: 'archer',
  username: 'Archer',
  email: 'archer@example.test',
  role: 'USER',
  permissions: ['play'],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
  localStorage.clear();
});

it('uses the shared authenticated navigation without a Guest play or login action', () => {
  render(<SiteHeader account={account} />);
  expect(screen.getByRole('button', { name: 'Dashboard' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Hall of records' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'AI workshop' })).toBeNull();
  expect(screen.queryByRole('link', { name: /Guest play|Log in/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Dashboard' }));
  expect(location.pathname).toBe('/dashboard');
});
