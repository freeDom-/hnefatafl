import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Archive from './Archive';
import type { Game, Ruleset, StatRow } from './types';

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

describe('interactive statistics', () => {
  const seer: StatRow = {
    id: 'seer',
    name: 'Hugin',
    kind: 'ai',
    profile_name: 'Seer',
    profile_id: 'mcts',
    games: 10,
    wins: 6,
    draws: 1,
    losses: 3,
    win_rate: 0.6,
    draw_rate: 0.1,
    loss_rate: 0.3,
    average_game_length: 42,
    config: { algorithm: 'mcts', iterations: 100, time_limit: 5, seed: 41 },
    variant_id: 'ai:seer-configuration',
  };
  const rows: StatRow[] = [
    seer,
    {
      id: 'astrid',
      name: 'Astrid',
      kind: 'human',
      games: 2,
      wins: 1,
      losses: 1,
      win_rate: 0.5,
      loss_rate: 0.5,
      average_game_length: 9,
    },
    {
      id: 'erik',
      name: 'Erik',
      kind: 'human',
      games: 8,
      wins: 2,
      losses: 6,
      win_rate: 0.25,
      loss_rate: 0.75,
      average_game_length: 18,
    },
  ];
  const boards: Ruleset[] = [
    { id: 'copenhagen', name: 'Copenhagen', size: 11, description: '', rules_markdown: '' },
    { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
    ...rulesets,
  ];
  const showStatistics = () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(
          path.startsWith('/api/games')
            ? [
                game('astrid-game'),
                game('hugin-game', {
                  players: {
                    attackers: {
                      name: 'Hugin',
                      kind: 'ai',
                      profile_name: 'Seer',
                      config: seer.config!,
                    },
                    defenders: { name: 'Erik', kind: 'human', config: {} },
                  },
                }),
              ]
            : {
                ...stats(2),
                by_player: rows,
                by_ruleset: [
                  { ruleset: 'copenhagen', games: 10 },
                  { ruleset: 'tablut', games: 2 },
                  { ruleset: 'brandubh', games: 8 },
                ],
              },
        ),
      ),
    );
    return render(
      <Archive
        rulesets={boards}
        players={[]}
        onLoad={vi.fn()}
        onError={noError}
        refreshPlayers={vi.fn()}
      />,
    );
  };
  const playerNames = (table: HTMLElement) =>
    Array.from(table.querySelectorAll('tbody .stat-player-name')).map((name) => name.textContent);

  it('preserves board-size order initially and sorts all headers in both directions', async () => {
    showStatistics();
    const battlefield = await screen.findByRole('table', { name: 'Results by battlefield' });
    expect(
      Array.from(battlefield.querySelectorAll('tbody th')).map((cell) => cell.textContent),
    ).toEqual(['Brandubh', 'Tablut', 'Copenhagen']);
    const performance = screen.getByRole('table', { name: 'Player & AI performance' });
    expect(playerNames(performance)).toEqual(['Hugin', 'Erik', 'Astrid']);
    expect(
      within(performance).getByRole('button', { name: 'Sort by W / D / L' }).closest('th'),
    ).toHaveAttribute('aria-sort', 'descending');
    const played = within(performance).getByRole('button', { name: 'Sort by Played' });
    fireEvent.click(played);
    expect(playerNames(performance)).toEqual(['Astrid', 'Erik', 'Hugin']);
    expect(played.closest('th')).toHaveAttribute('aria-sort', 'ascending');
    fireEvent.click(played);
    expect(playerNames(performance)).toEqual(['Hugin', 'Erik', 'Astrid']);
    for (const table of [battlefield, performance]) {
      for (const button of table.querySelectorAll('thead button')) {
        const previous = button.closest('th')?.getAttribute('aria-sort');
        fireEvent.click(button);
        expect(button.closest('th')).toHaveAttribute(
          'aria-sort',
          previous === 'ascending' ? 'descending' : 'ascending',
        );
        fireEvent.click(button);
        expect(button.closest('th')).toHaveAttribute(
          'aria-sort',
          previous === 'ascending' ? 'ascending' : 'descending',
        );
      }
    }
  });

  it('filters player metadata and either recorded participant independently of the totals', async () => {
    const { container } = showStatistics();
    const performance = await screen.findByRole('table', { name: 'Player & AI performance' });
    fireEvent.change(screen.getByLabelText('Filter players'), { target: { value: '  SEER  ' } });
    expect(playerNames(performance)).toEqual(['Hugin']);
    expect(container.querySelectorAll('.record-entry')).toHaveLength(2);
    fireEvent.change(screen.getByLabelText('Filter recorded games'), {
      target: { value: '  erIK ' },
    });
    expect(container.querySelectorAll('.record-entry')).toHaveLength(1);
    expect(container.querySelector('.record-entry')).toHaveAttribute('data-game-id', 'hugin-game');
    expect(playerNames(performance)).toEqual(['Hugin']);
    expect(screen.getByRole('region', { name: 'Statistics' })).toHaveTextContent('2 completed');
    fireEvent.change(screen.getByLabelText('Filter players'), { target: { value: 'Nobody' } });
    expect(screen.getByText('No players match this filter.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear player filter' }));
    expect(playerNames(performance)).toHaveLength(3);
    fireEvent.change(screen.getByLabelText('Filter recorded games'), {
      target: { value: 'Nobody' },
    });
    expect(screen.getByText('No recorded games match this name.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear recorded game filter' }));
    expect(container.querySelectorAll('.record-entry')).toHaveLength(2);
  });

  it('keeps AI statistics in one compact row with full parameters in a dismissible dialog', async () => {
    showStatistics();
    const table = await screen.findByRole('table', { name: 'Player & AI performance' });
    expect(table.querySelectorAll('tbody tr')).toHaveLength(3);
    const toggle = within(table).getByRole('button', { name: 'Show AI parameters for Hugin' });
    const row = toggle.closest('tr')!;
    expect(row.querySelector('details')).toBeNull();
    expect(row).toHaveTextContent(
      'Computer · Seer · Monte Carlo tree search · 100 iterations · 5s',
    );
    expect(
      toggle.compareDocumentPosition(row.querySelector('.stat-player-name')!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    toggle.focus();
    fireEvent.click(toggle);
    const dialog = screen.getByRole('dialog', { name: 'Hugin AI parameters' });
    expect(within(dialog).getByText('Random seed')).toBeVisible();
    expect(dialog).toHaveTextContent('41');
    expect(dialog).toHaveTextContent('Configuration ai:seer-co');
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
    expect(table.querySelectorAll('tbody tr')).toHaveLength(3);
  });
});
