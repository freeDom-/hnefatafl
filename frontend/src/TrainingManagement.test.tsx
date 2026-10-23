import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Training from './Training';
import type { Model, Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const model: Model = {
  id: 'raven-model',
  name: 'Raven strategist',
  ruleset: 'brandubh',
  training_config: { seed: 17 },
  evaluation: { seed: 1000017, wins: 1, losses: 1 },
};
const unfinished = (id: string) => ({
  id,
  version: 4,
  ruleset: 'brandubh',
  created_at: '2026-10-21T08:00:00Z',
  total_moves: 4,
  players: { attackers: { name: 'Astrid' }, defenders: { name: 'Raven' } },
});
const usage = (games: ReturnType<typeof unfinished>[] = [], token = 'preview-one') => ({
  model_id: model.id,
  usage_token: token,
  unfinished_games: games,
  running_tournaments: [] as { id: string; name: string; status: string }[],
});
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const noop = vi.fn();

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

function show(models: Model[] = [model], onGamesDeleted = vi.fn(), onModelsChanged = vi.fn()) {
  render(
    <Training
      rulesets={rules}
      models={models}
      onError={noop}
      onModelsChanged={onModelsChanged}
      onGamesDeleted={onGamesDeleted}
    />,
  );
  return { onGamesDeleted, onModelsChanged };
}
function openDelete() {
  const card = screen.getByText(model.name).closest('article')!;
  fireEvent.click(within(card).getByRole('button', { name: `Delete model ${model.name}` }));
  return within(screen.getByRole('dialog', { name: `Delete model ${model.name}?` }));
}

describe('workshop model provenance and deletion', () => {
  it('shows the training seed rather than the evaluation seed, and labels missing provenance', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    const { container } = render(
      <Training
        rulesets={rules}
        models={[model, { id: 'legacy', name: 'Legacy', ruleset: 'brandubh' }]}
        onError={noop}
        onModelsChanged={noop}
      />,
    );
    const seeds = [...container.querySelectorAll('.model-training-seed')];
    expect(seeds[0]).toHaveTextContent('Training seed 17');
    expect(seeds[0]).not.toHaveTextContent('1000017');
    expect(seeds[1]).toHaveTextContent('Training seed Not recorded');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Model collection/ })).toBeInTheDocument(),
    );
  });

  it('checks usage before enabling deletion and requires explicit discard consent for saved games', async () => {
    let finish!: (response: Response) => void;
    let previewCount = 0;
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (path.endsWith('/usage')) {
        previewCount++;
        if (previewCount === 1)
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        return json(usage([unfinished('hidden-one')]));
      }
      if (init?.method === 'DELETE')
        return json({ deleted: true, id: model.id, discarded_game_ids: ['hidden-one'] });
      return json([]);
    });
    vi.stubGlobal('fetch', fetcher);
    const callbacks = show();
    let dialog = openDelete();
    expect(dialog.getByRole('button', { name: 'Delete' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Keep it' })).toBeEnabled();
    await act(async () => finish(await json(usage([unfinished('hidden-one')]))));
    expect(dialog.getByText('1 unfinished game')).toBeInTheDocument();
    expect(dialog.getByText(/no longer open in your browser/)).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Delete' })).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: 'Keep it' }));
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    dialog = openDelete();
    const consent = await dialog.findByRole('checkbox', {
      name: 'Discard unfinished games and delete model',
    });
    fireEvent.click(consent);
    fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(callbacks.onGamesDeleted).toHaveBeenCalledWith(['hidden-one']));
    expect(callbacks.onModelsChanged).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith(
      '/api/models/raven-model?discard_unfinished=true&usage_token=preview-one',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      '1 unfinished game removed without results',
    );
  });

  it('blocks active tournaments even when unfinished games could be discarded', async () => {
    const preview = usage([unfinished('hidden-one')]);
    preview.running_tournaments = [
      { id: 'tournament', name: 'Raven challenge', status: 'running' },
    ];
    const fetcher = vi.fn((path: string, _init?: RequestInit) =>
      json(path.endsWith('/usage') ? preview : []),
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    const dialog = openDelete();
    await dialog.findByText('Raven challenge');
    expect(dialog.getByRole('button', { name: 'Delete' })).toBeDisabled();
    expect(dialog.getByRole('checkbox')).toBeDisabled();
    expect(dialog.getByText(/Stop these tournaments in Tournaments/)).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Keep it' }));
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
  });

  it('requires a new preview and consent when the referenced games change', async () => {
    let previewCount = 0;
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (path.endsWith('/usage')) {
        previewCount++;
        return json(
          previewCount === 1
            ? usage([unfinished('old')])
            : usage([unfinished('old'), unfinished('new')], 'preview-two'),
        );
      }
      if (init?.method === 'DELETE')
        return json(
          { detail: 'The unfinished games changed. Review them before deleting.' },
          false,
        );
      return json([]);
    });
    vi.stubGlobal('fetch', fetcher);
    const callbacks = show();
    const dialog = openDelete();
    const consent = await dialog.findByRole('checkbox', {
      name: 'Discard unfinished games and delete model',
    });
    fireEvent.click(consent);
    fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await dialog.findByText('2 unfinished games');
    expect(dialog.getByRole('checkbox')).not.toBeChecked();
    expect(dialog.getByRole('button', { name: 'Delete' })).toBeDisabled();
    expect(dialog.getByRole('alert')).toHaveTextContent('Review them');
    expect(callbacks.onGamesDeleted).not.toHaveBeenCalled();
    expect(callbacks.onModelsChanged).not.toHaveBeenCalled();
  });

  it('keeps deletion disabled when usage cannot be checked and permits a retry', async () => {
    let previewCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (!path.endsWith('/usage')) return json([]);
        previewCount++;
        return previewCount === 1
          ? json({ detail: 'Connection unavailable' }, false)
          : json(usage());
      }),
    );
    show();
    const dialog = openDelete();
    await dialog.findByRole('alert');
    expect(dialog.getByRole('button', { name: 'Delete' })).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Delete' })).toBeEnabled());
    expect(dialog.queryByRole('alert')).not.toBeInTheDocument();
  });
});
