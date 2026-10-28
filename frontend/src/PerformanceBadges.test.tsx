import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PerformanceBadges from './PerformanceBadges';
import { badgeTier } from './personalStatisticsData';
import type { PersonalStatisticGroup } from './personalTypes';
const group: PersonalStatisticGroup = {
  ruleset: 'brandubh',
  mode: null,
  played: 4,
  completed: 4,
  wins: 3,
  losses: 1,
  draws: 0,
  win_rate: 0.75,
  win_rate_attackers: 1,
  win_rate_defenders: 0.5,
  average_game_length: 20,
  average_game_length_unit: 'moves',
};
afterEach(cleanup);
it('uses compact linked badges with progressively richer single-player decoration', () => {
  render(
    <PerformanceBadges statistics={{ groups: [group], coverage: { unattributed_games: 0 } }} />,
  );
  const badge = screen.getByRole('link', { name: /Brandubh Singleplayer statistics.*75%/ });
  expect(badge).toHaveAttribute('href', '/statistics?mode=single-player&ruleset=brandubh');
  expect(badge).toHaveClass('badge-gold');
  expect(screen.getAllByRole('link')).toHaveLength(3);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/Elo/)).toBeNull();
});
it.each([
  [null, 'unplayed'],
  [0, 'bronze'],
  [0.4, 'silver'],
  [0.65, 'gold'],
  [0.85, 'royal'],
  [1, 'royal'],
] as const)('decorates the win rate %s as %s', (rate, tier) => {
  expect(badgeTier(rate)).toBe(tier);
});
it('shows real multiplayer Elo only when that ruleset has games, including a zero rating', () => {
  render(
    <PerformanceBadges
      online
      statistics={{
        groups: [
          { ...group, rating: 1537.4 },
          { ...group, ruleset: 'tablut', played: 0, completed: 0, win_rate: null, rating: 1500 },
          { ...group, ruleset: 'copenhagen', rating: 0 },
        ],
        coverage: { unattributed_games: 0 },
      }}
    />,
  );
  expect(screen.getByText('Elo 1537')).toBeVisible();
  expect(screen.getByText('Elo 0')).toBeVisible();
  expect(screen.queryByText('Elo 1500')).toBeNull();
  const empty = screen.getByRole('link', { name: /Tablut Multiplayer statistics/ });
  expect(empty).not.toHaveAccessibleName(/Elo/);
  expect(empty).not.toHaveTextContent(/Elo/);
});
