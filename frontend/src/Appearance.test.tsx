import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppearanceProvider, AppearanceSettings } from './Appearance';
import Board from './Board';
import type { Game } from './types';
import { APPEARANCE_KEY, DEFAULT_APPEARANCE, parseAppearance } from './appearanceConfig';
import { I18nProvider, LANGUAGE_KEY } from './i18n';

const game: Game = {
  id: 'appearance-game',
  ruleset: 'brandubh',
  size: 3,
  board: ['A', '.', '.', '.', 'K', '.', '.', '.', 'D'],
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [{ source: 0, target: 1 }],
  mode: 'human-human',
  players: {
    attackers: { name: 'A', kind: 'human', config: {} },
    defenders: { name: 'D', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
};
let deviceDark = false;
let mediaListeners: Set<(event: MediaQueryListEvent) => void>;
beforeEach(() => {
  localStorage.clear();
  deviceDark = false;
  mediaListeners = new Set();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      get matches() {
        return deviceDark;
      },
      media: '(prefers-color-scheme: dark)',
      addEventListener: (_name: string, listener: (event: MediaQueryListEvent) => void) =>
        mediaListeners.add(listener),
      removeEventListener: (_name: string, listener: (event: MediaQueryListEvent) => void) =>
        mediaListeners.delete(listener),
    })),
  );
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
});
const renderTable = () =>
  render(
    <I18nProvider>
      <AppearanceProvider>
        <AppearanceSettings />
        <Board game={game} selected={0} onSelect={vi.fn()} />
      </AppearanceProvider>
    </I18nProvider>,
  );
const openSettings = () =>
  fireEvent.click(screen.getByRole('button', { name: 'Appearance settings' }));
const changeDevice = (dark: boolean) =>
  act(() => {
    deviceDark = dark;
    for (const listener of mediaListeners) listener({ matches: dark } as MediaQueryListEvent);
  });

describe('appearance preferences', () => {
  it('starts a fresh browser with carved wood and old walnut', () => {
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Carved wood' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Old walnut' })).toBeChecked();
    expect(document.documentElement).toHaveAttribute('data-token-theme', 'wood');
    expect(document.documentElement).toHaveAttribute('data-board-theme', 'walnut');
    expect(document.documentElement).toHaveAttribute('data-density', 'default');
    expect(screen.getByRole('radio', { name: 'Default' })).toBeChecked();
    expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#b69570');
    expect(
      screen.getByRole('gridcell', { name: 'a3, attacker' }).querySelector('.token-wood'),
    ).toBeInTheDocument();
    expect(
      screen
        .getByRole('gridcell', { name: 'b2, king' })
        .querySelector('svg[data-king-design="fjord-diadem"]'),
    ).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual(DEFAULT_APPEARANCE);
  });

  it('preserves saved classic/oak choices and defaults only missing or invalid fields', () => {
    const saved = { mode: 'light', tokens: 'classic', board: 'oak', density: 'compact' };
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(saved));
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Classic stones' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Golden oak' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Compact' })).toBeChecked();
    expect(document.documentElement).toHaveAttribute('data-density', 'compact');
    expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#e4d3ac');
    expect(
      screen
        .getByRole('gridcell', { name: 'b2, king' })
        .querySelector('[data-king-design="fjord-diadem"]'),
    ).toBeNull();
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual(saved);
    expect(parseAppearance({ mode: 'dark' })).toEqual({ ...DEFAULT_APPEARANCE, mode: 'dark' });
    expect(parseAppearance({ density: 'invalid' }).density).toBe('default');
    expect(parseAppearance({ mode: 'light', tokens: 'classic', board: 'oak' })).toEqual({
      mode: 'light',
      tokens: 'classic',
      board: 'oak',
      density: 'default',
    });
    expect(parseAppearance({ tokens: 'classic', board: 'invalid' })).toEqual({
      ...DEFAULT_APPEARANCE,
      tokens: 'classic',
    });
  });

  it('changes actual token artwork and board palette without changing game interaction, and restores choices after remount', () => {
    const view = renderTable();
    openSettings();
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Viking figures' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Fjord slate' }));
    const cell = screen.getByRole('gridcell', { name: 'a3, attacker' });
    expect(cell.querySelector('svg[data-token-art="viking"]')).toBeInTheDocument();
    expect(cell).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('gridcell', { name: 'b3, empty, legal move' })).toHaveClass('legal');
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'dark');
    expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#c4d3d5');
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual({
      mode: 'dark',
      tokens: 'viking',
      board: 'slate',
      density: 'default',
    });
    view.unmount();
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Viking figures' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Fjord slate' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: 'Carved wood' }));
    expect(
      screen
        .getByRole('gridcell', { name: 'a3, attacker' })
        .querySelector('svg[data-token-art="wood"]'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('gridcell', { name: 'b2, king' }).querySelector('.wood-carving'),
    ).toBeInTheDocument();
  });

  it('changes and restores interface scale independently of the themes', () => {
    const view = renderTable();
    openSettings();
    for (const density of ['compact', 'large', 'default'] as const) {
      fireEvent.click(
        screen.getByRole('radio', {
          name: density[0].toUpperCase() + density.slice(1),
        }),
      );
      expect(document.documentElement).toHaveAttribute('data-density', density);
      expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual({
        ...DEFAULT_APPEARANCE,
        density,
      });
    }
    fireEvent.click(screen.getByRole('radio', { name: 'Compact' }));
    view.unmount();
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Compact' })).toBeChecked();
    expect(document.documentElement).toHaveAttribute('data-density', 'compact');
  });

  it('follows changes to the device color scheme while respecting an explicit override', () => {
    deviceDark = true;
    renderTable();
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'dark');
    changeDevice(false);
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'light');
    openSettings();
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    changeDevice(true);
    changeDevice(false);
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'dark');
    fireEvent.click(screen.getByRole('radio', { name: 'Follow device' }));
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'light');
  });

  it('restores wood mode and its decorative motifs without changing board or token choices', () => {
    const view = renderTable();
    expect(document.querySelector('[data-wood-ornaments]')).toBeNull();
    openSettings();
    fireEvent.click(screen.getByRole('radio', { name: 'Wood' }));
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'wood');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(document.documentElement).toHaveAttribute('data-token-theme', 'wood');
    expect(document.documentElement).toHaveAttribute('data-board-theme', 'walnut');
    expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#b69570');
    expect(document.querySelector('[data-wood-ornaments]')).toHaveAttribute('aria-hidden', 'true');
    expect(document.querySelectorAll('[data-wood-motif]')).toHaveLength(4);
    changeDevice(true);
    changeDevice(false);
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'wood');
    view.unmount();
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Wood' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(document.querySelector('[data-wood-ornaments]')).toBeNull();
    expect(document.documentElement.style.colorScheme).toBe('light');
  });

  it('uses German appearance labels while saving the same theme identifiers', () => {
    localStorage.setItem(LANGUAGE_KEY, 'de');
    renderTable();
    fireEvent.click(screen.getByRole('button', { name: 'Darstellungseinstellungen' }));
    expect(screen.getByRole('radio', { name: 'Geschnitztes Holz' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Alter Nussbaum' })).toBeChecked();
    expect(screen.getByText('Oberflächengröße')).toBeVisible();
    expect(screen.getByRole('radio', { name: 'Kompakt' })).toBeVisible();
    expect(screen.getByRole('radio', { name: 'Standard' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Groß' })).toBeVisible();
    fireEvent.click(screen.getByRole('radio', { name: 'Holz' }));
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual({
      ...DEFAULT_APPEARANCE,
      mode: 'wood',
    });
    expect(screen.getByRole('button', { name: 'Zurück zum Spieltisch' })).toBeInTheDocument();
  });

  it('recovers from corrupt or invalid saved preferences instead of breaking the board', () => {
    localStorage.setItem(APPEARANCE_KEY, '{not valid JSON');
    const view = renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Carved wood' })).toBeChecked();
    expect(screen.getAllByRole('gridcell')).toHaveLength(9);
    view.unmount();
    localStorage.setItem(
      APPEARANCE_KEY,
      JSON.stringify({ mode: 'invalid', tokens: {}, board: '../no' }),
    );
    renderTable();
    openSettings();
    expect(screen.getByRole('radio', { name: 'Follow device' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Old walnut' })).toBeChecked();
  });

  it('still lets users customize and dismiss the dialog when browser storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderTable();
    openSettings();
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-color-mode', 'dark');
    expect(screen.getByRole('status')).toHaveTextContent('apply for this visit');
    const dialog = screen.getByRole('dialog', { name: 'Appearance settings' });
    fireEvent(dialog, new Event('cancel', { bubbles: true, cancelable: true }));
    expect(dialog).not.toHaveAttribute('open');
  });
});
