import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Training from './Training';
import { setLanguage } from './i18n';
import type { Ruleset } from './types';

/**
 * A rejected training field must be actionable: the exact field is named, the
 * matching input is marked, and every entered value survives so the user can fix
 * one field instead of rebuilding the form.
 */

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

function rejection(fields: string[], detail: string) {
  return json({ detail, code: 'unsupported_training_field', fields }, false);
}

function harness(responses: Promise<Response>[]) {
  const posts: Record<string, unknown>[] = [];
  const error = vi.fn();
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    const base = path.split('?')[0];
    if (base === '/api/training/jobs') {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return responses[Math.min(posts.length - 1, responses.length - 1)];
      }
      return json([]);
    }
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/device') return json({});
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  return { posts, error };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

it('names and highlights a rejected field, keeps the values and recovers', async () => {
  setLanguage('en', false);
  const created = json({ id: 'new-job', status: 'running', config: {} });
  const { posts, error } = harness([
    rejection(['channels'], 'Unsupported training configuration field: channels'),
    created,
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Training iterations' }), {
    target: { value: '9' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Training was not started: one configuration field was rejected.',
  );
  // The technical name is always shown, even when a label exists.
  expect(alert).toHaveTextContent('Network channels');
  expect(alert).toHaveTextContent('channels');
  const channels = screen.getByRole('spinbutton', { name: 'Network channels' });
  await waitFor(() => expect(channels).toHaveAttribute('aria-invalid', 'true'));
  expect(channels).toHaveClass('field-invalid');
  // Every entered value survives, and the failure is not a global notice.
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(9);
  expect(error).not.toHaveBeenCalled();

  // Correcting the field clears its rejection; the next submit succeeds.
  fireEvent.change(channels, { target: { value: '8' } });
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(channels).not.toHaveAttribute('aria-invalid'));
  expect(channels).not.toHaveClass('field-invalid');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].channels).toBe(8);
});

it('reports every rejected field and explains the ones without an input', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['output_dir', 'registry_dir'],
      'Unsupported training configuration fields: output_dir, registry_dir',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Training was not started: 2 configuration fields were rejected.',
  );
  expect(alert).toHaveTextContent('output_dir');
  expect(alert).toHaveTextContent('registry_dir');
  expect(alert).toHaveTextContent('The server assigns each training job its own output directory.');
  expect(alert).toHaveTextContent('The server keeps trained models in its own model directory.');
  // No input exists for them, so nothing is marked invalid.
  expect(document.querySelector('.field-invalid')).toBeNull();
});

it('explains an unknown rejected field instead of showing only its name', async () => {
  setLanguage('en', false);
  harness([rejection(['future_field'], 'Unsupported training configuration field: future_field')]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('future_field');
  expect(alert).toHaveTextContent(
    'This server does not accept this training field. Remove it from the configuration and try again.',
  );
});

it('marks the nested tuning field the server named', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['training.batch_size'],
      'Unsupported training configuration field: training.batch_size',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Batch size');
  const batch = screen.getByRole('spinbutton', { name: 'Batch size' });
  await waitFor(() => expect(batch).toHaveAttribute('aria-invalid', 'true'));
});

it('localizes the rejected-field report in German', async () => {
  setLanguage('de', false);
  harness([rejection(['output_dir'], 'Unsupported training configuration field: output_dir')]);
  await screen.findByRole('button', { name: 'Training starten' });
  fireEvent.click(screen.getByRole('button', { name: 'Training starten' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Das Training wurde nicht gestartet: Ein Konfigurationsfeld wurde abgelehnt.',
  );
  expect(alert).toHaveTextContent('output_dir');
  expect(alert).toHaveTextContent(
    'Der Server weist jedem Trainingsauftrag ein eigenes Ausgabeverzeichnis zu.',
  );
});
