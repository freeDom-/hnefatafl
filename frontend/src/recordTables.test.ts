import { describe, expect, it } from 'vitest';
import {
  matchesPlayer,
  matchesRecordedGame,
  sortStatRows,
  statPlayerDescription,
} from './recordTables';
import type { Game, Ruleset, StatRow } from './types';

const rulesets: Ruleset[] = [
  { id: 'a', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'b', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const ids = (rows: StatRow[]) => rows.map((row) => row.id ?? row.ruleset);

describe('record table comparisons', () => {
  it('sorts counts, percentages and averages as numbers without mutating data', () => {
    const rows: StatRow[] = [
      { id: 'ten', games: 10, win_rate: 0.1, average_game_length: 12.5 },
      { id: 'two', games: 2, win_rate: 0.8, average_game_length: 3 },
      { id: 'hundred', games: 100, win_rate: 0.25, average_length: 40 },
    ];
    expect(ids(sortStatRows(rows, { key: 'games', direction: 'ascending' }, 'player', []))).toEqual(
      ['two', 'ten', 'hundred'],
    );
    expect(
      ids(sortStatRows(rows, { key: 'rates', direction: 'descending' }, 'player', [])),
    ).toEqual(['two', 'hundred', 'ten']);
    expect(
      ids(sortStatRows(rows, { key: 'average_game_length', direction: 'ascending' }, 'player', [])),
    ).toEqual(['two', 'ten', 'hundred']);
    expect(ids(rows)).toEqual(['ten', 'two', 'hundred']);
    expect(sortStatRows(rows, null, 'player', [])).not.toBe(rows);
  });

  it('compares result and rate triples in the documented win, draw, loss order', () => {
    const rows: StatRow[] = [
      { id: 'a', wins: 2, draws: 1, losses: 9, win_rate: 0.4, draw_rate: 0.1, loss_rate: 0.5 },
      { id: 'b', wins: 2, draws: 3, losses: 1, win_rate: 0.4, draw_rate: 0.3, loss_rate: 0.3 },
      { id: 'c', wins: 2, draws: 3, losses: 4, win_rate: 0.4, draw_rate: 0.3, loss_rate: 0.4 },
    ];
    expect(
      ids(sortStatRows(rows, { key: 'outcomes', direction: 'descending' }, 'player', [])),
    ).toEqual(['c', 'b', 'a']);
    expect(ids(sortStatRows(rows, { key: 'rates', direction: 'ascending' }, 'player', []))).toEqual(
      ['a', 'b', 'c'],
    );
  });

  it('uses friendly battlefield names and deterministic identity ties in either direction', () => {
    expect(
      ids(
        sortStatRows(
          [
            { id: 'ulf', kind: 'ai', name: 'My first strategist', model_name: 'Ulf' },
            { id: 'freydis', kind: 'ai', name: 'My first strategist', model_name: 'Freydis' },
          ],
          { key: 'name', direction: 'ascending' },
          'player',
          [],
        ),
      ),
    ).toEqual(['freydis', 'ulf']);
    expect(
      ids(
        sortStatRows(
          [{ ruleset: 'a' }, { ruleset: 'b' }],
          { key: 'name', direction: 'ascending' },
          'ruleset',
          rulesets,
        ),
      ),
    ).toEqual(['b', 'a']);
    const ties: StatRow[] = [{ id: 'b', games: NaN }, { id: 'a' }, { id: 'c', games: Infinity }];
    for (const direction of ['ascending', 'descending'] as const)
      expect(ids(sortStatRows(ties, { key: 'games', direction }, 'player', []))).toEqual([
        'a',
        'b',
        'c',
      ]);
  });
});

describe('compact players and independent name filters', () => {
  const seer: StatRow = {
    name: 'Raven strategist',
    kind: 'ai',
    profile_id: 'mcts',
    profile_name: 'Seer',
    config: { algorithm: 'mcts', iterations: 100, time_limit: 5, seed: 41 },
  };

  it('summarizes important AI settings once while keeping humans compact', () => {
    expect(statPlayerDescription(seer)).toBe(
      'Computer · Seer · Monte Carlo tree search · 100 iterations · 5s',
    );
    expect(statPlayerDescription({ ...seer, profile_name: 'Monte Carlo tree search' })).toBe(
      'Computer · Monte Carlo tree search · 100 iterations · 5s',
    );
    expect(statPlayerDescription({ kind: 'human', name: 'Astrid' })).toBe('Human');
    expect(statPlayerDescription({ name: 'Unknown' })).toBe('');
    expect(
      statPlayerDescription({ kind: 'ai', config: { algorithm: 'neural' }, model_name: 'Eir' }),
    ).toBe('Computer · Trained model · Eir · Neural search');
  });

  it('filters player names and their displayed metadata case insensitively', () => {
    expect(matchesPlayer(seer, '  RAVEN  ')).toBe(true);
    expect(matchesPlayer(seer, 'monte carlo')).toBe(true);
    expect(matchesPlayer(seer, '100 iterations')).toBe(true);
    expect(matchesPlayer(seer, 'Jarl')).toBe(false);
    expect(matchesPlayer(seer, '')).toBe(true);
  });

  it('matches either recorded participant and historical AI labels, never a battlefield name', () => {
    const game = {
      ruleset: 'brandubh',
      players: {
        attackers: { name: 'Astrid', kind: 'human', config: {} },
        defenders: {
          name: 'Computer',
          kind: 'ai',
          profile_name: 'Seer',
          model_name: 'Eir Stormshield',
          config: {},
        },
      },
    } as Game;
    expect(matchesRecordedGame(game, 'ASTRID')).toBe(true);
    expect(matchesRecordedGame(game, 'seer')).toBe(true);
    expect(matchesRecordedGame(game, 'Stormshield')).toBe(true);
    expect(matchesRecordedGame(game, 'brandubh')).toBe(false);
    expect(matchesRecordedGame(game, 'Munin')).toBe(false);
    expect(matchesRecordedGame(game, '  ')).toBe(true);
  });
});
