import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import RecordedGames, { winnerLabel, type RecordedGame } from './RecordedGames';
import { setLanguage, t } from './i18n';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

const base: RecordedGame = {
  id: 'game-1',
  category: 'online',
  ruleset: 'brandubh',
  status: 'completed',
  winner: 'attackers',
  eligible: true,
  created_at: '2035-01-01T00:00:00Z',
  recorded_at: '2035-01-01T00:00:00Z',
  attackers: 'Odin',
  defenders: 'Seer',
  owner: null,
};

it('renders the specified column headers in order', () => {
  render(<RecordedGames games={[base]} />);
  const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
  expect(headers).toEqual(['Status', 'Ruleset', 'Attacker', 'Defender', 'Winner', 'Date']);
});

it('formats the winner as name and singular side', () => {
  expect(winnerLabel(base, t)).toBe('Odin (Attacker)');
  expect(winnerLabel({ ...base, winner: 'defenders' }, t)).toBe('Seer (Defender)');
  expect(winnerLabel({ ...base, winner: 'draw' }, t)).toBe('Draw');
});

it('never invents a winner for non-winning states', () => {
  expect(winnerLabel({ ...base, status: 'in_progress', winner: null }, t)).toBe('—');
  expect(winnerLabel({ ...base, status: 'cancelled', winner: null }, t)).toBe('—');
  expect(winnerLabel({ ...base, status: 'voided', winner: null }, t)).toBe('—');
  // A stale winner on a non-completed record is not shown as a result.
  expect(winnerLabel({ ...base, status: 'voided', winner: 'attackers' }, t)).toBe('—');
  // A completed record with a missing name never invents one.
  expect(winnerLabel({ ...base, attackers: null }, t)).toBe('Unassigned (Attacker)');
});

it('shows a locale-aware date and time with a timezone and no Review label', () => {
  setLanguage('de', false);
  render(<RecordedGames games={[base]} />);
  const time = screen.getByText(/2035/);
  expect(time.tagName).toBe('TIME');
  expect(time).toHaveAttribute('datetime', '2035-01-01T00:00:00Z');
  expect(time.textContent).toMatch(/MEZ|GMT|UTC|CET/);
  expect(screen.queryByText('Review')).toBeNull();
  expect(screen.queryByText('Ansehen')).toBeNull();
});

it('provides an accessible link to the full review and highlights the selected row', () => {
  render(<RecordedGames games={[base]} selected={base.id} />);
  const link = screen.getByRole('link', { name: 'Open review: Odin vs Seer' });
  expect(link).toHaveAttribute('href', `/archive?review=online&selected=${base.id}`);
  expect(link).toHaveAttribute('aria-current', 'true');
  const row = link.closest('tr')!;
  expect(within(row).getByText('Odin (Attacker)')).toBeVisible();
  expect(row).toHaveClass('selected');
});

it('shows immutable start ratings and a positive winner delta only when available', () => {
  render(
    <RecordedGames
      games={[{ ...base, attacker_rating: 1354.2, defender_rating: 1490, winner_delta: 35.4 }]}
    />,
  );
  const row = screen.getByRole('link', { name: 'Open review: Odin vs Seer' }).closest('tr')!;
  expect(within(row).getByText('Odin (1354)')).toBeVisible();
  expect(within(row).getByText('Seer (1490)')).toBeVisible();
  const delta = within(row).getByText('+35');
  expect(delta).toHaveClass('winner-delta');
  expect(delta).toHaveAccessibleName('+35 rating gain');
});

it('never shows a start rating without a snapshot or a delta without a positive gain', () => {
  render(
    <RecordedGames
      games={[
        { ...base, attacker_rating: null, defender_rating: null, winner_delta: null },
        { ...base, id: 'draw', winner: 'draw', winner_delta: 3 },
        { ...base, id: 'void', status: 'voided', winner: 'attackers', winner_delta: 9 },
        { ...base, id: 'flat', winner_delta: 0.2 },
      ]}
    />,
  );
  const rows = screen.getAllByRole('row').slice(1);
  const cells = (row: HTMLElement) => within(row).getAllByRole('cell');
  expect(cells(rows[0])[2].textContent).toBe('Odin');
  expect(cells(rows[0])[4].textContent).not.toMatch(/\+/);
  expect(cells(rows[1])[4].textContent).not.toMatch(/\+/);
  expect(cells(rows[2])[4].textContent).not.toMatch(/\+/);
  // A tiny positive change rounds to +0 but is still a gain, not a loss.
  expect(within(cells(rows[3])[4]).getByText('+0')).toBeVisible();
});
