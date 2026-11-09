import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';
import {
  MAX_TOURNAMENT_CONFIG_BYTES,
  TournamentConfigImportError,
  parseTournamentConfig,
  readImportedTournamentConfig,
} from './tournamentConfigImport';
import { DEFAULT_ALPHA_BETA_SEARCH } from './alphaBetaSearch';
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

const fixedRoles = {
  name: 'Model side performance',
  mode: 'fixed_roles',
  rulesets: ['brandubh'],
  players: [
    {
      config: {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'mcts',
        iterations: 120,
        time_limit: 2,
      },
    },
    {
      config: {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'mcts',
        iterations: 120,
        time_limit: 2,
      },
    },
  ],
  games: 7,
  max_plies: 200,
  seed: 5,
};

it('parses a fixed-role document with its shared search configuration', () => {
  const result = parseTournamentConfig(fixedRoles, profiles, models);
  expect(result.config.mode).toBe('fixed_roles');
  expect(result.config.games).toBe(7);
  expect(result.config.players.map((player) => player.config.model_id)).toEqual([
    'model-brandubh',
    'model-brandubh',
  ]);
  // A missing mode keeps the historical balanced round robin.
  expect(parseTournamentConfig(valid, profiles, models).config.mode).toBe('round_robin');
});

it('rejects fixed-role documents with invalid participants, models or tournament settings', () => {
  const rejected = [
    { ...fixedRoles, mode: 'unknown' },
    { ...fixedRoles, games: 0 },
    { ...fixedRoles, games: 101 },
    { ...fixedRoles, games: 3.5 },
    { ...fixedRoles, rulesets: ['brandubh', 'tablut'] },
    { ...fixedRoles, players: [fixedRoles.players[0]] },
    {
      ...fixedRoles,
      players: [
        fixedRoles.players[0],
        {
          config: {
            algorithm: 'neural',
            model_id: 'model-tablut',
            search_wrapper: 'mcts',
            iterations: 120,
            time_limit: 2,
          },
        },
      ],
    },
  ];
  for (const document of rejected)
    expect(() => parseTournamentConfig(document, profiles, models)).toThrow(
      TournamentConfigImportError,
    );
  // A round-robin document still needs an even game count.
  expect(() => parseTournamentConfig({ ...valid, games: 3 }, profiles, models)).toThrow(
    TournamentConfigImportError,
  );
});

const fixedRoleDocument = (
  attacker: Record<string, unknown>,
  defender: Record<string, unknown>,
) => ({
  name: 'Fixed roles',
  mode: 'fixed_roles',
  rulesets: ['brandubh'],
  players: [
    { config: { algorithm: 'neural', model_id: 'model-brandubh', ...attacker } },
    { config: { algorithm: 'neural', model_id: 'model-brandubh', ...defender } },
  ],
  games: 7,
  max_plies: 200,
  seed: 5,
});

const parseSeats = (
  attacker: Record<string, unknown>,
  defender: Record<string, unknown>,
  modelsForSeats = models,
) => parseTournamentConfig(fixedRoleDocument(attacker, defender), profiles, modelsForSeats);

it('compares effective fixed-role search settings instead of raw objects', () => {
  // An omitted default and the same value recorded explicitly are one setting.
  expect(() => parseSeats({}, { time_limit: 2 })).not.toThrow();
  expect(() =>
    parseSeats({}, { iterations: 80, rollout_depth: 8, exploration: 1.4, temperature: 0 }),
  ).not.toThrow();
  expect(() =>
    parseSeats({ search_wrapper: 'alpha_beta' }, { search_wrapper: 'alpha_beta', depth: 2 }),
  ).not.toThrow();
  expect(() =>
    parseSeats({ search_wrapper: 'none' }, { search_wrapper: 'none', temperature: 0 }),
  ).not.toThrow();
  // Different model identities with equivalent settings are still valid.
  const secondBrandubh = [
    ...models,
    { id: 'model-brandubh-two', name: 'Raven two', ruleset: 'brandubh' },
  ];
  const parsed = parseTournamentConfig(
    {
      ...fixedRoleDocument({ iterations: 80 }, {}),
      players: [
        { config: { algorithm: 'neural', model_id: 'model-brandubh' } },
        { config: { algorithm: 'neural', model_id: 'model-brandubh-two', iterations: 80 } },
      ],
    },
    profiles,
    secondBrandubh,
  );
  expect(parsed.config.players.map((player) => player.config.model_id)).toEqual([
    'model-brandubh',
    'model-brandubh-two',
  ]);
});

it('preserves independent fixed-role search settings', () => {
  const rejected: [Record<string, unknown>, Record<string, unknown>][] = [
    [{ iterations: 81 }, {}],
    [{ exploration: 2 }, {}],
    [{ rollout_depth: 4 }, {}],
    [{ temperature: 0.5 }, {}],
    [{ time_limit: 1 }, {}],
    [{ search_wrapper: 'none' }, { search_wrapper: 'mcts' }],
    [{ search_wrapper: 'alpha_beta', depth: 3 }, { search_wrapper: 'alpha_beta' }],
    [{ search_wrapper: 'alpha_beta', search: {} }, { search_wrapper: 'alpha_beta' }],
    [
      { search_wrapper: 'alpha_beta', search: { legacy: true } },
      { search_wrapper: 'alpha_beta', search: {} },
    ],
    [
      { search_wrapper: 'alpha_beta', search: { move_ordering: false } },
      { search_wrapper: 'alpha_beta', search: {} },
    ],
    [
      { search_wrapper: 'alpha_beta', search: { table_capacity: 2048 } },
      { search_wrapper: 'alpha_beta', search: {} },
    ],
  ];
  for (const [attacker, defender] of rejected) {
    const parsed = parseSeats(attacker, defender);
    expect(parsed.config.players[0].config).toMatchObject(attacker);
    expect(parsed.config.players[1].config).toMatchObject(defender);
  }
});

const importFields = (document: unknown) => {
  try {
    parseTournamentConfig(document, profiles, models);
  } catch (error) {
    if (error instanceof TournamentConfigImportError) return error.fields;
    throw error;
  }
  return null;
};

it('rejects unknown AI configuration fields instead of dropping them', () => {
  // A misspelled `iterations` would otherwise silently run with the default.
  expect(
    importFields({
      ...valid,
      players: [{ config: { algorithm: 'mcts', iteratons: 500 } }, valid.players[1]],
    }),
  ).toEqual(['players[0].config.iteratons']);
  // The second participant is named when it is the offending one.
  expect(
    importFields({
      ...valid,
      players: [valid.players[0], { config: { algorithm: 'mcts', iterations: 100, extra: 1 } }],
    }),
  ).toEqual(['players[1].config.extra']);
  // A headless-only checkpoint path is not part of the browser contract.
  expect(
    importFields({
      ...valid,
      players: [valid.players[0], { config: { algorithm: 'mcts', model_path: '/models/a.pt' } }],
    }),
  ).toEqual(['players[1].config.model_path']);
});

it('rejects unknown fields in a fixed-role comparison', () => {
  // The participant index and the offending field are both reported.
  expect(
    importFields(
      fixedRoleDocument(
        { search_wrapper: 'mcts', iterations: 100 },
        { search_wrapper: 'mcts', iterations: 100, exploraton: 1.4 },
      ),
    ),
  ).toContain('players[1].config.exploraton');
});

it('keeps every supported AI configuration field', () => {
  const cases: [Record<string, unknown>, Record<string, unknown>][] = [
    [
      { algorithm: 'baseline', time_limit: 3, seed: 7, evaluation: 'heuristic' },
      { algorithm: 'baseline', time_limit: 3, seed: 7, evaluation: 'heuristic' },
    ],
    [
      { algorithm: 'shield_maiden', time_limit: 2, heuristic_version: 1, seed: 1 },
      { algorithm: 'shield_maiden', time_limit: 2, heuristic_version: 1, seed: 1 },
    ],
    [
      {
        algorithm: 'mcts',
        iterations: 120,
        exploration: 1.2,
        temperature: 0.4,
        rollout_depth: 12,
        time_limit: 5,
        seed: 9,
        heuristic: 'shield_bearer',
      },
      {
        algorithm: 'mcts',
        iterations: 120,
        exploration: 1.2,
        temperature: 0.4,
        rollout_depth: 12,
        time_limit: 5,
        seed: 9,
        heuristic: 'shield_bearer',
      },
    ],
    [
      {
        algorithm: 'alpha_beta',
        depth: 4,
        time_limit: 2,
        seed: 3,
        heuristic: 'shield_maiden',
        search: { legacy: false, move_ordering: false },
      },
      {
        algorithm: 'alpha_beta',
        depth: 4,
        time_limit: 2,
        seed: 3,
        heuristic: 'shield_maiden',
        search: { legacy: false, move_ordering: false },
      },
    ],
    [
      {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'alpha_beta',
        depth: 3,
        time_limit: 1,
        seed: 5,
        evaluation: 'neural',
        search: { transposition: true, move_ordering: true },
      },
      {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'alpha_beta',
        depth: 3,
        time_limit: 1,
        seed: 5,
        evaluation: 'neural',
        search: { transposition: true, move_ordering: true },
      },
    ],
    [
      {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'none',
        temperature: 0.7,
        time_limit: 1,
      },
      {
        algorithm: 'neural',
        model_id: 'model-brandubh',
        search_wrapper: 'none',
        temperature: 0.7,
        time_limit: 1,
      },
    ],
  ];
  for (const [attacker, defender] of cases) {
    const parsed = parseTournamentConfig(
      { ...valid, players: [{ config: attacker }, { config: defender }] },
      profiles,
      models,
    );
    expect(parsed.config.players[0].config).toMatchObject(attacker);
    expect(parsed.config.players[1].config).toMatchObject(defender);
  }
});

it('validates participant seeds even though the tournament seed supersedes them', () => {
  const seedOf = (seed: unknown) =>
    parseTournamentConfig(
      { ...valid, players: [{ config: { algorithm: 'mcts', seed } }, valid.players[1]] },
      profiles,
      models,
    ).config.players[0].config.seed;
  expect(seedOf(11)).toBe(11);
  expect(seedOf(0)).toBe(0);
  for (const seed of [-1, 2 ** 32, 1.5, true, '42', null]) {
    expect(
      importFields({
        ...valid,
        players: [{ config: { algorithm: 'mcts', seed } }, valid.players[1]],
      }),
    ).toEqual(['players[0].config.seed']);
  }
  // Different valid participant seeds keep an otherwise compatible pairing valid.
  expect(() =>
    parseSeats({ iterations: 80, seed: 11 }, { iterations: 80, seed: 12 }),
  ).not.toThrow();
});

it('keeps fixed presets authoritative over a recorded configuration', () => {
  const parsed = parseTournamentConfig(
    {
      ...valid,
      players: [
        { profile_id: 'baseline', config: { algorithm: 'mcts', iteratons: 500, unknown: true } },
        { profile_id: 'alpha_beta', config: { algorithm: 'mcts', depth: 1 } },
      ],
    },
    profiles,
    models,
  );
  expect(parsed.config.players[0].config).toMatchObject({
    algorithm: 'baseline',
    time_limit: 1,
  });
  expect(parsed.config.players[0].config.iteratons).toBeUndefined();
  expect(parsed.config.players[1].config).toMatchObject({ algorithm: 'alpha_beta', depth: 5 });
});

it('rejects unknown fields inside the nested alpha-beta search', () => {
  expect(
    importFields({
      ...valid,
      players: [
        { config: { algorithm: 'alpha_beta', search: { unknown: true } } },
        valid.players[1],
      ],
    }),
    // The nested validator names the search object itself.
  ).toEqual(['search']);
});

it('imports an exported round-robin document and keeps its effective configuration', () => {
  const document = {
    format: 'hnefatafl-tournament-configuration',
    format_version: 1,
    name: 'Exported round robin',
    rulesets: ['brandubh'],
    players: [
      { config: { algorithm: 'mcts', iterations: 33, exploration: 1.5, time_limit: 7.5, seed: 4 } },
      {
        profile_id: 'baseline',
        config: { algorithm: 'baseline', time_limit: 1, seed: 4 },
      },
    ],
    games: 4,
    max_plies: 150,
    seed: 4,
    results: { status: 'completed', progress: { completed_games: 4, total_games: 4 } },
  };
  const parsed = parseTournamentConfig(document, profiles, models);
  expect(parsed.name).toBe('Exported round robin');
  expect(parsed.config.mode).toBe('round_robin');
  expect(parsed.config.players[0].config).toMatchObject({
    algorithm: 'mcts',
    iterations: 33,
    exploration: 1.5,
    time_limit: 7.5,
    seed: 4,
  });
  // A named fixed profile is snapshotted from the catalog, so the recorded
  // configuration (including its seed) is replaced, exactly as the API does.
  expect(parsed.config.players[1].config).toMatchObject({
    algorithm: 'baseline',
    time_limit: 1,
  });
  expect(parsed.config.players[1].config.seed).toBeUndefined();
});

it('imports an exported fixed-role document and keeps its effective configuration', () => {
  const document = {
    format: 'hnefatafl-tournament-configuration',
    format_version: 1,
    name: 'Exported fixed roles',
    mode: 'fixed_roles',
    rulesets: ['brandubh'],
    players: [
      {
        config: {
          algorithm: 'neural',
          model_id: 'model-brandubh',
          search_wrapper: 'mcts',
          iterations: 120,
          time_limit: 2,
          seed: 4,
        },
      },
      {
        config: {
          algorithm: 'neural',
          model_id: 'model-brandubh',
          search_wrapper: 'mcts',
          iterations: 120,
          time_limit: 2,
          seed: 4,
        },
      },
    ],
    games: 7,
    max_plies: 200,
    seed: 4,
    results: { status: 'completed' },
  };
  const parsed = parseTournamentConfig(document, profiles, models);
  expect(parsed.config.mode).toBe('fixed_roles');
  expect(parsed.config.games).toBe(7);
  expect(parsed.config.players[0].config).toMatchObject({
    algorithm: 'neural',
    model_id: 'model-brandubh',
    iterations: 120,
    time_limit: 2,
  });
});

it('rejects a malformed configuration container', () => {
  for (const config of ['mcts', 42, true, [], null]) {
    expect(importFields({ ...valid, players: [{ config }, valid.players[1]] })).toEqual([
      'players[0].config',
    ]);
  }
  // The second participant is named when it is the malformed one.
  expect(importFields({ ...valid, players: [valid.players[0], { config: 'baseline' }] })).toEqual([
    'players[1].config',
  ]);
  // A malformed container is rejected for a fixed preset too, because the API
  // request schema requires an object before the preset replaces its contents.
  expect(
    importFields({
      ...valid,
      players: [{ profile_id: 'baseline', config: 'baseline' }, valid.players[1]],
    }),
  ).toEqual(['players[0].config']);
});

it('rejects explicitly invalid algorithms instead of defaulting them', () => {
  for (const algorithm of [null, 42, true, [], {}, '']) {
    expect(
      importFields({ ...valid, players: [{ config: { algorithm } }, valid.players[1]] }),
    ).toEqual(['players[0].config.algorithm']);
  }
  expect(
    importFields({ ...valid, players: [{ config: { algorithm: 'unknown' } }, valid.players[1]] }),
  ).toEqual(['players[0].config.algorithm']);
  // The historical `alphabeta` alias is normalised, exactly as the API does.
  const aliased = parseTournamentConfig(
    { ...valid, players: [{ config: { algorithm: 'alphabeta', depth: 2 } }, valid.players[1]] },
    profiles,
    models,
  );
  expect(aliased.config.players[0].config).toMatchObject({ algorithm: 'alpha_beta', depth: 2 });
  // An invalid algorithm inside a configuration a fixed preset replaces is
  // discarded with the rest of that configuration, as the API does.
  const preset = parseTournamentConfig(
    {
      ...valid,
      players: [
        { profile_id: 'baseline', config: { algorithm: 'unknown', iterations: 1 } },
        valid.players[1],
      ],
    },
    profiles,
    models,
  );
  expect(preset.config.players[0].config).toMatchObject({ algorithm: 'baseline', time_limit: 1 });
});

it('keeps missing and empty configurations valid', () => {
  // A missing config keeps the profile or baseline default.
  const missing = parseTournamentConfig(
    {
      ...valid,
      players: [{ profile_id: 'mcts' }, { profile_id: 'baseline' }],
    },
    profiles,
    models,
  );
  expect(missing.config.players[0].config).toMatchObject({ algorithm: 'mcts' });
  expect(missing.config.players[1].config).toMatchObject({ algorithm: 'baseline' });
  const custom = parseTournamentConfig(
    { ...valid, players: [{ config: {} }, { config: { algorithm: 'baseline' } }] },
    profiles,
    models,
  );
  expect(custom.config.players[0].config).toMatchObject({ algorithm: 'baseline' });
});

it('keeps a model-backed MCTS or alpha-beta opponent', () => {
  const parseOne = (config: Record<string, unknown>) =>
    parseTournamentConfig(
      { ...valid, players: [{ config }, { config: { algorithm: 'baseline' } }] },
      profiles,
      models,
    ).config.players[0].config;
  // The server infers neural evaluation from a recorded model, so the model is
  // preserved with an omitted evaluation too.
  expect(
    parseOne({ algorithm: 'mcts', model_id: 'model-brandubh', iterations: 200, time_limit: 2 }),
  ).toMatchObject({
    algorithm: 'mcts',
    model_id: 'model-brandubh',
    evaluation: 'neural',
    iterations: 200,
    time_limit: 2,
  });
  expect(
    parseOne({ algorithm: 'alpha_beta', model_id: 'model-brandubh', depth: 3, time_limit: 2 }),
  ).toMatchObject({
    algorithm: 'alpha_beta',
    model_id: 'model-brandubh',
    evaluation: 'neural',
    depth: 3,
  });
  // An explicit neural evaluation is kept as recorded.
  expect(
    parseOne({ algorithm: 'mcts', model_id: 'model-brandubh', evaluation: 'neural' }),
  ).toMatchObject({ model_id: 'model-brandubh', evaluation: 'neural' });
  expect(
    parseOne({
      algorithm: 'alpha_beta',
      model_id: 'model-brandubh',
      evaluation: 'neural',
      depth: 2,
    }),
  ).toMatchObject({ model_id: 'model-brandubh', evaluation: 'neural' });
  // A model-backed search keeps its nested alpha-beta settings.
  expect(
    parseOne({
      algorithm: 'alpha_beta',
      model_id: 'model-brandubh',
      search: { transposition: false, move_ordering: true },
    }),
  ).toMatchObject({
    model_id: 'model-brandubh',
    search: { transposition: false, move_ordering: true, symmetry_pruning: false },
  });
});

it('keeps heuristic-only MCTS and alpha-beta without a model', () => {
  const parseOne = (config: Record<string, unknown>) =>
    parseTournamentConfig(
      { ...valid, players: [{ config }, { config: { algorithm: 'baseline' } }] },
      profiles,
      models,
    ).config.players[0].config;
  for (const algorithm of ['mcts', 'alpha_beta']) {
    const parsed = parseOne({ algorithm, time_limit: 1 });
    expect(parsed.algorithm).toBe(algorithm);
    expect(parsed.model_id).toBeUndefined();
    expect(parsed.evaluation).toBeUndefined();
  }
});

it('rejects a model reference the server would ignore', () => {
  // The server only honours a model for neural, MCTS and alpha-beta, so a
  // baseline or shield-maiden reference is refused instead of silently dropped.
  for (const algorithm of ['baseline', 'shield_maiden']) {
    expect(
      importFields({
        ...valid,
        players: [
          { config: { algorithm, model_id: 'model-brandubh' } },
          { config: { algorithm: 'baseline' } },
        ],
      }),
    ).toEqual(['players[0].config.model_id']);
  }
  for (const model_id of [7, true, ['model-brandubh'], {}, 'missing']) {
    expect(
      importFields({
        ...valid,
        players: [
          { config: { algorithm: 'mcts', model_id } },
          { config: { algorithm: 'baseline' } },
        ],
      }),
    ).toEqual(['players[0].config.model_id']);
  }
  // A model trained for another battlefield is still refused.
  expect(
    importFields({
      ...valid,
      players: [
        { config: { algorithm: 'mcts', model_id: 'model-tablut' } },
        { config: { algorithm: 'baseline' } },
      ],
    }),
  ).toEqual(['players[0].config.model_id']);
});

it('imports an exported model-backed opponent with its effective configuration', () => {
  const document = {
    format: 'hnefatafl-tournament-configuration',
    format_version: 1,
    name: 'Model backed export',
    rulesets: ['brandubh'],
    players: [
      {
        config: {
          algorithm: 'mcts',
          model_id: 'model-brandubh',
          evaluation: 'neural',
          iterations: 200,
          time_limit: 2,
          seed: 4,
        },
      },
      { config: { algorithm: 'baseline', time_limit: 1, seed: 4 } },
    ],
    games: 2,
    max_plies: 150,
    seed: 4,
    results: { status: 'completed' },
  };
  const parsed = parseTournamentConfig(document, profiles, models);
  expect(parsed.config.players[0].config).toMatchObject({
    algorithm: 'mcts',
    model_id: 'model-brandubh',
    evaluation: 'neural',
    iterations: 200,
    time_limit: 2,
  });
});

it('rejects coerced enumeration values instead of converting them', () => {
  const cases: [Record<string, unknown>, string][] = [
    [
      { algorithm: 'neural', model_id: 'model-brandubh', search_wrapper: ['mcts'] },
      'players[0].config.search_wrapper',
    ],
    [
      { algorithm: 'neural', model_id: 'model-brandubh', search_wrapper: 123 },
      'players[0].config.search_wrapper',
    ],
    [
      { algorithm: 'neural', model_id: 'model-brandubh', search_wrapper: null },
      'players[0].config.search_wrapper',
    ],
    [
      { algorithm: 'neural', model_id: 'model-brandubh', search_wrapper: { value: 'mcts' } },
      'players[0].config.search_wrapper',
    ],
    [{ algorithm: 'mcts', heuristic: ['shield_bearer'] }, 'players[0].config.heuristic'],
    [{ algorithm: 'mcts', heuristic: null }, 'players[0].config.heuristic'],
    [{ algorithm: 'mcts', heuristic: 1 }, 'players[0].config.heuristic'],
    [{ algorithm: 'mcts', heuristic: true }, 'players[0].config.heuristic'],
  ];
  for (const [config, field] of cases) {
    expect(
      importFields({ ...valid, players: [{ config }, { config: { algorithm: 'baseline' } }] }),
    ).toEqual([field]);
  }
  // Real strings keep working.
  const parsed = parseTournamentConfig(
    {
      ...valid,
      players: [
        {
          config: {
            algorithm: 'neural',
            model_id: 'model-brandubh',
            search_wrapper: 'none',
            temperature: 0.5,
          },
        },
        { config: { algorithm: 'baseline' } },
      ],
    },
    profiles,
    models,
  );
  expect(parsed.config.players[0].config).toMatchObject({
    search_wrapper: 'none',
    evaluation: 'neural',
    temperature: 0.5,
  });
});

it('rejects malformed battlefield values', () => {
  for (const rulesets of [
    [['brandubh']],
    [7],
    [null],
    [true],
    [{ id: 'brandubh' }],
    ['brandubh', 7],
  ]) {
    expect(importFields({ ...valid, rulesets })).toEqual(['rulesets']);
  }
  expect(
    parseTournamentConfig({ ...valid, rulesets: ['brandubh'] }, profiles, models).config.rulesets,
  ).toEqual(['brandubh']);
});

it('refuses neural symmetry reuse before a legacy search can hide it', () => {
  const neural = (search: Record<string, unknown>) => ({
    ...valid,
    players: [
      {
        config: {
          algorithm: 'neural',
          model_id: 'model-brandubh',
          search_wrapper: 'alpha_beta',
          search,
        },
      },
      { config: { algorithm: 'baseline' } },
    ],
  });
  for (const search of [
    { legacy: true, symmetry_pruning: true },
    { legacy: true, symmetry_transposition: true },
    { symmetry_pruning: true },
    { symmetry_transposition: true },
  ]) {
    expect(importFields(neural(search))).toEqual(['search']);
  }
  // A legacy search without symmetry is still accepted and collapsed.
  const legacy = parseTournamentConfig(
    neural({ legacy: true, move_ordering: false }),
    profiles,
    models,
  );
  expect(legacy.config.players[0].config.search).toEqual({
    ...DEFAULT_ALPHA_BETA_SEARCH,
    legacy: true,
  });
  // A model-backed alpha-beta configuration is refused the same way.
  expect(
    importFields({
      ...valid,
      players: [
        {
          config: {
            algorithm: 'alpha_beta',
            model_id: 'model-brandubh',
            search: { symmetry_pruning: true },
          },
        },
        { config: { algorithm: 'baseline' } },
      ],
    }),
  ).toEqual(['search']);
  // Heuristic-only alpha-beta keeps its symmetry options, and malformed nested
  // values are still rejected.
  const heuristic = parseTournamentConfig(
    {
      ...valid,
      players: [
        {
          config: {
            algorithm: 'alpha_beta',
            search: { symmetry_transposition: true, symmetry_pruning: true },
          },
        },
        { config: { algorithm: 'baseline' } },
      ],
    },
    profiles,
    models,
  );
  expect(heuristic.config.players[0].config.search).toMatchObject({
    symmetry_transposition: true,
    symmetry_pruning: true,
    transposition: true,
  });
  expect(
    importFields({
      ...valid,
      players: [
        { config: { algorithm: 'alpha_beta', search: { move_ordering: 'yes' } } },
        { config: { algorithm: 'baseline' } },
      ],
    }),
  ).toEqual(['search.move_ordering']);
});

it('round-trips independent 64-vs-256 fixed-role configurations with randomized openings', () => {
  const document = {
    ...fixedRoles,
    randomize_start_positions: true,
    players: [
      {
        config: {
          ...fixedRoles.players[0].config,
          iterations: 256,
          exploration: 2,
          time_limit: 3,
          temperature: 0,
        },
      },
      {
        config: {
          ...fixedRoles.players[1].config,
          iterations: 64,
          exploration: 1,
          time_limit: 0.1,
          temperature: 0.5,
        },
      },
    ],
  };
  const parsed = parseTournamentConfig(document, profiles, models);
  expect(parsed.config.randomize_start_positions).toBe(true);
  document.players.forEach((player, index) =>
    expect(parsed.config.players[index].config).toMatchObject(player.config),
  );
  expect(
    parseTournamentConfig(JSON.parse(JSON.stringify(parsed.config)), profiles, models).config,
  ).toEqual(parsed.config);
  expect(parseTournamentConfig(valid, profiles, models).config.randomize_start_positions).toBe(
    false,
  );
});

it('accepts supported heuristic opponents in a fixed-role configuration', () => {
  const parsed = parseTournamentConfig(
    {
      ...fixedRoles,
      players: [
        fixedRoles.players[0],
        { config: { algorithm: 'mcts', heuristic: 'shield_bearer', iterations: 10 } },
      ],
    },
    profiles,
    models,
  );
  expect(parsed.config.players[1].config).toMatchObject({
    algorithm: 'mcts',
    heuristic: 'shield_bearer',
    iterations: 10,
  });
});

it.each([null, 1, 'true', [], {}])(
  'rejects a nonboolean opening mode %j',
  (randomize_start_positions) => {
    expect(() =>
      parseTournamentConfig({ ...valid, randomize_start_positions }, profiles, models),
    ).toThrow(TournamentConfigImportError);
  },
);
