import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Training from './Training';
import { sortRulesets } from './sortRulesets';
import { suggestVikingName } from './vikingNames';
import type { Model, Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'copenhagen', name: 'Copenhagen', size: 11, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
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
});
