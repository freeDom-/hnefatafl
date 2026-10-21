import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Archive from './Archive';
import Training from './Training';
import App from './App';
import { sortRulesets } from './sortRulesets';
import { suggestVikingName } from './vikingNames';
import type { Game, Model, Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'copenhagen', name: 'Copenhagen', size: 11, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const game = {
  id: 'battle',
  ruleset: 'tablut',
  created_at: '',
  winner: null,
  total_moves: 3,
  players: {
    attackers: {
      name: 'Jarl',
      kind: 'ai',
      config: { algorithm: 'alpha_beta', depth: 2, time_limit: 1, seed: 8 },
    },
    defenders: { name: 'Astrid', kind: 'human', config: {} },
  },
} as unknown as Game;
const model: Model = {
  id: 'model-one',
  name: 'Astrid Stormshield',
  ruleset: 'tablut',
  version: '1',
};
const noop = vi.fn();

beforeEach(() => {
  localStorage.clear();
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

describe('records and training controls', () => {
  it('orders battlefield sizes without mutating data and suggests unused Viking names', () => {
    expect(sortRulesets(rules).map((rule) => rule.size)).toEqual([7, 9, 11]);
    expect(rules.map((rule) => rule.size)).toEqual([11, 9, 7]);
    const first = suggestVikingName([], () => 0);
    const next = suggestVikingName([first.toUpperCase()], () => 0);
    expect(first).toMatch(/^\w+ \w+$/);
    expect(next).not.toBe(first);
  });

  it('shows different AI parameters in game records and statistics without nested buttons', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path.startsWith('/api/games')
          ? json([game])
          : json({
              by_player: [
                {
                  id: 'a',
                  kind: 'ai',
                  name: 'Jarl',
                  games: 1,
                  config: { algorithm: 'alpha_beta', depth: 2 },
                  variant_id: 'variant-a',
                },
                {
                  id: 'b',
                  kind: 'ai',
                  name: 'Jarl',
                  games: 1,
                  config: { algorithm: 'alpha_beta', depth: 4 },
                  variant_id: 'variant-b',
                },
              ],
            }),
      ),
    );
    const { container } = render(
      <Archive rulesets={rules} players={[]} onLoad={noop} onError={noop} refreshPlayers={noop} />,
    );
    await screen.findByRole('button', { name: /Tablut.*Jarl vs Astrid/ });
    expect(container.querySelectorAll('.record-parameters')).toHaveLength(3);
    const aiRows = screen.getByRole('table').querySelectorAll('tbody tr');
    expect(
      within(aiRows[0] as HTMLElement).getByText('depth 2', { exact: false }),
    ).toBeInTheDocument();
    expect(
      within(aiRows[1] as HTMLElement).getByText('depth 4', { exact: false }),
    ).toBeInTheDocument();
    fireEvent.click(container.querySelector('.record-parameters summary')!);
    expect(container.querySelector('.record-parameters')).toHaveAttribute('open');
    expect(screen.getByText('Random seed')).toBeInTheDocument();
    expect(container.querySelector('.record button')).toBeNull();
    expect(
      within(screen.getByLabelText('Battlefield'))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['All rulesets', 'Brandubh', 'Tablut', 'Copenhagen']);
  });

  it('requires confirmation before deleting a human profile and keeps historical records visible', async () => {
    const refresh = vi.fn();
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') return json({ deleted: true, id: 'human-one' });
      return path.startsWith('/api/games') ? json([game]) : json({});
    });
    vi.stubGlobal('fetch', fetcher);
    render(
      <Archive
        rulesets={rules}
        players={[{ id: 'human-one', name: 'Astrid' }]}
        onLoad={noop}
        onError={noop}
        refreshPlayers={refresh}
      />,
    );
    await screen.findByRole('button', { name: /Tablut.*Jarl vs Astrid/ });
    fireEvent.click(screen.getByRole('button', { name: 'Delete player Astrid' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete player Astrid?' });
    expect(
      within(confirm).getByText(/saved games, name and statistics remain/),
    ).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(fetcher).toHaveBeenCalledWith(
      '/api/players/human-one',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(screen.getByRole('button', { name: /Tablut.*Jarl vs Astrid/ })).toBeInTheDocument();
  });

  it('keeps an in-use model deletion error visible and suggests a different model name', async () => {
    const changed = vi.fn();
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE')
        return json({ detail: 'This model is used by an unfinished game.' }, false);
      if (path === `/api/models/${model.id}/usage`)
        return json({ model_id: model.id, unfinished_games: [], running_tournaments: [] });
      return path === '/api/training/device' ? json({ default_device: 'cpu' }) : json([]);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<Training rulesets={rules} models={[model]} onModelsChanged={changed} onError={noop} />);
    const input = screen.getByLabelText('Model name') as HTMLInputElement;
    const before = input.value;
    fireEvent.click(screen.getByRole('button', { name: 'Suggest Viking name' }));
    expect(input.value).not.toBe(before);
    expect(input.value).not.toBe(model.name);
    fireEvent.click(screen.getByRole('button', { name: `Delete model ${model.name}` }));
    const confirm = screen.getByRole('dialog', { name: `Delete model ${model.name}?` });
    expect(
      within(confirm).getByText(
        /Completed game histories, statistics and training checkpoints are kept/,
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(within(confirm).getByRole('button', { name: 'Delete' })).toBeEnabled(),
    );
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    expect(await within(confirm).findByRole('alert')).toHaveTextContent('unfinished game');
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: model.name })).toBeInTheDocument();
    expect(
      within(screen.getByLabelText('Battlefield'))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Brandubh', 'Tablut', 'Copenhagen']);
  });

  it('uses a single human dropdown, then clears the profile identity when choosing a new name', async () => {
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/rulesets') return json(rules);
      if (path === '/api/players') return json([{ id: 'astrid-one', name: 'Astrid' }]);
      if (path === '/api/games')
        return json({
          ...game,
          id: 'created',
          size: 9,
          board: [],
          legal_moves: [],
          moves: [],
          captured: { attackers: 0, defenders: 0 },
          cursor: 0,
        });
      return json([]);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    await screen.findByRole('dialog', { name: 'Begin a new saga' });
    const select = screen.getByRole('combobox', { name: 'Attackers human player' });
    fireEvent.change(select, { target: { value: 'astrid-one' } });
    expect(screen.queryByLabelText('Attackers player name')).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: '' } });
    const name = screen.getByLabelText('Attackers player name');
    expect(name).toHaveValue('');
    fireEvent.change(name, { target: { value: 'Freydis' } });
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([path]) => path === '/api/games')).toBe(true),
    );
    const request = fetcher.mock.calls.find(([path]) => path === '/api/games') as unknown as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(String(request[1].body));
    expect(body.players.attackers.name).toBe('Freydis');
    expect(body.players.attackers.player_id).toBeUndefined();
  });
});
