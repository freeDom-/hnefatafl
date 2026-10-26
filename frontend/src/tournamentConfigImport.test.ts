import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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
    parameters: { iterations: 5000, time_limit: 2, exploration: 1.4, temperature: 0 },
    supported_rulesets: ['brandubh', 'tablut', 'copenhagen'],
  },
  {
    id: 'alpha_beta',
    name: 'Jarl',
    algorithm: 'alpha_beta',
    difficulty: 'Strong',
    parameters: { depth: 5, time_limit: 2 },
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
    iterations: 5000,
    time_limit: 2,
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

it('parses and normalises a nested alpha-beta search configuration', () => {
  const result = parseTournamentConfig(
    {
      ...valid,
      players: [
        {
          config: {
            algorithm: 'alpha_beta',
            depth: 3,
            time_limit: 1,
            search: {
              legacy: false,
              transposition: false,
              move_ordering: false,
              symmetry_transposition: true,
              symmetry_pruning: false,
              table_capacity: 4096,
            },
          },
        },
        { config: { algorithm: 'alpha_beta', depth: 2, time_limit: 1, search: { legacy: true } } },
      ],
    },
    profiles,
    models,
  );
  expect(result.config.players[0].config.search).toEqual({
    legacy: false,
    transposition: true,
    move_ordering: false,
    symmetry_transposition: true,
    symmetry_pruning: false,
    table_capacity: 4096,
  });
  // Legacy collapses to the deterministic default shape.
  expect(result.config.players[1].config.search).toEqual({
    legacy: true,
    transposition: true,
    move_ordering: true,
    symmetry_transposition: false,
    symmetry_pruning: false,
    table_capacity: 100_000,
  });
});

it('rejects malformed or misplaced nested search configurations', () => {
  const cases = [
    { algorithm: 'alpha_beta', search: { unknown: true } },
    { algorithm: 'alpha_beta', search: { transposition: 1 } },
    { algorithm: 'alpha_beta', search: { table_capacity: 0 } },
    { algorithm: 'alpha_beta', search: { table_capacity: 1.5 } },
    { algorithm: 'baseline', search: { legacy: true } },
    {
      algorithm: 'neural',
      model_id: 'model-brandubh',
      search_wrapper: 'alpha_beta',
      search: { symmetry_pruning: true },
    },
  ];
  for (const config of cases) {
    expect(() =>
      parseTournamentConfig(
        { ...valid, players: [{ config }, { config: { algorithm: 'baseline' } }] },
        profiles,
        models,
      ),
    ).toThrow(TournamentConfigImportError);
  }
});

it('prevents preset spoofing of fixed profiles', () => {
  const result = parseTournamentConfig(
    {
      ...valid,
      players: [
        {
          profile_id: 'alpha_beta',
          config: { algorithm: 'alpha_beta', depth: 1, search: { legacy: true } },
        },
        { config: { algorithm: 'baseline', time_limit: 1 } },
      ],
    },
    profiles,
    models,
  );
  // The catalog snapshot wins; the spoofed search settings never survive.
  expect(result.config.players[0].config).toEqual({
    algorithm: 'alpha_beta',
    depth: 5,
    time_limit: 2,
  });
});

it('parses the shipped alpha-beta comparison example with nested search configs', () => {
  const document = JSON.parse(
    readFileSync(resolve(process.cwd(), '../configs/alpha-beta-search-comparison.json'), 'utf8'),
  );
  const result = parseTournamentConfig(document, profiles, models);
  expect(result.name).toBe('Alpha-beta search comparison');
  expect(result.config.rulesets).toEqual(['brandubh']);
  expect(result.config.games).toBe(20);
  expect(result.config.max_plies).toBe(500);
  expect(result.config.seed).toBe(20261026);
  expect(result.config.players[0].config.search).toMatchObject({ legacy: true });
  expect(result.config.players[1].config.search).toMatchObject({
    symmetry_transposition: true,
    symmetry_pruning: true,
  });
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

it('parses and validates the model-free search heuristic', () => {
  const result = parseTournamentConfig(
    {
      ...valid,
      players: [
        {
          config: { algorithm: 'alpha_beta', depth: 2, time_limit: 1, heuristic: 'shield_bearer' },
        },
        { config: { algorithm: 'mcts', time_limit: 1, heuristic: 'shield_maiden' } },
      ],
    },
    profiles,
    models,
  );
  expect(result.config.players[0].config.heuristic).toBe('shield_bearer');
  expect(result.config.players[1].config.heuristic).toBe('shield_maiden');

  for (const config of [
    { algorithm: 'alpha_beta', heuristic: 'unknown' },
    { algorithm: 'baseline', heuristic: 'shield_bearer' },
    { algorithm: 'neural', model_id: 'model-brandubh', heuristic: 'shield_maiden' },
  ]) {
    expect(() =>
      parseTournamentConfig(
        { ...valid, players: [{ config }, { config: { algorithm: 'baseline' } }] },
        profiles,
        models,
      ),
    ).toThrow(TournamentConfigImportError);
  }
});
