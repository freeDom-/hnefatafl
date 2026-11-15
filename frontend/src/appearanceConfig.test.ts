import { expect, it } from 'vitest';
import {
  DEFAULT_APPEARANCE,
  UI_THEMES,
  parseAppearance,
  effectiveDensity,
} from './appearanceConfig';
it('lists only the four approved themes in picker order', () =>
  expect(UI_THEMES.map((x) => x.id)).toEqual([
    'kings-table',
    'iron-ash',
    'saga-hall',
    'classic-table',
  ]));
it.each([
  ['light', 'classic-table'],
  ['dark', 'iron-ash'],
  ['wood', 'saga-hall'],
  ['system', 'kings-table'],
])('idempotently migrates legacy %s to %s', (mode, uiTheme) => {
  for (const legacy of [{ mode }, { mode, uiTheme: 'legacy' }]) {
    const parsed = parseAppearance({
      ...legacy,
      tokens: 'classic',
      board: 'oak',
      density: 'large',
    });
    expect(parsed).toEqual({
      version: 2,
      uiTheme,
      tokens: 'classic',
      board: 'oak',
      density: 'large',
    });
    expect(parseAppearance(parsed)).toEqual(parsed);
  }
});
it.each(UI_THEMES)('keeps named $id ahead of obsolete modes', (theme) =>
  expect(parseAppearance({ uiTheme: theme.id, mode: 'wood' })).toEqual({
    ...DEFAULT_APPEARANCE,
    uiTheme: theme.id,
  }),
);
it.each([
  null,
  [],
  42,
  'x',
  { uiTheme: 'unknown' },
  { uiTheme: ['saga-hall'] },
  { uiTheme: '__proto__' },
  { tokens: {}, board: '../x', density: 'wrong' },
])('defaults invalid fields safely: %j', (value) =>
  expect(parseAppearance(value)).toEqual(DEFAULT_APPEARANCE),
);
it('distinguishes automatic from explicit density', () => {
  expect(effectiveDensity(parseAppearance(null), false)).toBe('default');
  expect(effectiveDensity(parseAppearance(null), true)).toBe('large');
  for (const density of ['compact', 'default', 'large'] as const)
    expect(effectiveDensity(parseAppearance({ density }), true)).toBe(density);
});
