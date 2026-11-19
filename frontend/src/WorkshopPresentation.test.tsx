import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import Tournaments from './Tournaments';
import { setLanguage } from './i18n';
const session = vi.hoisted(() => ({ permissions: ['training', 'tournaments'] }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: { permissions: session.permissions } }) }));
beforeEach(() => {
  session.permissions = ['training', 'tournaments'];
  history.replaceState(null, '', '/training');
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

it.each([
  ['Model collection', 'model-collection', /Model collection \(/],
  ['Remote workers', 'workshop-workers', /^Remote workers \(/],
])(
  'reopens and focuses collapsed %s on repeated links with an unchanged hash',
  async (label, id, toggleName) => {
    show();
    await screen.findByRole('heading', { name: 'Live strategist' });
    const tasks = screen.getByRole('navigation', { name: 'Workshop tasks' });
    const link = within(tasks).getByRole('link', { name: label });
    const section = document.getElementById(id)!;
    for (let click = 0; click < 2; click++) {
      fireEvent.click(link);
      expect(location.hash).toBe(`#${id}`);
      await waitFor(() => expect(section).toHaveFocus());
      expect(section).not.toHaveAttribute('hidden');
      fireEvent.click(screen.getByRole('button', { name: toggleName }));
      expect(section).toHaveAttribute('hidden');
    }
  },
);

it.each([
  [['training'], false, true],
  [['tournaments'], true, false],
  [['training', 'tournaments'], true, true],
])('gates task and return links for permissions %j', async (permissions, comparisons, training) => {
  session.permissions = permissions;
  show();
  await screen.findByRole('heading', { name: 'Live strategist' });
  expect(!!screen.queryByRole('link', { name: 'AI comparisons' })).toBe(comparisons);
  cleanup();
  render(<Tournaments rulesets={[]} profiles={[]} models={[]} onError={vi.fn()} />);
  await screen.findByRole('button', { name: 'New tournament' });
  expect(!!screen.queryByRole('link', { name: 'Return to the AI workshop' })).toBe(training);
});
