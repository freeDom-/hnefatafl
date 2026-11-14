import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { UI_THEMES, DEFAULT_UI_THEME, parseAppearance, resolveUiTheme } from './appearanceConfig';
const source = readFileSync('public/assets/appearance-init.js', 'utf8');
function firstPaint(value: string | null, dark = true) {
  const root = document.createElement('html');
  const storage = { getItem: () => value };
  new Function('document', 'localStorage', 'window', source)({ documentElement: root }, storage, {
    matchMedia: () => ({ matches: dark }),
  });
  return root;
}
it('keeps the generated synchronous bootstrap synchronized with the canonical catalog', () => {
  const bootstrap = new Function(
    source.split('/* END GENERATED APPEARANCE CATALOG */')[0] + '\nreturn UI_THEME_CATALOG;',
  )();
  expect(Object.keys(bootstrap.themes)).toEqual(UI_THEMES.map((theme) => theme.id));
  expect(bootstrap.defaultTheme).toBe(DEFAULT_UI_THEME);
  expect(bootstrap.legacyTheme).toBe('legacy');
});
it.each([...UI_THEMES.map((theme) => theme.id), 'legacy'])(
  'applies saved %s before React and after reload without changing independent preferences',
  (id) => {
    const stored = JSON.stringify({
      uiTheme: id,
      mode: 'wood',
      tokens: 'classic',
      board: 'slate',
      density: 'large',
    });
    for (let reload = 0; reload < 2; reload++) {
      const root = firstPaint(stored);
      expect(root.dataset.uiTheme).toBe(id === 'legacy' ? undefined : id);
      expect(root.dataset.colorMode).toBe('wood');
      expect(root.dataset.density).toBe('large');
      const parsed = parseAppearance(JSON.parse(stored));
      expect(resolveUiTheme(parsed)).toBe(id);
      expect(parsed).toMatchObject({
        tokens: 'classic',
        board: 'slate',
        density: 'large',
        mode: 'wood',
      });
    }
  },
);
it.each([
  '{"uiTheme":["classic-table"]}',
  '{"uiTheme":{"id":"classic-table"}}',
  '{"uiTheme":true}',
  null,
  '{',
  'null',
  '[]',
  '42',
  '{"uiTheme":"unknown"}',
  '{"uiTheme":"__proto__"}',
])('uses the default and device color mode for absent/invalid storage %s', (value) => {
  expect(firstPaint(value).dataset.uiTheme).toBe(DEFAULT_UI_THEME);
  expect(firstPaint(value).dataset.colorMode).toBe('dark');
  expect(firstPaint(value, false).dataset.colorMode).toBe('light');
});

it('retains the external synchronous initializer before the React entry without inline bootstrap code', () => {
  const html = readFileSync('index.html', 'utf8');
  expect(html).toContain('<script src="/assets/appearance-init.js"></script>');
  expect(html.indexOf('/assets/appearance-init.js')).toBeLessThan(html.indexOf('/src/main.tsx'));
});
