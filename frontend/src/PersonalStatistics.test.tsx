import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PersonalStatisticsView from './PersonalStatistics';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

it('groups by ruleset and matchup type and shows personal outcomes', () => {
  render(
    <PersonalStatisticsView
      statistics={{
        groups: [
          {
            ruleset: 'brandubh',
            mode: 'human-ai',
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
          {
            ruleset: 'tablut',
            mode: 'ai-ai',
            played: 2,
            wins: null,
            losses: null,
            draws: null,
            win_rate: null,
            win_rate_attackers: null,
            win_rate_defenders: null,
            average_game_length: 20,
            average_game_length_unit: 'moves',
          },
        ],
        coverage: { unattributed_games: 1 },
      }}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Brandubh' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Human vs AI' })).toBeVisible();
  expect(screen.getByText('66.7%')).toBeVisible();
  expect(screen.getByText('30 moves')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'AI vs AI' })).toBeVisible();
  expect(screen.getByText('AI vs AI games have no personal result.')).toBeVisible();
  expect(
    screen.getByText(
      '1 historical games have no authoritative account seat and are not counted here.',
    ),
  ).toBeVisible();
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
