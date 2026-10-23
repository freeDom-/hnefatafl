import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingHistoryChart from './TrainingHistoryChart';
import { setLanguage } from './i18n';
import type { Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'copenhagen', name: 'Copenhagen', size: 11, description: '', rules_markdown: '' },
];
beforeEach(() => {
  setLanguage('en', false);
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: async () => [] })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

it('selects board-specific real training plans and preserves identity/retention when switching presets', () => {
  render(<Training rulesets={rules} models={[]} onModelsChanged={vi.fn()} onError={vi.fn()} />);
  const name = screen.getByRole('textbox', { name: 'Model name' });
  fireEvent.change(name, { target: { value: 'Eir of the Fjord' } });
  expect(screen.getByRole('combobox', { name: 'Training preset' })).toHaveValue('recommended');
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(30);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Save every new best model' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Battlefield' }), {
    target: { value: 'copenhagen' },
  });
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(40);
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  expect(screen.getByRole('spinbutton', { name: 'Network channels' })).toHaveValue(64);
  expect(screen.getByRole('spinbutton', { name: 'Residual blocks' })).toHaveValue(4);
  fireEvent.change(screen.getByRole('combobox', { name: 'Training preset' }), {
    target: { value: 'smoke' },
  });
  expect(screen.getByRole('spinbutton', { name: 'Network channels' })).toHaveValue(4);
  expect(screen.getByRole('spinbutton', { name: 'Search time limit (seconds)' })).toHaveValue(0.1);
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(1);
  expect(screen.getByRole('checkbox', { name: 'Save every new best model' })).toBeChecked();
  expect(name).toHaveValue('Eir of the Fjord');
  act(() => setLanguage('de', false));
  expect(screen.getByRole('combobox', { name: 'Trainingsvorlage' })).toHaveValue('smoke');
  expect(screen.getByRole('checkbox', { name: 'Jedes neue beste Modell speichern' })).toBeChecked();
  expect(screen.getByRole('textbox', { name: 'Modellname' })).toHaveValue('Eir of the Fjord');
});

it('adds real completed points to loss/evaluation graphs and leaves missing evaluation explicit', async () => {
  const view = render(<TrainingHistoryChart history={[]} />);
  expect(
    screen.getByText('Graphs appear after the first completed training iteration.'),
  ).toBeVisible();
  const first = { iteration: 1, policy_loss: 1.2, value_loss: 0.4 };
  view.rerender(<TrainingHistoryChart history={[first]} />);
  expect(screen.getAllByRole('img')).toHaveLength(2);
  expect(document.querySelectorAll('svg circle')).toHaveLength(2);
  view.rerender(
    <TrainingHistoryChart
      history={[
        first,
        {
          iteration: 2,
          policy_loss: 0.8,
          value_loss: 0.2,
          evaluation_score: 0.5,
          evaluation_truncated: 1,
        },
      ]}
    />,
  );
  expect(document.querySelectorAll('svg circle')).toHaveLength(5);
  fireEvent.click(screen.getByText('View graph data'));
  const table = await screen.findByRole('table', { name: 'Training history data' });
  const rows = within(table).getAllByRole('row');
  expect(rows).toHaveLength(3);
  expect([...rows[1].children].map((cell) => cell.textContent)).toEqual([
    '1',
    '1.2',
    '0.4',
    '—',
    '—',
  ]);
  expect([...rows[2].children].map((cell) => cell.textContent)).toEqual([
    '2',
    '0.8',
    '0.2',
    '0.5',
    '1',
  ]);
});
