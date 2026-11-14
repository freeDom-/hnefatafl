import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppearanceProvider, AppearanceSettings } from './Appearance';
import Board from './Board';
import type { Game } from './types';
import {
  APPEARANCE_KEY,
  DEFAULT_APPEARANCE,
  LARGE_VIEWPORT_QUERY,
  UI_THEMES,
} from './appearanceConfig';
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

let large = false;
let listeners: Set<() => void>;
beforeEach(() => {
  localStorage.clear();
  large = false;
  listeners = new Set();
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      get matches() {
        return query === LARGE_VIEWPORT_QUERY && large;
      },
      addEventListener: (_: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
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
function table() {
  return render(
    <I18nProvider>
      <AppearanceProvider>
        <AppearanceSettings />
        <Board game={game} selected={0} onSelect={vi.fn()} />
      </AppearanceProvider>
    </I18nProvider>,
  );
}
function open() {
  fireEvent.click(screen.getByRole('button', { name: 'Appearance settings' }));
}
function resize(value: boolean) {
  act(() => {
    large = value;
    listeners.forEach((listener) => listener());
  });
}
it('offers exactly four UI themes and independent tokens, board and scale without obsolete modes', () => {
  table();
  open();
  for (const theme of UI_THEMES)
    expect(screen.getByRole('radio', { name: theme.name })).toBeVisible();
  for (const name of ['Light', 'Dark', 'Wood', 'Follow device', 'Classic look'])
    expect(screen.queryByRole('radio', { name })).toBeNull();
  expect(screen.getByRole('radio', { name: 'The King’s Table' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Carved wood' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Old walnut' })).toBeChecked();
  expect(document.documentElement).not.toHaveAttribute('data-color-mode');
  expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual(DEFAULT_APPEARANCE);
});
it.each(UI_THEMES)(
  'persists $id without changing board, tokens or explicit scale or gameplay',
  (theme) => {
    localStorage.setItem(
      APPEARANCE_KEY,
      JSON.stringify({ mode: 'wood', tokens: 'classic', board: 'slate', density: 'compact' }),
    );
    const view = table();
    open();
    fireEvent.click(screen.getByRole('radio', { name: theme.name }));
    expect(screen.getByRole('gridcell', { name: 'a3, attacker' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('gridcell', { name: 'b3, empty, legal move' })).toHaveClass('legal');
    expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#c4d3d5');
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toEqual({
      version: 2,
      uiTheme: theme.id,
      tokens: 'classic',
      board: 'slate',
      density: 'compact',
    });
    view.unmount();
    table();
    open();
    expect(screen.getByRole('radio', { name: theme.name })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Compact' })).toBeChecked();
  },
);
it('changes actual token art and board colours while retaining independent theme and turn', () => {
  table();
  open();
  fireEvent.click(screen.getByRole('radio', { name: 'Viking figures' }));
  fireEvent.click(screen.getByRole('radio', { name: 'Golden oak' }));
  expect(
    screen
      .getByRole('gridcell', { name: 'a3, attacker' })
      .querySelector('svg[data-token-art="viking"]'),
  ).toBeInTheDocument();
  expect(document.documentElement.style.getPropertyValue('--board-light')).toBe('#e4d3ac');
  expect(document.documentElement).toHaveAttribute('data-ui-theme', 'kings-table');
  fireEvent.click(screen.getByRole('radio', { name: 'Carved wood' }));
  expect(
    screen
      .getByRole('gridcell', { name: 'b2, king' })
      .querySelector('[data-king-design="fjord-diadem"]'),
  ).toBeInTheDocument();
});
it('uses viewport defaults without persisting an automatic choice, respecting explicit overrides across resize and reload', () => {
  const view = table();
  open();
  expect(screen.getByRole('radio', { name: 'Default' })).toBeChecked();
  resize(true);
  expect(screen.getByRole('radio', { name: 'Large' })).toBeChecked();
  expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).not.toHaveProperty('density');
  for (const name of ['Compact', 'Default', 'Large']) {
    fireEvent.click(screen.getByRole('radio', { name }));
    resize(false);
    resize(true);
    expect(screen.getByRole('radio', { name })).toBeChecked();
  }
  view.unmount();
  table();
  open();
  resize(false);
  expect(screen.getByRole('radio', { name: 'Large' })).toBeChecked();
});
it('preserves an explicit old default scale at 4K and does not mutate unrelated preferences', () => {
  large = true;
  localStorage.setItem('hnefatafl-play-route', '/games/keep');
  localStorage.setItem(
    APPEARANCE_KEY,
    JSON.stringify({
      mode: 'dark',
      uiTheme: 'legacy',
      density: 'default',
      tokens: 'viking',
      board: 'oak',
    }),
  );
  const view = table();
  expect(document.documentElement).toHaveAttribute('data-ui-theme', 'iron-ash');
  expect(document.documentElement).toHaveAttribute('data-density', 'default');
  const migrated = localStorage.getItem(APPEARANCE_KEY);
  view.unmount();
  table();
  expect(localStorage.getItem(APPEARANCE_KEY)).toBe(migrated);
  expect(localStorage.getItem('hnefatafl-play-route')).toBe('/games/keep');
});
it('localizes supported appearance controls in German without obsolete labels', () => {
  localStorage.setItem(LANGUAGE_KEY, 'de');
  table();
  fireEvent.click(screen.getByRole('button', { name: 'Darstellungseinstellungen' }));
  expect(screen.getByRole('radio', { name: 'Geschnitztes Holz' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Standard' })).toBeChecked();
  expect(screen.queryByText('Farbmodus')).toBeNull();
  expect(screen.getByRole('button', { name: 'Zurück zum Spieltisch' })).toBeVisible();
});
it('recovers from corrupt preferences and permits visit-only changes when storage is blocked', () => {
  localStorage.setItem(APPEARANCE_KEY, '{');
  const view = table();
  open();
  expect(screen.getAllByRole('gridcell')).toHaveLength(9);
  view.unmount();
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw Error('blocked');
  });
  table();
  open();
  fireEvent.click(screen.getByRole('radio', { name: 'Saga Hall' }));
  expect(document.documentElement).toHaveAttribute('data-ui-theme', 'saga-hall');
  expect(screen.getByRole('status')).toHaveTextContent('apply for this visit');
  const dialog = screen.getByRole('dialog');
  fireEvent(dialog, new Event('cancel', { bubbles: true, cancelable: true }));
  expect(dialog).not.toHaveAttribute('open');
});

it.each([false, true])(
  'locks an explicit choice when the already-selected automatic radio is clicked (large viewport: %s)',
  (value) => {
    large = value;
    table();
    open();
    const name = value ? 'Large' : 'Default';
    fireEvent.click(screen.getByRole('radio', { name }));
    expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!)).toHaveProperty(
      'density',
      value ? 'large' : 'default',
    );
    resize(!value);
    expect(screen.getByRole('radio', { name })).toBeChecked();
  },
);
