import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import LocalStatisticsView from './LocalStatistics';
import { setLanguage } from './i18n';
import type { LocalHistory } from './personalTypes';

const history: LocalHistory = {
  category: 'local',
  games: [],
  totals: {
    total_games: 5,
    completed_games: 4,
    in_progress: 1,
    attackers_wins: 3,
    defenders_wins: 1,
    draws: 0,
    average_game_length: 24.5,
  },
  limit: 1,
  matching: 5,
  next_cursor: null,
};

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

it('shows owner-scoped local 1v1 totals as a separate non-Elo category', () => {
  render(<LocalStatisticsView history={history} />);
  const table = screen.getByRole('table', { name: 'Local 1v1 totals' });
  expect(within(table).getByRole('row', { name: /Games played/ })).toHaveTextContent('5');
  expect(within(table).getByRole('row', { name: /Completed/ })).toHaveTextContent('4');
  expect(within(table).getByRole('row', { name: /In progress/ })).toHaveTextContent('1');
  expect(within(table).getByRole('row', { name: /Attacker wins/ })).toHaveTextContent('3');
  expect(within(table).getByRole('row', { name: /Defender wins/ })).toHaveTextContent('1');
  expect(screen.getByText('Non-Elo · same device')).toBeVisible();
  expect(screen.getByText('24.5 moves')).toBeVisible();
  expect(screen.getByText('Local totals cover every ruleset and never affect Elo.')).toBeVisible();
});

it('never invents a local record when the account has none', () => {
  render(
    <LocalStatisticsView
      history={{
        ...history,
        totals: { ...history.totals, total_games: 0, completed_games: 0, in_progress: 0 },
      }}
    />,
  );
  expect(screen.getByText('No local 1v1 games recorded yet.')).toBeVisible();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/Elo/)).toBeNull();
});

it('translates the local category into German', () => {
  setLanguage('de', false);
  render(<LocalStatisticsView history={history} />);
  expect(screen.getByRole('heading', { name: 'Lokal 1 gegen 1' })).toBeVisible();
  expect(screen.getByText('Ohne Elo · am selben Gerät')).toBeVisible();
});
