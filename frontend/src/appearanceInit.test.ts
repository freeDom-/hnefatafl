import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import {
  DEFAULT_UI_THEME,
  LARGE_VIEWPORT_QUERY,
  UI_THEMES,
  parseAppearance,
  effectiveDensity,
} from './appearanceConfig';
const source = readFileSync('public/assets/appearance-init.js', 'utf8');
function firstPaint(value: string | null, large = false) {
  const root = document.createElement('html');
  new Function('document', 'localStorage', 'window', source)(
    { documentElement: root },
    { getItem: () => value },
    { matchMedia: (q: string) => ({ matches: q === LARGE_VIEWPORT_QUERY && large }) },
  );
  return root;
}
it('uses the same catalog and viewport query as React', () => {
  const c = new Function(
    source.split('/* END GENERATED APPEARANCE CATALOG */')[0] + ';return UI_THEME_CATALOG;',
  )();
  expect(Object.keys(c.themes)).toEqual(UI_THEMES.map((t) => t.id));
  expect(c.defaultTheme).toBe(DEFAULT_UI_THEME);
  expect(c.largeViewport).toBe(LARGE_VIEWPORT_QUERY);
});
const cases = [
  ...UI_THEMES.map((t) => ({ uiTheme: t.id })),
  ...['legacy', undefined].flatMap((uiTheme) =>
    ['light', 'dark', 'wood', 'system'].map((mode) => ({ uiTheme, mode })),
  ),
  { uiTheme: 'unknown' },
  { uiTheme: ['classic-table'] },
  { uiTheme: '__proto__' },
  null,
  [],
  42,
];
it.each(cases)(
  'matches React before mount and repeated reload, including migration: %j',
  (value) => {
    for (const large of [false, true])
      for (const density of [undefined, 'compact', 'default', 'large']) {
        const saved =
          value && typeof value === 'object' && !Array.isArray(value)
            ? { ...value, tokens: 'classic', board: 'slate', density }
            : value;
        const text = JSON.stringify(saved);
        const parsed = parseAppearance(saved);
        for (let reload = 0; reload < 2; reload++) {
          const root = firstPaint(text, large);
          expect(root.dataset.uiTheme).toBe(parsed.uiTheme);
          expect(root.dataset.density).toBe(effectiveDensity(parsed, large));
          expect(root.dataset.colorMode).toBeUndefined();
        }
      }
  },
);
it('recovers invalid JSON with viewport defaults and preserves the external blocking CSP bootstrap', () => {
  expect(firstPaint('{', true).dataset.density).toBe('large');
  expect(firstPaint('{').dataset.uiTheme).toBe('kings-table');
  const html = readFileSync('index.html', 'utf8');
  expect(html).toContain('<script src="/assets/appearance-init.js"></script>');
  expect(html.indexOf('/assets/appearance-init.js')).toBeLessThan(html.indexOf('/src/main.tsx'));
});
