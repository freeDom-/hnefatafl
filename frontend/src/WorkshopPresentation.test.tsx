import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import { setLanguage } from './i18n';
beforeEach(() => {
  setLanguage('en', false);
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          path.split('?')[0] === '/api/training/jobs'
            ? [
                {
                  id: 'live-job',
                  name: 'Live strategist',
                  status: 'running',
                  config: { ruleset: 'brandubh' },
                  progress: { iteration: 1 },
                },
              ]
            : path.endsWith('/device')
              ? { device: 'cpu' }
              : [],
      } as Response),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
});
function show() {
  render(
    <Training
      models={[{ id: 'model', name: 'Raven', ruleset: 'brandubh' }]}
      rulesets={[
        { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
      ]}
      onError={vi.fn()}
      onModelsChanged={vi.fn()}
    />,
  );
}
it('uses correct singular job/model counts in the task summary', async () => {
  show();
  const summary = screen.getByLabelText('Workshop summary');
  await waitFor(() => expect(summary).toHaveTextContent('1 active job'));
  expect(summary).not.toHaveTextContent('1 active jobs');
  expect(summary).toHaveTextContent('1 registered model');
  expect(summary).not.toHaveTextContent('1 registered models');
});
it('task navigation opens existing creation, monitoring, model and worker surfaces', async () => {
  show();
  await screen.findByRole('heading', { name: 'Live strategist' });
  const tasks = screen.getByRole('navigation', { name: 'Workshop tasks' });
  fireEvent.click(within(tasks).getByRole('button', { name: 'Monitor jobs' }));
  expect(screen.getByRole('region', { name: 'At the forge' })).toHaveFocus();
  fireEvent.click(within(tasks).getByRole('button', { name: 'Train or tune' }));
  await waitFor(() =>
    expect(document.getElementById('training-creation')).not.toHaveAttribute('hidden'),
  );
  expect(within(tasks).getByRole('link', { name: 'AI comparisons' })).toHaveAttribute(
    'href',
    '/tournaments',
  );
  expect(within(tasks).getByRole('link', { name: 'Model collection' })).toHaveAttribute(
    'href',
    '#model-collection',
  );
  expect(within(tasks).getByRole('link', { name: 'Remote workers' })).toHaveAttribute(
    'href',
    '#workshop-workers',
  );
});
