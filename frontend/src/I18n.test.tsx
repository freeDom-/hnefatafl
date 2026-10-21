import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewGame } from './App';
import {
  I18nProvider,
  LanguageSelect,
  LANGUAGE_KEY,
  readLanguage,
  setLanguage,
  translate,
} from './i18n';
import { localizedRules } from './locales/rules-de';
import type { Ruleset } from './types';

const rulesets: Ruleset[] = [
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: 'English rules' },
];
beforeEach(() => {
  localStorage.clear();
  setLanguage('en', false);
  vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-GB');
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

const form = () => (
  <I18nProvider>
    <LanguageSelect />
    <NewGame
      rulesets={rulesets}
      profiles={[]}
      models={[]}
      players={[]}
      onClose={vi.fn()}
      onCreated={vi.fn()}
    />
  </I18nProvider>
);

describe('English and German localization', () => {
  it('switches visible and accessible game controls live, persists the choice and keeps API identifiers unchanged', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ id: 'localized-game' }) });
    vi.stubGlobal('fetch', fetcher);
    const view = render(form());
    expect(screen.getByRole('button', { name: 'To the table' })).toBeVisible();
    fireEvent.change(screen.getByRole('combobox', { name: 'Language' }), {
      target: { value: 'de' },
    });
    expect(screen.getByRole('dialog', { name: 'Beginne eine neue Saga' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'An die Tafel' })).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Spielername der Angreifer' })).toBeVisible();
    expect(document.documentElement.lang).toBe('de');
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('de');
    fireEvent.click(screen.getByRole('button', { name: 'An die Tafel' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body.ruleset).toBe('tablut');
    expect(body.mode).toBe('human-human');
    expect(body.players.attackers.name).toBe('Attacker');
    view.unmount();
    render(form());
    expect(screen.getByRole('button', { name: 'An die Tafel' })).toBeVisible();
  });

  it('uses the browser language for invalid preferences and lets an explicit choice override it', () => {
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('de-DE');
    localStorage.setItem(LANGUAGE_KEY, 'unsupported');
    expect(readLanguage()).toBe('de');
    render(form());
    expect(screen.getByRole('combobox', { name: 'Sprache' })).toHaveValue('de');
    fireEvent.change(screen.getByRole('combobox', { name: 'Sprache' }), {
      target: { value: 'en' },
    });
    expect(readLanguage()).toBe('en');
    expect(screen.getByRole('button', { name: 'To the table' })).toBeVisible();
  });

  it('works when storage is blocked and translates rules and placeholders without changing names', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(form());
    act(() => setLanguage('de'));
    expect(screen.getByRole('button', { name: 'An die Tafel' })).toBeVisible();
    expect(translate('Delete player {name}?', { name: 'Raven King' }, 'de')).toBe(
      'Spieler Raven King löschen?',
    );
    expect(localizedRules('tablut', 'English rules')).toContain('Die Verteidiger beginnen.');
    expect(localizedRules('brandubh', 'English rules')).toContain('Die Angreifer beginnen.');
    expect(localizedRules('copenhagen', 'English rules')).toContain('Schildwall');
    expect(translate('A historical player name', {}, 'de')).toBe('A historical player name');
  });
});
