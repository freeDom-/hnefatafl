import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Archive from './Archive';
import type { Game, Ruleset } from './types';

const rulesets: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const game = (id = 'saved-one', changes: Partial<Game> = {}): Game => ({
  id,
  version: 1,
  ruleset: 'brandubh',
  mode: 'human-human',
  size: 7,
  board: [],
  turn: 'attackers',
  ply: 8,
  winner: 'attackers',
  reason: 'king_captured',
  total_moves: 8,
  cursor: 8,
  moves: [],
  legal_moves: [],
  players: {
    attackers: { name: 'Astrid', kind: 'human', config: {} },
    defenders: { name: 'Freya', kind: 'human', config: {} },
  },
  captured: { attackers: 0, defenders: 0 },
  created_at: '2026-10-21T08:00:00Z',
  ...changes,
});
const stats = (count: number) => ({
  total_games: count,
  completed_games: count,
  attackers_wins: count,
  attacker_win_rate: count ? 1 : 0,
  average_game_length: count ? 8 : 0,
  by_ruleset: count
    ? [{ ruleset: 'brandubh', games: count, attacker_win_rate: 1, average_game_length: 8 }]
    : [],
  by_player: [],
});
const json = (value: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => value } as Response);
const noError = vi.fn();

beforeEach(() => {
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
  vi.clearAllMocks();
});
const show = (onGameDeleted = vi.fn(), onLoad = vi.fn()) =>
  render(
    <Archive
      rulesets={rulesets}
      players={[]}
      onLoad={onLoad}
      onError={noError}
      refreshPlayers={vi.fn()}
      onGameDeleted={onGameDeleted}
    />,
  );

describe('statistics and saved game management', () => {
  it('puts statistics before paged, collapsible history', async () => {
    const games = Array.from({ length: 12 }, (_, index) => game(`game-${index}`));
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => json(path.startsWith('/api/games') ? games : stats(12))),
    );
    const { container } = show();
    await screen.findByRole('button', { name: 'Delete game game-0' });
    const statistics = screen.getByRole('region', { name: 'Statistics' });
    const history = container.querySelector('.records-history')!;
    expect(statistics.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(container.querySelectorAll('.record-entry')).toHaveLength(10);
    fireEvent.click(screen.getByRole('button', { name: 'Show 10 more games' }));
    expect(container.querySelectorAll('.record-entry')).toHaveLength(12);
    fireEvent.click(container.querySelector('.archive-history-summary')!);
    expect(history).not.toHaveAttribute('open');
    expect(statistics).toBeVisible();
    expect(container.querySelector('[aria-label="Delete game game-0"]')).not.toBeVisible();
  });

  it('confirms game deletion and refreshes all statistics from the remaining records', async () => {
    let deleted = false;
    const deletedGame = vi.fn();
    const fetcher = vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'DELETE') {
        deleted = true;
        return json({ deleted: true, id: 'saved-one' });
      }
      return json(
        path.startsWith('/api/games') ? (deleted ? [] : [game()]) : stats(deleted ? 0 : 1),
      );
    });
    vi.stubGlobal('fetch', fetcher);
    show(deletedGame);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete game saved-one' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete recorded game?' });
    expect(confirm).toHaveTextContent('Statistics are recalculated');
    expect(confirm).toHaveTextContent('Astrid vs Freya');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Keep it' }));
    expect(fetcher.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Delete game saved-one' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletedGame).toHaveBeenCalledWith('saved-one'));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Statistics' })).toHaveTextContent('0 completed'),
    );
    expect(screen.queryByRole('button', { name: 'Delete game saved-one' })).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith('/api/games/saved-one?version=1', { method: 'DELETE' });
    expect(screen.getByRole('status')).toHaveTextContent('Game removed');
    expect(noError).not.toHaveBeenCalled();
  });

  it('requires renewed confirmation with the latest game version after a concurrent move', async () => {
    let attempts = 0;
    const deletedGame = vi.fn();
    const fetcher = vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'DELETE') {
        attempts++;
        return attempts === 1 ? json({ detail: 'Game changed' }, 409) : json({ deleted: true });
      }
      if (path === '/api/games/saved-one')
        return json(game('saved-one', { version: 2, total_moves: 14 }));
      return json(
        path.startsWith('/api/games')
          ? attempts > 1
            ? []
            : [game()]
          : stats(attempts > 1 ? 0 : 1),
      );
    });
    vi.stubGlobal('fetch', fetcher);
    show(deletedGame);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete game saved-one' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete recorded game?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await within(confirm).findByRole('alert');
    expect(confirm).toHaveTextContent('choose Delete again');
    expect(confirm).toHaveTextContent('14 moves');
    expect(deletedGame).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletedGame).toHaveBeenCalledOnce());
    expect(fetcher).toHaveBeenCalledWith('/api/games/saved-one?version=2', { method: 'DELETE' });
  });

  it('clears a stale current-game pointer when the record was already deleted elsewhere', async () => {
    let missing = false;
    const deletedGame = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'DELETE') {
          missing = true;
          return json({ detail: 'Game not found.' }, 404);
        }
        return json(
          path.startsWith('/api/games') ? (missing ? [] : [game()]) : stats(missing ? 0 : 1),
        );
      }),
    );
    show(deletedGame);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete game saved-one' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletedGame).toHaveBeenCalledWith('saved-one'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(noError).not.toHaveBeenCalled();
  });

  it('does not reopen a deleted game when an earlier load response arrives late', async () => {
    let finishLoad!: (response: Response) => void;
    let deleted = false;
    const onLoad = vi.fn();
    const onDeleted = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'DELETE') {
          deleted = true;
          return json({ deleted: true });
        }
        if (path === '/api/games/saved-one')
          return new Promise<Response>((resolve) => {
            finishLoad = resolve;
          });
        return json(
          path.startsWith('/api/games') ? (deleted ? [] : [game()]) : stats(deleted ? 0 : 1),
        );
      }),
    );
    const { container } = show(onDeleted, onLoad);
    await screen.findByRole('button', { name: 'Delete game saved-one' });
    fireEvent.click(container.querySelector('.record')!);
    fireEvent.click(screen.getByRole('button', { name: 'Delete game saved-one' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith('saved-one'));
    await act(async () => finishLoad(await json(game())));
    expect(onLoad).not.toHaveBeenCalled();
  });
});
