import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ArenaShell, WorkshopShell } from './ArenaShell';
import { I18nProvider, setLanguage } from './i18n';
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
  setLanguage('en', false);
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

it('keeps branding and licensing links without decorative footer slogans', () => {
  render(
    <I18nProvider>
      <ArenaShell account={player} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  const footer = screen.getByRole('contentinfo');
  expect(footer).toHaveTextContent('ᛟ HNEFATAFL');
  expect(footer).not.toHaveTextContent('An ancient game. An open horizon.');
  expect(footer).not.toHaveTextContent('Made for thoughtful moves.');
  expect(within(footer).getByRole('link', { name: 'License terms' })).toHaveAttribute(
    'href',
    '/LICENSE.txt',
  );
  expect(within(footer).getByRole('link', { name: 'Third-party notices' })).toHaveAttribute(
    'href',
    '/THIRD_PARTY_NOTICES.txt',
  );
  expect(
    within(footer).getByRole('link', {
      name: 'Support development on Buy Me a Coffee (opens in a new tab)',
    }),
  ).toHaveAttribute('href', 'https://buymeacoffee.com/free.dom');
  expect(within(footer).queryByRole('link', { name: /commercial licensing/i })).toBeNull();
});

it('keeps the simplified footer localized in German', () => {
  render(
    <I18nProvider>
      <ArenaShell account={player} current="dashboard">
        <main id="main-content">Content</main>
      </ArenaShell>
    </I18nProvider>,
  );
  act(() => setLanguage('de', false));
  const footer = screen.getByRole('contentinfo');
  expect(footer).toHaveTextContent('ᛟ HNEFATAFL');
  expect(footer).not.toHaveTextContent('Ein altes Spiel. Ein offener Horizont.');
  expect(footer).not.toHaveTextContent('Für durchdachte Züge gemacht.');
  expect(within(footer).getByRole('link', { name: 'Lizenzbedingungen' })).toBeVisible();
  expect(
    within(footer).getByRole('link', {
      name: 'Die Entwicklung auf Buy Me a Coffee unterstützen (öffnet in einem neuen Tab)',
    }),
  ).toHaveAttribute('href', 'https://buymeacoffee.com/free.dom');
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
      name: 'Battlefield',
    }),
  ).toHaveAttribute('aria-current', 'page');
  const bottom = screen.getByRole('navigation', { name: 'Mobile navigation' });
  expect(within(bottom).getByRole('link', { name: 'Battlefield' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(bottom).getByRole('link', { name: 'Great Hall' })).not.toHaveAttribute(
    'aria-current',
  );
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
