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
beforeEach(() => {
  mobile = true;
  seek.mockClear();
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width') && mobile,
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
      switcher={
        <nav aria-label="My games">
          <button>My current game</button>
        </nav>
      }
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

it('keeps every move in one list and preserves browsing across tabs', () => {
  const view = render(layout());
  expect(screen.getAllByRole('button', { name: /Replay to move/ })).toHaveLength(24);
  expect(screen.queryByRole('navigation', { name: 'Move history pages' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Replay to move 24' })).toBeVisible();
  const list = screen.getByRole('list');
  list.scrollTop = 80;
  fireEvent.click(screen.getByRole('button', { name: 'Replay to move 21' }));
  expect(seek).toHaveBeenCalledWith(21);
  fireEvent.click(screen.getByRole('tab', { name: 'My games' }));
  expect(
    within(screen.getByRole('tabpanel')).getByRole('button', { name: 'My current game' }),
  ).toBeVisible();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'My games' }), { key: 'Home' });
  expect(screen.getByRole('tab', { name: 'Moves 24' })).toHaveFocus();
  expect(screen.getByRole('button', { name: 'Replay to move 21' })).toBeVisible();
  expect(list.scrollTop).toBe(80);
  expect(screen.getAllByRole('grid')).toHaveLength(1);
  view.rerender(layout(1));
  expect(screen.getByRole('button', { name: 'Replay to move 1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Replay to move 1' }).parentElement).toHaveClass(
    'current',
  );
});

it('keeps only advisor setup in options and returns focus after closing', () => {
  render(layout());
  fireEvent.click(screen.getByRole('tab', { name: 'Analytics' }));
  const trigger = screen.getByRole('button', { name: 'Move advisor' });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Game options' });
  expect(
    within(dialog).queryByRole('button', { name: /Resume AI|Pause AI/ }),
  ).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('navigation')).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Players' })).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText('Advisor settings')).toBeVisible();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(screen.getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true');
});

it('leaves desktop history complete', () => {
  mobile = false;
  render(layout());
  expect(screen.getByText('Desktop details')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /Replay to move/ })).toHaveLength(24);
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
});
