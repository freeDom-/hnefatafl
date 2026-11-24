import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PlayerTournaments from './PlayerTournaments';
import { api } from './api';
import { notify } from './notifications';
import { setLanguage } from './i18n';
import type {
  PlayerTournamentBracket,
  PlayerTournamentDetail,
  TournamentMatch,
  TournamentPlayer,
} from './playerTournamentTypes';
const identity = vi.hoisted(() => ({
  id: 'archer',
  permissions: ['play'] as string[],
  email_verified: true,
}));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
vi.mock('./api', () => ({ api: vi.fn(), errorMessage: (error: Error) => error.message }));
vi.mock('./notifications', () => ({ notify: { success: vi.fn(), error: vi.fn() } }));
const players: TournamentPlayer[] = ['Archer', 'Keeper', 'Skald', 'Jarl'].map((name) => ({
  account_id: name.toLowerCase(),
  name,
  portrait_id: 'shieldbearer',
}));
const detailFixture: PlayerTournamentDetail = {
  id: 'cup-1',
  name: 'Fjord cup',
  ruleset: 'brandubh',
  format: 'double_elimination',
  status: 'running',
  review_state: 'ok',
  capacity: 4,
  match_legs: 1,
  time_per_move: 3600,
  bracket_size: 4,
  entrant_count: 4,
  version: 7,
  created_at: '2035-01-01T00:00:00Z',
  started_at: '2035-02-01T00:00:00Z',
  completed_at: null,
  scheduled_start_at: null,
  official_winner: null,
  entrants: players.map((player, index) => ({
    ...player,
    seed: index + 1,
    registered_at: '2035-01-01T00:00:00Z',
    losses: 0,
    eliminated: false,
  })),
  match_counts: { ready: 1, complete: 1, pending: 4 },
};
function match(
  id: string,
  bracket: TournamentMatch['bracket'],
  round: number,
  source_a: string,
  source_b: string,
): TournamentMatch {
  return {
    id,
    bracket,
    round,
    slot: 0,
    status: 'pending',
    source_a,
    source_b,
    decided_by: null,
    participants: { a: null, b: null },
    winner: null,
    loser: null,
    legs: [],
    created_at: '2035-01-01T00:00:00Z',
    settled_at: null,
  };
}
const upper1: TournamentMatch = {
  ...match('u1', 'upper', 1, 'seed:1', 'seed:4'),
  status: 'complete',
  participants: { a: players[0], b: players[3] },
  winner: players[0],
  loser: players[3],
  decided_by: 'play',
};
const upper2: TournamentMatch = {
  ...match('u2', 'upper', 1, 'seed:2', 'seed:3'),
  slot: 1,
  status: 'ready',
  participants: { a: players[1], b: players[2] },
  legs: [
    {
      leg: 1,
      game_id: 'online-game-1',
      game_status: 'active',
      outcome: null,
      result_revision: null,
      settled_at: null,
    },
  ],
};
const bracketFixture: PlayerTournamentBracket = {
  tournament_id: 'cup-1',
  version: 7,
  status: 'running',
  review_state: 'ok',
  bracket_size: 4,
  upper: [
    { round: 1, matches: [upper1, upper2] },
    { round: 2, matches: [match('u3', 'upper', 2, 'winner:u1', 'winner:u2')] },
  ],
  lower: [
    { round: 1, matches: [match('l1', 'lower', 1, 'loser:u1', 'loser:u2')] },
    { round: 2, matches: [match('l2', 'lower', 2, 'winner:l1', 'loser:u3')] },
  ],
  final: match('f1', 'grand_final', 1, 'winner:u3', 'winner:l2'),
  reset: null,
};
let tournament: PlayerTournamentDetail;
let bracket: PlayerTournamentBracket;
beforeEach(() => {
  vi.clearAllMocks();
  identity.id = 'archer';
  identity.permissions = ['play'];
  identity.email_verified = true;
  setLanguage('en');
  history.replaceState(null, '', '/player-tournaments');
  tournament = structuredClone(detailFixture);
  bracket = structuredClone(bracketFixture);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  vi.mocked(api).mockImplementation(async (path, _data, method) => {
    if (path.startsWith('/player-tournaments?'))
      return { items: [tournament], next_cursor: null, limit: 20 } as never;
    if (path === '/player-tournaments/cup-1') return tournament as never;
    if (path.endsWith('/bracket')) return bracket as never;
    if (method === 'POST') return tournament as never;
    throw new Error(`Unexpected API ${path}`);
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
it('renders server-provided upper/lower/final edges and a keyboard-accessible match-list alternative', async () => {
  render(<PlayerTournaments tournamentId="cup-1" />);
  await screen.findByRole('heading', { name: 'Fjord cup' });
  const graph = screen.getByRole('region', { name: 'Double-elimination bracket' });
  expect(graph).toHaveAttribute('tabindex', '0');
  expect(within(graph).getByRole('region', { name: 'Upper bracket' })).toBeVisible();
  expect(within(graph).getByRole('region', { name: 'Lower bracket' })).toBeVisible();
  expect(graph.querySelectorAll('svg path')).toHaveLength(8);
  expect(graph.querySelectorAll('.player-bracket-loser-path')).toHaveLength(3);
  expect(screen.getByRole('link', { name: 'Game 1' })).toHaveAttribute(
    'href',
    '/online/games/online-game-1',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Accessible match list' }));
  expect(
    screen.queryByRole('region', { name: 'Double-elimination bracket' }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole('article')).toHaveLength(6);
  expect(screen.getByRole('button', { name: 'Accessible match list' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(api).not.toHaveBeenCalledWith(
    expect.stringContaining('/settle'),
    expect.anything(),
    expect.anything(),
    expect.anything(),
  );
});
it.each([
  ['legacy', 5, 'per_move', 90123, 0, 'Legacy: 5 games per match', 'Time per move: 1d 01h 02m 03s'],
  ['single', 1, 'per_move', 65, 0, 'Single game', 'Time per move: 1m 05s'],
  [
    'sequential',
    2,
    'total',
    90061,
    30,
    'Two-game match — sequential',
    'Total time per player: 1d 01h 01m 01s · Increment: 30 seconds',
  ],
  [
    'simultaneous',
    2,
    'total',
    3600,
    0,
    'Two-game match — simultaneous',
    'Total time per player: 1h 00m 00s · Increment: 0 seconds',
  ],
] as const)(
  'summarizes %s configuration without losing historical duration precision',
  async (mode, legs, control, seconds, increment, modeLabel, clockLabel) => {
    tournament.match_mode = mode;
    tournament.match_legs = legs;
    tournament.time_control = control;
    tournament.time_per_move = seconds;
    tournament.increment_seconds = increment;
    render(<PlayerTournaments tournamentId="cup-1" />);
    expect(await screen.findByText(modeLabel, { exact: true })).toBeVisible();
    expect(screen.getByText(clockLabel, { exact: true })).toBeVisible();
  },
);
it('uses authoritative champion, elimination and reset state rather than deriving winners from legs', async () => {
  tournament.status = 'complete';
  tournament.official_winner = players[1];
  tournament.entrants[0].losses = 2;
  tournament.entrants[0].eliminated = true;
  bracket.status = 'complete';
  bracket.reset = {
    ...match('r1', 'reset', 1, 'winner:f1', 'loser:f1'),
    status: 'complete',
    winner: players[1],
    loser: players[0],
    participants: { a: players[0], b: players[1] },
  };
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByRole('heading', { name: 'Keeper' })).toBeVisible();
  expect(screen.getByText('Match losses: 2', { exact: false })).toBeVisible();
  expect(screen.getByText('Eliminated')).toBeVisible();
  expect(screen.getByRole('region', { name: 'Finals' })).toHaveTextContent('Bracket reset');
});
it('keeps all administrative actions hidden from ordinary players', async () => {
  render(<PlayerTournaments tournamentId="cup-1" />);
  await screen.findByRole('heading', { name: 'Fjord cup' });
  for (const name of [
    'Create player tournament',
    'Start tournament',
    'Cancel tournament',
    'Rebuild bracket',
    'Override match result',
  ])
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
});
it('registers the current account through the existing server API only after confirmation', async () => {
  tournament.status = 'registration';
  tournament.entrants = [];
  tournament.entrant_count = 0;
  const user = userEvent.setup();
  render(<PlayerTournaments tournamentId="cup-1" />);
  await user.click(await screen.findByRole('button', { name: 'Register for tournament' }));
  expect(api).not.toHaveBeenCalledWith(
    '/player-tournaments/cup-1/register',
    expect.anything(),
    expect.anything(),
    expect.anything(),
  );
  await user.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Register for tournament' }),
  );
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments/cup-1/register',
      {},
      'POST',
      expect.any(AbortSignal),
    ),
  );
  expect(notify.success).toHaveBeenCalledWith('Tournament updated.');
});
it('offers withdrawal to a registered account and disables registration for unverified accounts', async () => {
  tournament.status = 'registration';
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByRole('button', { name: 'Withdraw registration' })).toBeEnabled();
  cleanup();
  tournament.entrants = [];
  tournament.entrant_count = 0;
  identity.email_verified = false;
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByRole('button', { name: 'Register for tournament' })).toBeDisabled();
  expect(
    screen.getByRole('link', { name: 'Verify your email before registering.' }),
  ).toHaveAttribute('href', '/profile#email-verification');
});
it('sends server-side status/ruleset/upcoming filters and resets the paging cursor on a filter change', async () => {
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path.startsWith('/player-tournaments?')
      ? Promise.resolve({
          items: [tournament],
          next_cursor: path.includes('cursor=') ? null : 'page-two',
          limit: 20,
        } as never)
      : original(path, ...rest),
  );
  render(<PlayerTournaments />);
  fireEvent.click(await screen.findByRole('button', { name: 'Next page' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      expect.stringContaining('cursor=page-two'),
      undefined,
      undefined,
      expect.any(AbortSignal),
    ),
  );
  fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'upcoming' } });
  await waitFor(() =>
    expect(api).toHaveBeenLastCalledWith(
      '/player-tournaments?limit=20&phase=upcoming',
      undefined,
      undefined,
      expect.any(AbortSignal),
    ),
  );
  fireEvent.change(screen.getByLabelText('Ruleset'), { target: { value: 'copenhagen' } });
  await waitFor(() =>
    expect(api).toHaveBeenLastCalledWith(
      '/player-tournaments?limit=20&phase=upcoming&ruleset=copenhagen',
      undefined,
      undefined,
      expect.any(AbortSignal),
    ),
  );
});
it('shows honest empty and retry states', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], next_cursor: null, limit: 20 });
  render(<PlayerTournaments />);
  expect(
    await screen.findByRole('heading', { name: 'The next saga is still to come' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
  cleanup();
  vi.mocked(api).mockRejectedValue(new Error('Service unavailable'));
  render(<PlayerTournaments />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Tournament data could not be refreshed.',
  );
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
});
it('captures the reviewed version when opening cancellation and preserves a stale-state error', async () => {
  identity.permissions = ['play', 'tournaments'];
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path.endsWith('/cancel')
      ? Promise.reject(new Error('This tournament changed. Reload before retrying.'))
      : original(path, ...rest),
  );
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel tournament' }));
  tournament.version = 8;
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel tournament' }),
  );
  expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
    'This tournament changed. Reload before retrying.',
  );
  expect(api).toHaveBeenCalledWith(
    '/player-tournaments/cup-1/cancel',
    { version: 7 },
    'POST',
    expect.any(AbortSignal),
  );
  expect(notify.success).not.toHaveBeenCalled();
});
it('requires a private administrator reason and uses the actual match participant for overrides', async () => {
  identity.permissions = ['play', 'tournaments'];
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Override match result' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Private administrator reason')).toBeRequired();
  fireEvent.change(within(dialog).getByLabelText('Private administrator reason'), {
    target: { value: 'Reviewed disputed connection history' },
  });
  fireEvent.change(within(dialog).getByLabelText('Match winner'), { target: { value: 'skald' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Override match result' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments/cup-1/matches/u2/override',
      { version: 7, note: 'Reviewed disputed connection history', winner: 'skald' },
      'POST',
      expect.any(AbortSignal),
    ),
  );
});
it('only confirms server-side random seeding and never submits client seeds', async () => {
  identity.permissions = ['play', 'tournaments'];
  tournament.status = 'registration';
  tournament.entrants = tournament.entrants.map((entry) => ({ ...entry, seed: null }));
  tournament.capabilities = { can_create: true, can_start: true, can_moderate: true };
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Start tournament' }));
  const dialog = screen.getByRole('dialog');
  expect(
    within(dialog).getByText('Player seeds will be assigned randomly when the tournament starts.'),
  ).toBeVisible();
  expect(
    within(dialog).getByText('Starting closes registration and creates official online games.'),
  ).toBeVisible();
  expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument();
  expect(within(dialog).queryByLabelText(/^Seed \d/)).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Start tournament' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments/cup-1/start',
      { version: 7 },
      'POST',
      expect.any(AbortSignal),
    ),
  );
  const startCall = vi
    .mocked(api)
    .mock.calls.find(([path, , method]) => path.endsWith('/start') && method === 'POST');
  expect(startCall?.[1]).not.toHaveProperty('seeds');
});
it('explains random seeding in German without seed controls', async () => {
  setLanguage('de');
  identity.permissions = ['play', 'tournaments'];
  tournament.status = 'registration';
  tournament.capabilities = { can_create: true, can_start: true, can_moderate: true };
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Turnier starten' }));
  const dialog = screen.getByRole('dialog');
  expect(
    within(dialog).getByText('Die Setzliste wird beim Turnierstart zufällig ausgelost.'),
  ).toBeVisible();
  expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument();
  expect(within(dialog).queryByLabelText(/^Setzplatz \d/)).not.toBeInTheDocument();
});
it('shows pending seed labels before the start and server-assigned seeds afterwards', async () => {
  tournament.status = 'registration';
  tournament.entrants = tournament.entrants.map((entry) => ({ ...entry, seed: null }));
  const view = render(<PlayerTournaments tournamentId="cup-1" />);
  expect(
    await screen.findByText('Player seeds will be assigned randomly when the tournament starts.'),
  ).toBeVisible();
  expect(screen.getAllByText(/Seed pending randomization/)).toHaveLength(4);
  expect(screen.queryByText('Seed 1')).not.toBeInTheDocument();
  view.unmount();
  tournament.status = 'running';
  tournament.entrants = tournament.entrants.map((entry, index) => ({
    ...entry,
    seed: index + 1,
  }));
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByText(/Seed 1/)).toBeVisible();
  expect(screen.getByText(/Seed 4/)).toBeVisible();
  expect(screen.queryByText('Seed pending randomization')).not.toBeInTheDocument();
});
it('rejects a late old-account mutation response and notification', async () => {
  tournament.status = 'registration';
  tournament.entrants = [];
  tournament.entrant_count = 0;
  let finish: (value: PlayerTournamentDetail) => void = () => {};
  let requestSignal: AbortSignal | undefined;
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, data, method, signal) => {
    if (path.endsWith('/register')) {
      requestSignal = signal;
      return new Promise((resolve) => {
        finish = resolve as typeof finish;
      });
    }
    return original(path, data, method, signal);
  });
  const view = render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Register for tournament' }));
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Register for tournament' }),
  );
  identity.id = 'keeper';
  view.rerender(<PlayerTournaments tournamentId="cup-1" />);
  expect(requestSignal?.aborted).toBe(true);
  await act(async () => finish(tournament));
  expect(notify.success).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('stops polling while hidden and cleans up timers on unmount', async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  const view = render(<PlayerTournaments tournamentId="cup-1" />);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  const calls = vi.mocked(api).mock.calls.length;
  visibility.mockReturnValue('hidden');
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(api).toHaveBeenCalledTimes(calls);
  visibility.mockReturnValue('visible');
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(vi.mocked(api).mock.calls.length).toBe(calls + 2);
  view.unmount();
  const stopped = vi.mocked(api).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(api).toHaveBeenCalledTimes(stopped);
});
it('creates a tournament with a planned start serialized as UTC', async () => {
  identity.permissions = ['play', 'tournaments'];
  render(<PlayerTournaments />);
  fireEvent.click(await screen.findByRole('button', { name: 'Create player tournament' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('Match mode')).toHaveValue('sequential');
  expect(
    within(dialog).getByRole('option', { name: 'Two-game match — simultaneous' }),
  ).toBeInTheDocument();
  expect(within(dialog).queryByRole('option', { name: 'Single game' })).not.toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText('Tournament name'), {
    target: { value: 'Winter cup' },
  });
  const picker = within(
    within(dialog).getByRole('group', { name: 'Planned start (your local time, optional)' }),
  );
  fireEvent.change(picker.getByLabelText('Date (YYYY-MM-DD)'), {
    target: { value: '2035-02-01' },
  });
  fireEvent.change(picker.getByLabelText('Time (HH:MM)'), { target: { value: '19:00' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create player tournament' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments',
      {
        name: 'Winter cup',
        ruleset: 'tablut',
        capacity: 8,
        match_mode: 'sequential',
        time_control: 'per_move',
        increment_seconds: 0,
        time_per_move: 259200,
        scheduled_start_at: new Date('2035-02-01T19:00').toISOString(),
      },
      'POST',
      expect.any(AbortSignal),
    ),
  );
  expect(notify.success).toHaveBeenCalledWith('Player tournament created.');
});
it('respects the server registration-closed projection without using the device clock', async () => {
  tournament.status = 'registration';
  tournament.registration_open = false;
  tournament.entrants = [];
  tournament.entrant_count = 0;
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByRole('button', { name: 'Register for tournament' })).toBeDisabled();
  expect(
    screen.getByText(
      'Registration is closed. Registered entrants may withdraw until the tournament starts.',
    ),
  ).toBeVisible();
});

it('lets a creator start their own tournament using server capabilities without moderation grants', async () => {
  tournament.status = 'registration';
  tournament.capabilities = { can_create: false, can_start: true, can_moderate: false };
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Start tournament' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Start tournament' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments/cup-1/start',
      { version: 7 },
      'POST',
      expect.any(AbortSignal),
    ),
  );
  for (const name of ['Cancel tournament', 'Rebuild bracket', 'Override match result'])
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
});

it('offers eligible ordinary players creation when the server grants it and submits shared total clocks', async () => {
  tournament.capabilities = { can_create: true, can_start: false, can_moderate: false };
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Create player tournament' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Tournament name'), {
    target: { value: 'Creator cup' },
  });
  fireEvent.change(within(dialog).getByLabelText('Match mode'), {
    target: { value: 'sequential' },
  });
  fireEvent.change(within(dialog).getByLabelText('Time control'), { target: { value: 'total' } });
  fireEvent.change(within(dialog).getByLabelText('Increment per move (seconds)'), {
    target: { value: '30' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create player tournament' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      '/player-tournaments',
      expect.objectContaining({
        match_mode: 'sequential',
        time_control: 'total',
        increment_seconds: 30,
        time_per_move: 259200,
      }),
      'POST',
      expect.any(AbortSignal),
    ),
  );
});

it('shows a pending sequential return without inventing a playable link', async () => {
  bracket.upper[0].matches[1].return_game_state = 'waiting_for_game';
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(
    await screen.findByText('Next game available after the previous game finishes.'),
  ).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Game 2' })).not.toBeInTheDocument();
});

it.each([
  ['waiting_for_entrants', 'Waiting for more registrations.'],
  ['waiting_for_start', 'Waiting for the planned start.'],
  ['start_pending', 'Automatic start pending.'],
  ['missed_start', /Planned start missed/],
] as const)('shows the server scheduling state %s', async (state, label) => {
  tournament.status = 'registration';
  tournament.scheduled_start_at = '2035-02-01T00:00:00Z';
  tournament.start_state = state;
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByText(label)).toBeVisible();
});

it('renders server round counts, held matches and return-game settlement in both bracket views', async () => {
  bracket.round_progress = { number: 1, finished_matches: 1, outstanding_matches: 1 };
  bracket.upper[1].matches[0].waiting_for_round = true;
  bracket.upper[1].matches[0].global_round = 2;
  bracket.lower[0].matches[0].global_round = 2;
  bracket.upper[0].matches[1].return_game_state = 'awaiting_settlement';
  bracket.final!.global_round = 4;
  bracket.reset = { ...match('reset', 'reset', 1, 'winner:f1', 'loser:f1'), global_round: 5 };
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(await screen.findByText('Tournament round 1 · 1 finished · 1 outstanding')).toBeVisible();
  expect(screen.getByRole('region', { name: 'Round 2 · Tournament round 2' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Round 1 · Tournament round 2' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Grand final · Tournament round 4' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Bracket reset · Tournament round 5' })).toBeVisible();
  expect(screen.getByText('Waiting for the current round to finish')).toBeVisible();
  expect(screen.getByText('Next game awaiting settlement of the previous game.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Accessible match list' }));
  expect(screen.getByText('Waiting for the current round to finish')).toBeVisible();
  for (const name of [
    'Upper bracket · Round 2 · Match 1 · Tournament round 2',
    'Lower bracket · Round 1 · Match 1 · Tournament round 2',
    'Grand final · Tournament round 4',
    'Bracket reset · Tournament round 5',
  ]) {
    expect(screen.getByRole('heading', { name })).toBeVisible();
  }
  expect(screen.queryByRole('link', { name: 'Game 2' })).not.toBeInTheDocument();
});

it.each([
  ['sequential', 'active'],
  ['sequential', 'results_held'],
  ['simultaneous', 'active'],
  ['simultaneous', 'results_held'],
] as const)(
  'describes %s retained games with server state %s in both bracket views',
  async (mode, state) => {
    tournament.match_mode = mode;
    const retained = bracket.upper[1].matches[0];
    const isActive = state === 'active';
    const first = { ...upper2.legs[0], game_status: isActive ? 'active' : 'completed' };
    retained.global_round = 2;
    retained.waiting_for_round = true;
    retained.retained_game_state = state;
    retained.legs =
      mode === 'simultaneous' ? [first, { ...first, leg: 2, game_id: 'retained-return' }] : [first];
    const status = isActive
      ? 'Retained games in progress'
      : 'Retained results waiting for the match’s tournament round';
    const clocks =
      'These games are playable and their clocks keep running. Match advancement waits for the match’s tournament round.';
    render(<PlayerTournaments tournamentId="cup-1" />);
    await screen.findByText(status);
    const verify = () => {
      const card = within(
        screen.getByRole('article', {
          name: 'Upper bracket · Round 2 · Match 1 · Tournament round 2',
        }),
      );
      expect(card.getByText(status)).toBeVisible();
      expect(card.queryByText('Waiting for the current round to finish')).not.toBeInTheDocument();
      expect(card.getByRole('link', { name: 'Game 1' })).toHaveAttribute(
        'href',
        '/online/games/online-game-1',
      );
      expect(card.queryByText(clocks) !== null).toBe(isActive);
    };
    verify();
    fireEvent.click(screen.getByRole('button', { name: 'Accessible match list' }));
    verify();
  },
);

it('explains independent progression using the published policy', async () => {
  tournament.progression_policy = 'independent';
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(
    await screen.findByText('Each match starts as soon as its preceding matches are decided.'),
  ).toBeVisible();
  expect(screen.queryByRole('heading', { name: /Tournament round/ })).not.toBeInTheDocument();
});

it('identifies randomized provisional bracket slots and omits links before the start', async () => {
  tournament.status = bracket.status = 'registration';
  tournament.capabilities = { can_create: false, can_start: false, can_moderate: false };
  tournament.entrants = tournament.entrants.map((entry) => ({ ...entry, seed: null }));
  bracket.preview = true;
  bracket.upper[0].matches = [match('u1', 'upper', 1, 'seed:1', 'seed:4')];
  render(<PlayerTournaments tournamentId="cup-1" />);
  expect(
    await screen.findByText(
      /Provisional bracket based on maximum entrants. Player seeds and byes will be randomized/,
    ),
  ).toBeVisible();
  expect(screen.getByText('Unassigned seed 1')).toBeVisible();
  expect(screen.queryByRole('link', { name: /Game \d/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Accessible match list' }));
  expect(screen.getByText('Unassigned seed 1')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Reschedule tournament' })).not.toBeInTheDocument();
});

it('sends a complete reschedule in UTC once and uses server validation with skewed clocks', async () => {
  tournament.status = 'registration';
  tournament.scheduled_start_at = '2035-02-01T00:00:00Z';
  tournament.capabilities = {
    can_create: false,
    can_start: false,
    can_moderate: false,
    can_reschedule: true,
  };
  const original = vi.mocked(api).getMockImplementation()!;
  let resolve: () => void = () => {};
  vi.mocked(api).mockImplementation((path, data, ...rest) => {
    if (!path.endsWith('/reschedule')) return original(path, data, ...rest);
    const { scheduled_start_at } = data as { scheduled_start_at: string };
    if (Date.parse(scheduled_start_at) <= Date.parse('2035-02-01T00:00:00Z')) {
      return Promise.reject(new Error('The planned start must be in the future.'));
    }
    return new Promise((done) => {
      resolve = () => done(tournament as never);
    });
  });
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Reschedule tournament' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText(/^Current planned start is /)).toBeVisible();
  const button = dialog.getByRole('button', { name: 'Reschedule tournament' });
  const date = dialog.getByLabelText('Date (YYYY-MM-DD)');
  const time = dialog.getByLabelText('Time (HH:MM)');
  expect(date).toHaveValue('');
  fireEvent.click(button);
  expect(dialog.getByRole('alert')).toHaveTextContent('Enter a valid date and time.');
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/reschedule'))).toHaveLength(
    0,
  );
  const deviceClock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('1999-01-01T00:00:00Z'));
  fireEvent.change(date, { target: { value: '2000-02-01' } });
  fireEvent.click(button);
  expect(await dialog.findByRole('alert')).toHaveTextContent(
    'The planned start must be in the future.',
  );
  expect(api).toHaveBeenCalledWith(
    '/player-tournaments/cup-1/reschedule',
    { version: 7, scheduled_start_at: new Date('2000-02-01T12:00').toISOString() },
    'POST',
    expect.any(AbortSignal),
  );
  vi.mocked(api).mockClear();
  deviceClock.mockReturnValue(Date.parse('2036-01-01T00:00:00Z'));
  fireEvent.change(date, { target: { value: '2035-02-02' } });
  fireEvent.change(time, { target: { value: '19:30' } });
  fireEvent.submit(button.closest('form')!);
  fireEvent.submit(button.closest('form')!);
  expect(button).toBeDisabled();
  expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith('/reschedule'))).toEqual([
    [
      '/player-tournaments/cup-1/reschedule',
      { version: 7, scheduled_start_at: new Date('2035-02-02T19:30').toISOString() },
      'POST',
      expect.any(AbortSignal),
    ],
  ]);
  await act(async () => resolve());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('keeps the schedule and version under review while polling and reports a stale reschedule', async () => {
  tournament.status = 'registration';
  tournament.scheduled_start_at = '2035-02-01T00:00:00Z';
  tournament.capabilities = {
    can_create: false,
    can_start: false,
    can_moderate: false,
    can_reschedule: true,
  };
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, ...rest) =>
    path.endsWith('/reschedule')
      ? Promise.reject(new Error('This tournament changed. Reload before retrying.'))
      : original(path, ...rest),
  );
  render(<PlayerTournaments tournamentId="cup-1" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Reschedule tournament' }));
  const dialog = within(screen.getByRole('dialog'));
  const currentStart = dialog.getByText(/^Current planned start is /).textContent;
  tournament.version = bracket.version = 8;
  tournament.scheduled_start_at = '2035-02-03T00:00:00Z';
  fireEvent.change(dialog.getByLabelText('Date (YYYY-MM-DD)'), { target: { value: '2035-02-02' } });
  fireEvent.click(dialog.getByRole('button', { name: 'Reschedule tournament' }));
  expect(await dialog.findByRole('alert')).toHaveTextContent(
    'This tournament changed. Reload before retrying.',
  );
  expect(dialog.getByText(/^Current planned start is /)).toHaveTextContent(currentStart!);
  expect(api).toHaveBeenCalledWith(
    '/player-tournaments/cup-1/reschedule',
    expect.objectContaining({ version: 7 }),
    'POST',
    expect.any(AbortSignal),
  );
});
