import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ArenaShell, WorkshopShell } from './ArenaShell';
import { I18nProvider } from './i18n';
import type { Account } from './Auth';

const player: Account = {
  id: 'archer',
  username: 'Archer',
  email: 'archer@example.test',
  role: 'USER',
  permissions: ['play'],
};
const keeper: Account = { ...player, permissions: ['play', 'training', 'tournaments'] };

afterEach(() => {
  cleanup();
  history.replaceState(null, '', '/');
  delete document.documentElement.dataset.workspace;
});

it('offers a skip link to the main region for every player', () => {
  render(
    <I18nProvider>
      <ArenaShell account={player} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute(
    'href',
    '#main-content',
  );
  expect(screen.getByRole('main')).toBeVisible();
});

it('shows the workshop switch only when the account may enter the workshop', () => {
  const view = render(
    <I18nProvider>
      <ArenaShell account={player} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  expect(screen.queryByRole('button', { name: 'Workshop' })).toBeNull();
  view.rerender(
    <I18nProvider>
      <ArenaShell account={keeper} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Workshop' }));
  expect(location.pathname).toBe('/training');
});

it('switches a workshop shell back to the arena and records the workspace', () => {
  render(
    <I18nProvider>
      <WorkshopShell account={keeper} current="training">
        <main id="main-content">Workshop</main>
      </WorkshopShell>
    </I18nProvider>,
  );
  expect(document.documentElement.dataset.workspace).toBe('workshop');
  fireEvent.click(screen.getByRole('button', { name: 'Arena' }));
  expect(location.pathname).toBe('/dashboard');
});

it('marks the active destination with aria-current in both navigations', () => {
  render(
    <I18nProvider>
      <ArenaShell account={player} current="play">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  expect(
    within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', {
      name: 'Play',
    }),
  ).toHaveAttribute('aria-current', 'page');
  const bottom = screen.getByRole('navigation', { name: 'Mobile navigation' });
  expect(within(bottom).getByRole('link', { name: 'Play' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(bottom).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');
});

it('hides the mobile navigation and workspace switch for signed-out visitors', () => {
  render(
    <I18nProvider>
      <ArenaShell account={null} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  expect(screen.queryByRole('navigation', { name: 'Mobile navigation' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Workshop' })).toBeNull();
});
