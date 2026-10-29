import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PersonalStatisticsView from './PersonalStatistics';
import type { PersonalStatisticGroup } from './personalTypes';
const group: PersonalStatisticGroup = {
  ruleset: 'brandubh',
  mode: null,
  played: 4,
  completed: 3,
  wins: 2,
  losses: 1,
  draws: 0,
  win_rate: 2 / 3,
  win_rate_attackers: 0.5,
  win_rate_defenders: 1,
  average_game_length: 30,
  average_game_length_unit: 'moves',
  by_side: {
    attackers: {
      played: 2,
      completed: 2,
      wins: 1,
      losses: 1,
      draws: 0,
      win_rate: 0.5,
      average_game_length: 20,
      average_game_length_unit: 'moves',
    },
    defenders: {
      played: 2,
      completed: 1,
      wins: 1,
      losses: 0,
      draws: 0,
      win_rate: 1,
      average_game_length: 50,
      average_game_length_unit: 'moves',
    },
  },
};
afterEach(cleanup);
it('compares all metrics overall and per side and expands an AI opponent', () => {
  render(
    <PersonalStatisticsView
      statistics={{
        groups: [
          {
            ...group,
            agents: [{ ...group, id: 'baseline', name: 'Shield bearer', profile_id: 'baseline' }],
          },
        ],
        coverage: { unattributed_games: 1 },
      }}
    />,
  );
  const total = screen.getByRole('table', { name: 'Brandubh results by side' });
  expect(within(total).getByRole('row', { name: /As attacker/ })).toHaveTextContent('20 moves');
  expect(within(total).getByRole('row', { name: /As defender/ })).toHaveTextContent('50 moves');
  expect(within(total).getByRole('row', { name: /Overall/ })).toHaveTextContent('66.7%');
  expect(within(total).getByRole('row', { name: /Overall/ })).toHaveTextContent('30 moves');
  fireEvent.click(screen.getByText('Shield bearer'));
  expect(screen.getByRole('table', { name: 'Shield bearer results by side' })).toBeVisible();
  expect(screen.getByText(/1 historical games/)).toBeVisible();
});
it('shows available Elo without inventing a score for an unplayed ruleset', () => {
  render(
    <PersonalStatisticsView
      online
      statistics={{
        groups: [
          { ...group, rating: 1537.4, timed_out: 1 },
          { ...group, ruleset: 'tablut', played: 0, completed: 0, rating: 1500 },
        ],
        coverage: { unattributed_games: 0 },
      }}
    />,
  );
  expect(screen.getByText('Elo 1537')).toBeVisible();
  expect(screen.queryByText('Elo 1500')).toBeNull();
  expect(screen.getByText(/Timed-out games.*1/)).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'AI opponents' })).toBeNull();
});
it('renders an empty dataset without fabricated statistics', () => {
  render(
    <PersonalStatisticsView statistics={{ groups: [], coverage: { unattributed_games: 0 } }} />,
  );
  expect(screen.getByText('No games recorded yet.')).toBeVisible();
  expect(screen.queryByRole('table')).toBeNull();
});
