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
      : request.action === 'computer'
        ? fixture.computer
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
it('has no self-link or account-game actions and leaves nothing after Clear device game', async () => {
  await mount();
  expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login');
  expect(
    screen.queryByRole('link', { name: /Guest play|Account single-player|Online multiplayer/ }),
  ).toBeNull();
  expect(screen.queryByText(/Only the current or last guest game is kept/)).toBeNull();
  expect(screen.queryByText(/Browser storage can be cleared or evicted/)).toBeNull();
  expect(screen.queryByText(/Language and appearance are separate preferences/)).toBeNull();
  const clear = screen.getByRole('button', { name: 'Clear device game' });
  expect(clear.nextElementSibling).toBeNull();
  expect(screen.getByRole('link', { name: 'Runtime licenses and source' })).toBeVisible();
});
it('requires accessible replacement confirmation and preserves the slot when cancelled', async () => {
  state.raw = JSON.stringify(fixture.computer.record);
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'info'
      ? {
          engine_version: 'a'.repeat(64),
          rulesets: [{ id: 'brandubh', name: 'Brandubh', version: '1' }],
        }
      : r.action === 'restore'
        ? fixture.computer
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
  await waitFor(() =>
    expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(1),
  );
  expect(screen.getByRole('gridcell', { name: 'c7, defender' })).toBeVisible();
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

it('reflects the validated restored side in the guest setup controls', async () => {
  const restored = {
    ...fixture.initial,
    record: { ...fixture.initial.record, human_side: 'defenders' },
  };
  state.raw = JSON.stringify(restored.record);
  const prior = rpc.getMockImplementation()!;
  rpc.mockImplementation(async (request: { action: string }) =>
    request.action === 'restore' ? restored : prior(request),
  );
  await mount();
  await screen.findByRole('grid');
  expect(screen.getByLabelText('Your side')).toHaveValue('defenders');
  expect(screen.getByLabelText('Ruleset')).toHaveValue('brandubh');
});

it('automatically plays exactly one reply after an accepted human move', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  await screen.findByRole('grid');
  fireEvent.click(screen.getByRole('gridcell', { name: 'd7, attacker' }));
  fireEvent.click(screen.getByRole('gridcell', { name: /^b7, empty/ }));
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(2));
  expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Play computer move' })).toBeNull();
});
it.each(['new', 'restore'])('automatically plays an AI-first %s game', async (action) => {
  const aiFirst = {
    ...fixture.initial,
    record: { ...fixture.initial.record, human_side: 'defenders' },
  };
  const humanNext = {
    ...fixture.moved,
    record: { ...fixture.moved.record, human_side: 'defenders' },
  };
  const original = rpc.getMockImplementation()!;
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'computer' ? humanNext : r.action === action ? aiFirst : original(r),
  );
  if (action === 'restore') state.raw = JSON.stringify(aiFirst.record);
  await mount();
  if (action === 'new') {
    fireEvent.change(screen.getByLabelText('Your side'), { target: { value: 'defenders' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  }
  await waitFor(() =>
    expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(1),
  );
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(1));
  expect(screen.getByLabelText('Your side')).toHaveValue('defenders');
});
it('restores a pending reply, exposes failure and retries only on explicit action', async () => {
  state.raw = JSON.stringify(fixture.moved.record);
  const original = rpc.getMockImplementation()!;
  let fail = true;
  rpc.mockImplementation(async (r: { action: string }) => {
    if (r.action === 'restore') return fixture.moved;
    if (r.action === 'computer') {
      if (fail) throw Error('Search failed');
      return fixture.computer;
    }
    return original(r);
  });
  await mount();
  await screen.findByText('Search failed');
  expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(1);
  await act(async () => {
    await Promise.resolve();
  });
  expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(1);
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry computer move' }));
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(2));
  expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(2);
});
it('never computes a terminal guest reply even when the human side differs', async () => {
  state.raw = JSON.stringify(fixture.moved.record);
  const terminal = {
    ...fixture.moved,
    live_winner: 'draw',
    position: { ...fixture.moved.position, winner: 'draw' },
  };
  const original = rpc.getMockImplementation()!;
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'restore' ? terminal : original(r),
  );
  await mount();
  await screen.findByText('Draw');
  expect(rpc.mock.calls.some(([r]) => r.action === 'computer')).toBe(false);
});
it('discards a cancelled late reply after restoring and replacing the game', async () => {
  state.raw = JSON.stringify(fixture.moved.record);
  let resolve!: (value: unknown) => void;
  const original = rpc.getMockImplementation()!;
  let replies = 0;
  rpc.mockImplementation(async (r: { action: string }) => {
    if (r.action === 'restore') return fixture.moved;
    if (r.action === 'computer' && replies++ === 0)
      return new Promise((r) => {
        resolve = r;
      });
    return original(r);
  });
  await act(async () => {
    render(<Guest />);
  });
  await screen.findByText('Computer is thinking…');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel computation' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Retry loading engine' }));
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(2));
  fireEvent.click(screen.getByRole('button', { name: 'Start guest game' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  await waitFor(() => expect(JSON.parse(state.raw!).moves).toHaveLength(0));
  const replaced = state.raw;
  await act(async () => resolve(fixture.computer));
  expect(state.raw).toBe(replaced);
  expect(rpc.mock.calls.filter(([r]) => r.action === 'computer')).toHaveLength(2);
});
it('ignores pending AI results after navigation unmounts the guest surface', async () => {
  state.raw = JSON.stringify(fixture.moved.record);
  let resolve!: (value: unknown) => void;
  const original = rpc.getMockImplementation()!;
  rpc.mockImplementation(async (r: { action: string }) =>
    r.action === 'restore'
      ? fixture.moved
      : r.action === 'computer'
        ? new Promise((r) => {
            resolve = r;
          })
        : original(r),
  );
  const view = render(<Guest />);
  await screen.findByText('Computer is thinking…');
  const saved = state.raw;
  view.unmount();
  await act(async () => resolve(fixture.computer));
  expect(state.raw).toBe(saved);
  expect(writeGuestSlot).not.toHaveBeenCalled();
});
