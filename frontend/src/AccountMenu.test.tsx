import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SiteHeader from './SiteHeader';
import { I18nProvider, LANGUAGE_KEY, setLanguage } from './i18n';

const account = {
  id: 'odin',
  username: 'Odin',
  email: 'odin@example.test',
  role: 'USER',
  permissions: ['play', 'profile'],
};
beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '/dashboard');
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setLanguage('en', false);
});
function header(logout = vi.fn(async () => {})) {
  render(
    <I18nProvider>
      <SiteHeader account={account} logout={logout} />
    </I18nProvider>,
  );
  return logout;
}
it('keeps the original language selector directly before the account menu, with no theme or separate logout control', () => {
  header();
  const language = screen.getByRole('combobox', { name: 'Language' });
  const trigger = screen.getByRole('button', { name: 'Account menu for Odin' });
  expect(language.closest('label')?.nextElementSibling).toContainElement(trigger);
  expect(language).toHaveValue('en');
  expect(within(language).getAllByRole('option')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Appearance settings' })).not.toBeInTheDocument();
  fireEvent.change(language, { target: { value: 'de' } });
  expect(localStorage.getItem(LANGUAGE_KEY)).toBe('de');
  expect(screen.getByRole('combobox', { name: 'Sprache' })).toHaveValue('de');
  fireEvent.click(screen.getByRole('button', { name: 'Kontomenü für Odin' }));
  expect(screen.getByRole('menuitem', { name: 'Mein Profil' })).toBeVisible();
});
it('opens and closes by click, outside pointer, focus leaving and Escape without logging out', async () => {
  const logout = header();
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', { name: 'Account menu for Odin' });
  await user.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('menu')).toHaveAttribute('id', trigger.getAttribute('aria-controls'));
  expect(screen.getByRole('menuitem', { name: 'My Profile' })).toHaveFocus();
  await user.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await user.click(trigger);
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  await user.click(trigger);
  await user.keyboard('{Escape}');
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await user.keyboard('{Enter}');
  await user.tab();
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(logout).not.toHaveBeenCalled();
});
it('supports arrows, Home and End and invokes only the existing logout callback on activation', async () => {
  const logout = header();
  const user = userEvent.setup();
  screen.getByRole('button', { name: 'Account menu for Odin' }).focus();
  await user.keyboard('{ArrowDown}');
  expect(screen.getByRole('menuitem', { name: 'My Profile' })).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(screen.getByRole('menuitem', { name: 'Log out' })).toHaveFocus();
  await user.keyboard('{ArrowDown}{End}');
  expect(screen.getByRole('menuitem', { name: 'Log out' })).toHaveFocus();
  await user.keyboard('{Home}');
  expect(screen.getByRole('menuitem', { name: 'My Profile' })).toHaveFocus();
  await user.keyboard('{Escape}{ArrowUp}');
  expect(screen.getByRole('menuitem', { name: 'Log out' })).toHaveFocus();
  expect(logout).not.toHaveBeenCalled();
  await user.keyboard('{Enter}');
  expect(logout).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
});
it('navigates to the authenticated profile without logging out', async () => {
  const logout = header();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Account menu for Odin' }));
  await user.click(screen.getByRole('menuitem', { name: 'My Profile' }));
  expect(location.pathname).toBe('/profile');
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(logout).not.toHaveBeenCalled();
});
it('keeps a long username accessible and never shows an authenticated menu for anonymous users', () => {
  const username = 'VeryLongAccountName'.repeat(3);
  const view = render(<SiteHeader account={{ ...account, username }} />);
  expect(screen.getByRole('button', { name: `Account menu for ${username}` })).toBeVisible();
  expect(screen.getByTitle(username)).toHaveClass('account-name');
  view.rerender(<SiteHeader account={null} />);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
