import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import GameLayout from './GameLayout';
import HistoryPanel, { type HistoryEntry } from './HistoryPanel';
import ArmiesPanel from './ArmiesPanel';

const seek = vi.fn();
const entries = (cursor = 24): HistoryEntry[] =>
  Array.from({ length: 24 }, (_, i) => ({
    ply: i + 1,
    notation: `a${i + 1} → b${i + 1}`,
    side: i % 2 ? 'defenders' : 'attackers',
    current: i + 1 === cursor,
    future: i + 1 > cursor,
    onSeek: () => seek(i + 1),
  }));
let mobile = true;
let short = false;
beforeEach(() => {
  mobile = true;
  short = false;
  seek.mockClear();
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width') ? mobile : short,
    addEventListener() {},
    removeEventListener() {},
  }));
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function layout(cursor = 24) {
  return (
    <GameLayout
      sidebar={<p>Desktop details</p>}
      battlefield={<div role="grid" aria-label="Board" />}
      history={<HistoryPanel count={24} entries={entries(cursor)} />}
      mobile={{
        title: 'Copenhagen',
        subtitle: '11 × 11 · Move 24',
        moveCount: 24,
        players: (
          <ArmiesPanel
            rows={[
              {
                side: 'attackers',
                name: 'Astrid',
                detail: 'Attackers · Human',
                symbol: 'A',
                toMove: true,
              },
              {
                side: 'defenders',
                name: 'Huginn',
                detail: 'Defenders · AI',
                symbol: 'D',
                toMove: false,
              },
            ]}
            captured={{ attackers: 2, defenders: 1 }}
          />
        ),
        details: <p>Game details</p>,
        analytics: null,
        settings: (
          <label>
            Advisor settings
            <input />
          </label>
        ),
      }}
    />
  );
}

it('pages the full history, preserves a browsed page across tabs, and follows a changed replay cursor', () => {
  const view = render(layout());
  expect(screen.getAllByRole('button', { name: /Replay to move/ })).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Replay to move 24' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Earlier moves' }));
  fireEvent.click(screen.getByRole('button', { name: 'Replay to move 21' }));
  expect(seek).toHaveBeenCalledWith(21);
  fireEvent.click(screen.getByRole('tab', { name: 'Players' }));
  expect(within(screen.getByRole('tabpanel')).getByText('Captured')).toBeVisible();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Players' }), { key: 'Home' });
  expect(screen.getByRole('tab', { name: 'Moves 24' })).toHaveFocus();
  expect(screen.getByRole('button', { name: 'Replay to move 21' })).toBeVisible();
  expect(screen.getAllByRole('grid')).toHaveLength(1);
  view.rerender(layout(1));
  expect(screen.getByRole('button', { name: 'Replay to move 1' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Latest' }));
  expect(screen.getByRole('button', { name: 'Replay to move 24' })).toBeVisible();
});

it('keeps advisor setup and game details in options and returns focus after closing', () => {
  render(layout());
  fireEvent.click(screen.getByRole('tab', { name: 'Analytics' }));
  const trigger = screen.getByRole('button', { name: 'Move advisor' });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Game options' });
  expect(within(dialog).getByText('Game details')).toBeVisible();
  expect(within(dialog).getByLabelText('Advisor settings')).toBeVisible();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(screen.getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true');
});

it('uses one paged row on short phones and leaves desktop history complete', () => {
  short = true;
  const view = render(layout());
  expect(screen.getAllByRole('button', { name: /Replay to move/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Earlier moves' }));
  expect(screen.getByRole('button', { name: 'Replay to move 23' })).toBeVisible();
  view.unmount();
  mobile = false;
  render(layout());
  expect(screen.getByText('Desktop details')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /Replay to move/ })).toHaveLength(24);
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
});
