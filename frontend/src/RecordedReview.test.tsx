import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RecordedReview from './RecordedReview';
import fixture from './onlineFixture.testdata.json';
import { setLanguage } from './i18n';

const respond = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status === 200, status, json: async () => body } as Response);
const fetcher = vi.fn<(url: string, opts?: RequestInit) => Promise<Response>>();

beforeEach(() => {
  history.replaceState(null, '', '/archive');
  fetcher.mockReset();
  vi.stubGlobal('fetch', fetcher);
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('restores the full read-only online review with completed-game analytics', async () => {
  fetcher.mockImplementation(() => respond(fixture.replay));
  render(<RecordedReview id={fixture.replay.game.id} category="online" accountId="archer" />);
  await screen.findByRole('grid');
  expect(screen.getByRole('heading', { name: 'The saga so far' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Winning chances' })).toBeVisible();
  // Completed online reviews reuse the analytics experience instead of a denial.
  expect(screen.queryByText(/Winning chances are unavailable/)).toBeNull();
  expect(screen.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  expect(screen.queryByText('Analysis is unavailable for recorded online games.')).toBeNull();
  expect(screen.getByText('Single game')).toBeVisible();
  expect(screen.getByText('Archer')).toBeVisible();
  expect(screen.getByText('Keeper')).toBeVisible();
  expect(screen.getByText('Read-only replay')).toBeVisible();
  // Replay navigation reaches every recorded move and the starting position.
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Previous move' }));
  expect(screen.getByText('Move 0 of 1')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Replay to move 1' }));
  expect(screen.getByText('Move 1 of 1')).toBeVisible();
  // No mutation controls exist on the review.
  expect(screen.queryByRole('button', { name: /concede|seek|undo|restart/i })).toBeNull();
  expect(fetcher.mock.calls.every(([, opts]) => !opts?.method || opts.method === 'GET')).toBe(true);
});

it('plays recorded moves locally without authoritative writes or analysis', async () => {
  fetcher.mockImplementation(() => respond(fixture.replay));
  render(<RecordedReview id={fixture.replay.game.id} category="online" accountId="archer" />);
  await screen.findByRole('grid');
  const before = fetcher.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  // Stopping retains the position; the one-second cadence is covered by the
  // deterministic ReplayControls tests with fake timers.
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(screen.getByText('Move 0 of 1')).toBeVisible();
  const calls = fetcher.mock.calls.slice(before);
  expect(calls.every(([, opts]) => !opts?.method || opts.method === 'GET')).toBe(true);
});

it('keeps authorized single-player analytics available on the review', async () => {
  const account = {
    game: {
      id: 'account-1',
      category: 'account-single-player',
      ruleset: 'brandubh',
      mode: 'human-ai',
      status: 'completed',
      winner: 'draw',
      plies: 1,
      players: {
        attackers: { name: 'Odin', kind: 'human' },
        defenders: { name: 'Jarl', kind: 'ai' },
      },
    },
    game_id: 'account-1',
    version: 4,
    validated: true,
    cursor: 1,
    states: fixture.replay.states,
    moves: fixture.replay.game.moves,
  };
  fetcher.mockImplementation((url) => {
    if (url.includes('/ai/profiles') || url.includes('/models')) return respond([]);
    if (url.includes('/analytics'))
      return respond({
        advisor: { name: 'Advisor', profile_id: 'baseline', config: { algorithm: 'baseline' } },
        time_limit: 5,
        show_win_chances: true,
        revision: 0,
      });
    if (url.includes('/analysis'))
      return respond({
        cursor: 0,
        ply: 0,
        side_to_move: 'attackers',
        position_key: 'position',
        created_at: null,
        evaluations: [],
        hint: null,
        status: 'complete',
      });
    return respond(account);
  });
  render(
    <RecordedReview id="account-1" category="account-single-player" accountId="odin" personal />,
  );
  await screen.findByRole('grid');
  expect(screen.getByRole('heading', { name: 'The saga so far' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Winning chances' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  expect(screen.getByText('Human vs AI')).toBeVisible();
  expect(screen.getByText('Recorded result').closest('.detail-row')).toHaveTextContent('Draw');
  // History review analyzes any recorded cursor read-only.
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([url, opts]) => url.includes('/analysis') && opts?.method === 'POST',
      ),
    ).toBe(true),
  );
  const analysisCall = fetcher.mock.calls.find(
    ([url, opts]) => url.includes('/analysis') && opts?.method === 'POST',
  );
  expect(JSON.parse(String(analysisCall![1]?.body))).toMatchObject({ historical: true });
});

it('does not request analysis while playing a recorded account game', async () => {
  const account = {
    game: {
      id: 'account-play',
      category: 'account-single-player',
      ruleset: 'brandubh',
      mode: 'human-ai',
      status: 'completed',
      winner: 'draw',
      plies: 1,
      players: {
        attackers: { name: 'Odin', kind: 'human' },
        defenders: { name: 'Jarl', kind: 'ai' },
      },
    },
    game_id: 'account-play',
    version: 4,
    validated: true,
    cursor: 1,
    states: fixture.replay.states,
    moves: fixture.replay.game.moves,
  };
  const analysisReport = {
    cursor: 0,
    ply: 0,
    side_to_move: 'attackers',
    position_key: 'key',
    created_at: null,
    evaluations: [],
    hint: null,
    status: 'complete',
  };
  fetcher.mockImplementation((url) => {
    if (url.includes('/ai/profiles') || url.includes('/models')) return respond([]);
    if (url.includes('/analytics'))
      return respond({
        advisor: { name: 'Advisor', profile_id: 'baseline', config: { algorithm: 'baseline' } },
        time_limit: 5,
        show_win_chances: true,
        revision: 0,
      });
    if (url.includes('/analysis')) return respond(analysisReport);
    return respond(account);
  });
  render(
    <RecordedReview id="account-play" category="account-single-player" accountId="odin" personal />,
  );
  await screen.findByRole('grid');
  const analysisCalls = () =>
    fetcher.mock.calls.filter(([url, opts]) => url.includes('/analysis') && opts?.method === 'POST')
      .length;
  await waitFor(() => expect(analysisCalls()).toBeGreaterThan(0));
  const before = fetcher.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  await new Promise((resolve) => setTimeout(resolve, 300));
  const during = fetcher.mock.calls.slice(before);
  expect(during.some(([url, opts]) => url.includes('/analysis') && opts?.method === 'POST')).toBe(
    false,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
});

it('keeps the captured king on the board in a recorded review', async () => {
  const initial = fixture.replay.states[0];
  const capturedBoard = initial.board.map((piece) => (piece === 'K' ? '.' : piece));
  const account = {
    game: {
      id: 'king-capture',
      category: 'account-single-player',
      ruleset: 'brandubh',
      mode: 'human-ai',
      status: 'completed',
      winner: 'attackers',
      plies: 1,
      players: {
        attackers: { name: 'Odin', kind: 'human' },
        defenders: { name: 'Jarl', kind: 'ai' },
      },
    },
    game_id: 'king-capture',
    version: 1,
    validated: true,
    cursor: 1,
    states: [
      initial,
      {
        ...initial,
        board: capturedBoard,
        ply: 1,
        turn: 'attackers',
        winner: 'attackers',
        reason: 'king_captured',
      },
    ],
    moves: [{ source: 0, target: 1 }],
  };
  fetcher.mockImplementation((url) => {
    if (url.includes('/ai/profiles') || url.includes('/models')) return respond([]);
    if (url.includes('/analytics'))
      return respond({ advisor: null, time_limit: 5, show_win_chances: false, revision: 0 });
    return respond(account);
  });
  render(
    <RecordedReview id="king-capture" category="account-single-player" accountId="odin" personal />,
  );
  await screen.findByRole('grid');
  // The final board omits K, but the review retains the captured king marker.
  expect(screen.getByRole('gridcell', { name: /king/ })).toBeVisible();
});
