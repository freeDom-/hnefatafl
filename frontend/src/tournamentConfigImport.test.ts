import { expect, it } from 'vitest';
import {
  MAX_TOURNAMENT_CONFIG_BYTES,
  TournamentConfigImportError,
  parseTournamentConfig,
  readImportedTournamentConfig,
} from './tournamentConfigImport';
import type { AIProfile, Model } from './types';

const profiles: AIProfile[] = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    difficulty: 'Beginner',
    parameters: { time_limit: 1 },
    supported_rulesets: ['brandubh', 'tablut', 'copenhagen'],
  },
  {
    id: 'mcts',
    name: 'Seer',
    algorithm: 'mcts',
    difficulty: 'Advanced',
    parameters: { iterations: 24, time_limit: 10, exploration: 1.4, temperature: 0 },
    supported_rulesets: ['brandubh', 'tablut', 'copenhagen'],
  },
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: 'Model dependent',
    parameters: { iterations: 40, time_limit: 10 },
    supported_rulesets: ['brandubh', 'tablut', 'copenhagen'],
  },
];
const models: Model[] = [
  { id: 'model-brandubh', name: 'Raven', ruleset: 'brandubh' },
  { id: 'model-tablut', name: 'Wanderer', ruleset: 'tablut' },
];

const valid = {
  name: 'Spring round robin',
  rulesets: ['brandubh'],
  players: [
    { profile_id: 'baseline', config: { algorithm: 'baseline', time_limit: 1 } },
    {
      profile_id: 'neural',
      config: {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'mcts',
        time_limit: 10,
      },
    },
  ],
  games: 4,
  max_plies: 150,
  seed: 42,
};

const file = (value: unknown, name = 'tournament.json') =>
  new File([typeof value === 'string' ? value : JSON.stringify(value)], name, {
    type: 'application/json',
  });

it('parses a portable configuration and snapshots named presets', () => {
  const result = parseTournamentConfig(valid, profiles, models);
  expect(result.name).toBe('Spring round robin');
  expect(result.config.rulesets).toEqual(['brandubh']);
  expect(result.config.games).toBe(4);
  expect(result.config.max_plies).toBe(150);
  expect(result.config.seed).toBe(42);
  expect(result.config.players[0].config).toMatchObject({ algorithm: 'baseline', time_limit: 1 });
  expect(result.config.players[1].config).toMatchObject({
    algorithm: 'neural',
    model_id: 'model-brandubh',
    search_wrapper: 'mcts',
    time_limit: 10,
  });
});

it('keeps a custom opponent’s explicit budget and defaults a profile opponent', () => {
  const result = parseTournamentConfig(
    {
      ...valid,
      players: [
        { config: { algorithm: 'mcts', iterations: 33, time_limit: 7.5 } },
        { profile_id: 'mcts', config: { algorithm: 'baseline', iterations: 2, time_limit: 0.01 } },
      ],
    },
    profiles,
    models,
  );
  // The named preset is authoritative, exactly as the API snapshots it.
  expect(result.config.players[1].config).toMatchObject({
    algorithm: 'mcts',
    iterations: 24,
    time_limit: 10,
  });
  expect(result.config.players[0].config).toMatchObject({
    algorithm: 'mcts',
    iterations: 33,
    time_limit: 7.5,
  });
});

it.each([
  ['not an object', 42],
  ['an array', []],
  ['missing players', { ...valid, players: undefined }],
  ['too few players', { ...valid, players: [valid.players[0]] }],
  [
    'too many players',
    { ...valid, players: [...valid.players, ...valid.players, ...valid.players] },
  ],
  ['unsupported ruleset', { ...valid, rulesets: ['atlantis'] }],
  ['duplicate rulesets', { ...valid, rulesets: ['brandubh', 'brandubh'] }],
  ['odd game count', { ...valid, games: 3 }],
  ['out-of-range plies', { ...valid, max_plies: 0 }],
  ['bad seed', { ...valid, seed: 2 ** 32 }],
  ['blank name', { ...valid, name: '   ' }],
])('rejects %s without returning a partial configuration', (_label, value) => {
  expect(() => parseTournamentConfig(value, profiles, models)).toThrow(TournamentConfigImportError);
});

it('rejects unknown profiles, algorithms and unresolved model references', () => {
  const cases = [
    { ...valid, players: [{ profile_id: 'ghost' }, valid.players[1]] },
    {
      ...valid,
      players: [{ config: { algorithm: 'atlantis' } }, valid.players[1]],
    },
    {
      ...valid,
      players: [{ config: { algorithm: 'neural', model_id: 'missing' } }, valid.players[1]],
    },
    {
      ...valid,
      rulesets: ['brandubh', 'tablut'],
      players: [{ config: { algorithm: 'neural', model_id: 'model-brandubh' } }, valid.players[1]],
    },
  ];
  for (const value of cases) {
    expect(() => parseTournamentConfig(value, profiles, models)).toThrow(
      TournamentConfigImportError,
    );
  }
});

it('rejects malformed JSON and oversized files without touching the form', async () => {
  await expect(
    readImportedTournamentConfig(file('{not json'), profiles, models),
  ).rejects.toBeInstanceOf(TournamentConfigImportError);
  await expect(
    readImportedTournamentConfig(
      new File(['x'.repeat(MAX_TOURNAMENT_CONFIG_BYTES + 1)], 'huge.json'),
      profiles,
      models,
    ),
  ).rejects.toBeInstanceOf(TournamentConfigImportError);
});
