import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Tournaments from './Tournaments';
import type { AIProfile, Model, Ruleset } from './types';
import type { Tournament } from './tournamentTypes';

const rulesets: Ruleset[] = [
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const profiles: AIProfile[] = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    difficulty: '',
    parameters: {},
    supported_rulesets: [],
  },
  {
    id: 'alpha_beta',
    name: 'Jarl',
    algorithm: 'alpha_beta',
    difficulty: '',
    parameters: { depth: 2 },
    supported_rulesets: [],
  },
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: '',
    parameters: { iterations: 40 },
    supported_rulesets: [],
  },
];
const saved = (id: string): Tournament => ({
  id,
  name: `Challenge ${id}`,
  status: 'completed',
  created_at: '2026-10-21T08:00:00Z',
  config: {
    players: [
      { name: 'Raven', config: { algorithm: 'baseline', time_limit: 0.1 } },
      { name: 'Wolf', config: { algorithm: 'alpha_beta', depth: 2 } },
    ],
    rulesets: ['brandubh'],
    games: 2,
    max_plies: 1,
    seed: 19,
  },
  progress: { completed_games: 2, total_games: 2 },
});
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
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
});
const show = (models: Model[] = []) =>
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={models} onError={vi.fn()} />);

describe('tournament setup and saved selection', () => {
  it('submits balanced game counts and independent opponent settings', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('new'));
        }
        return json(path === '/api/tournaments' ? [] : saved('new'));
      }),
    );
    show();
    const newButton = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(newButton).toBeEnabled());
    fireEvent.click(newButton);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    expect(setup.getAllByRole('checkbox').map((box) => box.getAttribute('aria-label'))).toEqual([
      'Brandubh 7 × 7',
      'Tablut 9 × 9',
    ]);
    fireEvent.change(setup.getByLabelText('Tournament name'), {
      target: { value: 'Ravens at dawn' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 1 time limit (seconds)'), {
      target: { value: '0.02' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 2 time limit (seconds)'), {
      target: { value: '0.15' },
    });
    fireEvent.change(setup.getByLabelText('Games per battlefield'), { target: { value: '4' } });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      name: 'Ravens at dawn',
      games: 4,
      rulesets: ['brandubh'],
      players: [
        { config: { algorithm: 'baseline', time_limit: 0.02 } },
        { config: { algorithm: 'alpha_beta', depth: 2, time_limit: 0.15 } },
      ],
    });
  });

  it('requires a single compatible battlefield before allowing trained models', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    show([{ id: 'raven-model', name: 'Raven model', ruleset: 'brandubh' }]);
    const newButton = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(newButton).toBeEnabled());
    fireEvent.click(newButton);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: 'neural' } });
    const start = setup.getByRole('button', { name: 'Start tournament' });
    expect(start).toBeDisabled();
    fireEvent.change(setup.getByLabelText('Opponent 1 trained model'), {
      target: { value: 'raven-model' },
    });
    expect(start).toBeEnabled();
    fireEvent.click(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' }));
    expect(start).toBeDisabled();
    expect(
      setup.getByText('Choose a compatible trained model and its single matching battlefield.'),
    ).toBeInTheDocument();
  });

  it('keeps the latest selected result when an earlier detail request finishes late', async () => {
    let finish!: (response: Response) => void;
    let firstA = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments') return json([saved('a'), saved('b')]);
        if (path === '/api/tournaments/a' && firstA) {
          firstA = false;
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        return json(saved('b'));
      }),
    );
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'View tournament Challenge b' }));
    await screen.findByRole('heading', { name: 'Challenge b' });
    await act(async () => finish(await json(saved('a'))));
    expect(screen.getByRole('heading', { name: 'Challenge b' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Challenge a' })).not.toBeInTheDocument();
  });
});
