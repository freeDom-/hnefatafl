import { describe, expect, it } from 'vitest';
import { modelOptionLabel } from './modelLabel';
import { suggestTournamentName } from './mythologicalNames';
import type { Model } from './types';

describe('model descriptions and northern tournament names', () => {
  it('distinguishes saved training versions and keeps imported model details honest', () => {
    const model: Model = {
      id: 'abcdef0123456789',
      name: 'Eir',
      ruleset: 'tablut',
      version: 4,
      training_config: { iterations: 40, channels: 48, blocks: 3 },
    };
    expect(modelOptionLabel(model)).toBe(
      'Eir · iteration 4/40 · 48 channels · 3 blocks · #abcdef01',
    );
    expect(modelOptionLabel({ ...model, version: 'external', training_config: undefined })).toBe(
      'Eir · version external · #abcdef01',
    );
    expect(modelOptionLabel({ ...model, version: undefined, training_config: undefined })).toBe(
      'Eir · #abcdef01',
    );
  });

  it('excludes normalized saved titles and the current suggestion, including a full catalogue', () => {
    const first = suggestTournamentName([], () => 0);
    const second = suggestTournamentName([`  ${first.toUpperCase()}  `], () => 0);
    expect(second).not.toBe(first);
    const used: string[] = [];
    for (let i = 0; i < 96; i++) used.push(suggestTournamentName(used, () => 0));
    expect(new Set(used).size).toBe(96);
    expect(suggestTournamentName(used, () => 0)).toBe('Yggdrasil Gathering 2');
    expect(suggestTournamentName([...used, 'Yggdrasil Gathering 2'], () => 0)).toBe(
      'Yggdrasil Gathering 3',
    );
    expect(suggestTournamentName([], () => 0, 'de')).toBe('Odins Herausforderung');
  });
});
