import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PersonalStatisticsView from './PersonalStatistics';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

it('groups by ruleset and shows personal outcomes without a mode label', () => {
  render(
    <PersonalStatisticsView
      statistics={{
        groups: [
          {
            ruleset: 'brandubh',
            mode: null,
            played: 3,
            wins: 2,
            losses: 1,
            draws: 0,
            win_rate: 2 / 3,
            win_rate_attackers: 0.5,
            win_rate_defenders: 1,
            average_game_length: 30,
            average_game_length_unit: 'moves',
          },
        ],
        coverage: { unattributed_games: 1 },
      }}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Brandubh' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Human vs AI' })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'AI vs AI' })).toBeNull();
  expect(screen.getByText('66.7%')).toBeVisible();
  expect(screen.getByText('30 moves')).toBeVisible();
  expect(
    screen.getByText(
      '1 historical games have no authoritative account seat and are not counted here.',
    ),
  ).toBeVisible();
});

it('shows the available Elo rating in each online ruleset heading', () => {
  render(
    <PersonalStatisticsView
      online
      statistics={{
        groups: [
          {
            ruleset: 'brandubh',
            mode: null,
            rating: 1537.4,
            played: 2,
            wins: 1,
            losses: 1,
            draws: 0,
            timed_out: 0,
            win_rate: 0.5,
            win_rate_attackers: 0.5,
            win_rate_defenders: null,
            average_game_length: 12,
            average_game_length_unit: 'moves',
          },
          {
            ruleset: 'tablut',
            mode: null,
            rating: null,
            played: 1,
            wins: 0,
            losses: 1,
            draws: 0,
            timed_out: 0,
            win_rate: 0,
            win_rate_attackers: null,
            win_rate_defenders: 0,
            average_game_length: 10,
            average_game_length_unit: 'moves',
          },
        ],
        coverage: { unattributed_games: 0 },
      }}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Brandubh (1537)' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Tablut' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Tablut (1500)' })).toBeNull();
});

it('shows online timeouts and em dashes without eligible games', () => {
  render(
    <PersonalStatisticsView
      online
      statistics={{
        groups: [
          {
            ruleset: 'copenhagen',
            mode: 'single',
            played: 1,
            wins: 0,
            losses: 1,
            draws: 0,
            timed_out: 1,
            win_rate: 0,
            win_rate_attackers: null,
            win_rate_defenders: null,
            average_game_length: null,
            average_game_length_unit: 'moves',
          },
        ],
        coverage: { unattributed_games: 0 },
      }}
    />,
  );
  expect(screen.getByText('Timed-out games')).toBeVisible();
  expect(screen.getByText('Timed-out games').closest('div')?.querySelector('dd')).toHaveTextContent(
    '1',
  );
  expect(screen.getAllByText('—')).toHaveLength(3);
});

it('renders an empty dataset without fabricated statistics', () => {
  render(
    <PersonalStatisticsView statistics={{ groups: [], coverage: { unattributed_games: 0 } }} />,
  );
  expect(screen.getByText('No games recorded yet.')).toBeVisible();
});
