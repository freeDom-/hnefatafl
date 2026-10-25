import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Guest from './Guest';
import fixture from './guestFixture.testdata.json';
import { setLanguage } from './i18n';
const state = vi.hoisted(() => ({
  raw: null as string | null,
  revision: null as string | null,
  fail: false,
}));
const rpc = vi.hoisted(() => vi.fn());
vi.mock('./guestEngine', () => ({
  GuestEngine: class {
    ready = true;
    notices = '/notices';
    start = async () => {};
    request = rpc;
    dispose = vi.fn();
  },
}));
vi.mock('./guestStore', () => ({
  GuestConflict: class extends Error {},
  clearGuestSlot: vi.fn(async () => {
    state.raw = null;
    return (state.revision = crypto.randomUUID());
  }),
  readGuestSlot: async () => ({ revision: state.revision, raw: state.raw }),
  writeGuestSlot: vi.fn(async (_revision: string | null, record: unknown) => {
    if (state.fail) throw new Error('quota');
    state.raw = record === null ? null : JSON.stringify(record);
    return (state.revision = crypto.randomUUID());
  }),
}));
import { writeGuestSlot } from './guestStore';
beforeEach(() => {
  state.raw = null;
  state.revision = null;
  state.fail = false;
  vi.mocked(writeGuestSlot).mockClear();
  rpc.mockReset();
  setLanguage('en', false);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function () {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function () {
      this.removeAttribute('open');
    },
  });
  rpc.mockImplementation(async (request: { action: string }) =>
    request.action === 'info'
      ? {
          engine_version: 'a'.repeat(64),
          rulesets: [{ id: 'brandubh', name: 'Brandubh', version: '1' }],
        }
      : request.action === 'move'
        ? fixture.moved
        : request.action === 'seek'
          ? fixture.replay
          : fixture.initial,
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});
async function mount() {
  await act(async () => {
    render(<Guest />);
  });
  await screen.findByText('Local engine ready.');
}
it('starts a distinct guest game without any account/API requests', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  await screen.findByRole('grid');
  expect(rpc).toHaveBeenCalledWith({ action: 'new', ruleset: 'brandubh', human_side: 'attackers' });
  expect(fetcher).not.toHaveBeenCalled();
  expect(JSON.parse(state.raw!).moves).toEqual([]);
});
it('requires accessible replacement confirmation and preserves the slot when cancelled', async () => {
  state.raw = JSON.stringify(fixture.moved.record);
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'info'
      ? {
          engine_version: 'a'.repeat(64),
          rulesets: [{ id: 'brandubh', name: 'Brandubh', version: '1' }],
        }
      : r.action === 'restore'
        ? fixture.moved
        : fixture.initial,
  );
  await mount();
  const before = state.raw;
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  expect(await screen.findByRole('dialog', { name: 'Replace device game?' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(state.raw).toBe(before);
  expect(writeGuestSlot).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(0));
});
it('clears only the guest slot after confirmation and keeps appearance preferences', async () => {
  state.raw = JSON.stringify(fixture.initial.record);
  localStorage.setItem('hnefatafl-appearance', 'preserved');
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Clear device game' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm clear' }));
  await waitFor(() => expect(state.raw).toBeNull());
  expect(localStorage.getItem('hnefatafl-appearance')).toBe('preserved');
  localStorage.clear();
});
it('continues in memory and reports persistence failure after an accepted move', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  await screen.findByRole('grid');
  state.fail = true;
  fireEvent.click(screen.getByRole('gridcell', { name: 'd7, attacker' }));
  fireEvent.click(screen.getByRole('gridcell', { name: /^b7, empty/ }));
  await screen.findByText(/This change could not be saved/);
  expect(screen.getByRole('gridcell', { name: 'b7, attacker' })).toBeVisible();
  expect(JSON.parse(state.raw!).moves).toHaveLength(0);
});
it('rejects corrupt saves without deleting or silently replacing them', async () => {
  state.raw = '{bad';
  await mount();
  await screen.findByRole('alert');
  expect(state.raw).toBe('{bad');
  expect(writeGuestSlot).not.toHaveBeenCalled();
  expect(screen.queryByRole('grid')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  expect(await screen.findByRole('dialog')).toBeVisible();
});
it('historical guest positions are read-only and never submitted as live moves', async () => {
  state.raw = JSON.stringify(fixture.computer.record);
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'info'
      ? {
          engine_version: 'a'.repeat(64),
          rulesets: [{ id: 'brandubh', name: 'Brandubh', version: '1' }],
        }
      : r.action === 'restore'
        ? fixture.computer
        : fixture.replay,
  );
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Previous move' }));
  await screen.findByText('Guest replay · read only');
  const count = rpc.mock.calls.length;
  fireEvent.click(screen.getByRole('gridcell', { name: 'd7, attacker' }));
  expect(rpc.mock.calls).toHaveLength(count);
});
it('supports the German guest controls', async () => {
  setLanguage('de', false);
  await act(async () => {
    render(<Guest />);
  });
  await screen.findByText('Lokale Spiel-Engine bereit.');
  expect(screen.getByRole('heading', { name: 'Gastspiel · nur in diesem Browser' })).toBeVisible();
});
