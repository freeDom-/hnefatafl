import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import NewGame from './NewGame';
import type { NewGameProps } from './NewGameForm';

/**
 * The deferred loader is replaced here so the shell contract can be exercised
 * deterministically. `deferredFeature.test.tsx` covers the real loader and the
 * production browser suites cover the real dynamic import.
 */
const deferred = vi.hoisted(() => ({ state: 'loading' as 'loading' | 'failed' | 'ready' }));
vi.mock('./deferredFeature', () => ({
  deferredFeature: () =>
    function DeferredForm() {
      if (deferred.state === 'failed') return <p role="alert">Import failed</p>;
      if (deferred.state === 'ready') return <p>Form ready</p>;
      return <p role="status">Loading…</p>;
    },
}));

const props = (overrides: Partial<NewGameProps> = {}): NewGameProps => ({
  rulesets: [],
  profiles: [],
  onClose: vi.fn(),
  onCreated: vi.fn(),
  ...overrides,
});

beforeEach(() => {
  deferred.state = 'loading';
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('owns the shared modal shell and shows loading feedback inside it', () => {
  render(<NewGame {...props()} />);
  const dialog = screen.getByRole('dialog', { name: 'Begin a new saga' });
  expect(dialog).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  expect(screen.getByRole('button', { name: 'Close dialog' })).toBeVisible();
});

it('keeps the shell, close action and import-failure feedback when loading fails', () => {
  deferred.state = 'failed';
  render(<NewGame {...props()} />);
  const dialog = screen.getByRole('dialog', { name: 'Begin a new saga' });
  expect(dialog).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('Import failed');
  expect(screen.getByRole('button', { name: 'Close dialog' })).toBeVisible();
});

it('closes through Escape while the form is still loading', () => {
  const onClose = vi.fn();
  render(<NewGame {...props({ onClose })} />);
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(onClose).toHaveBeenCalledOnce();
});

it('closes through the shared close button while loading', () => {
  const onClose = vi.fn();
  render(<NewGame {...props({ onClose })} />);
  fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  expect(onClose).toHaveBeenCalledOnce();
});

it('renders the deferred form inside the same shell once it resolves', () => {
  deferred.state = 'ready';
  render(<NewGame {...props()} />);
  expect(screen.getByRole('dialog', { name: 'Begin a new saga' })).toBeVisible();
  expect(screen.getByText('Form ready')).toBeVisible();
});

it('restores focus to the opener after the dialog unmounts', () => {
  const opener = document.createElement('button');
  opener.textContent = 'Open';
  document.body.append(opener);
  opener.focus();
  const view = render(<NewGame {...props()} />);
  expect(screen.getByRole('dialog')).toBeVisible();
  view.unmount();
  expect(document.activeElement).toBe(opener);
  opener.remove();
});
