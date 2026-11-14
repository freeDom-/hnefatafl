import catalog from './appearanceCatalog.json';
/** Named app-shell themes. The catalog default is applied when no theme was chosen. */
export const UI_THEMES = (Object.keys(catalog.themes) as (keyof typeof catalog.themes)[]).map(
  (id) => ({ id, ...catalog.themes[id] }),
);

export const TOKEN_THEMES = [
  { id: 'classic', name: 'Classic stones', description: 'Smooth dark, ivory and gold pieces' },
  { id: 'viking', name: 'Viking figures', description: 'Shield bearers and a crowned king' },
  { id: 'wood', name: 'Carved wood', description: 'Weathered grain and hand-carved runes' },
] as const;

export const DENSITIES = [
  { id: 'compact', name: 'Compact', description: 'Tighter spacing for more on screen' },
  { id: 'default', name: 'Default', description: 'Balanced spacing and text' },
  { id: 'large', name: 'Large', description: 'Roomier spacing for relaxed reading' },
] as const;

export const BOARD_THEMES = [
  {
    id: 'oak',
    name: 'Golden oak',
    description: 'Warm, familiar honeyed wood',
    palette: {
      light: '#e4d3ac',
      dark: '#d3bd92',
      specialLight: '#cbb88e',
      specialDark: '#c6af82',
      frame: '#b69b6d',
      rim: '#a78a5b',
      inner: '#baa275',
      edge: '#9e875e',
      rune: '#8a6b3d',
      selected: '#9da77b',
      selectRing: '#536b3e',
      last: '#5b793b38',
      legal: '#375726c9',
    },
  },
  {
    id: 'walnut',
    name: 'Old walnut',
    description: 'Deep timber with a worn bronze edge',
    palette: {
      light: '#b69570',
      dark: '#92714f',
      specialLight: '#a2825d',
      specialDark: '#80603f',
      frame: '#624a34',
      rim: '#4d3a29',
      inner: '#816244',
      edge: '#54402e',
      rune: '#523a24',
      selected: '#9bab77',
      selectRing: '#37532b',
      last: '#d6ca7e55',
      legal: '#193824e8',
    },
  },
  {
    id: 'slate',
    name: 'Fjord slate',
    description: 'Cool stone, silver and northern blue',
    palette: {
      light: '#c4d3d5',
      dark: '#94adb3',
      specialLight: '#a9bfc4',
      specialDark: '#809fa7',
      frame: '#59747d',
      rim: '#3f5d68',
      inner: '#76949c',
      edge: '#496d77',
      rune: '#486670',
      selected: '#aec49a',
      selectRing: '#365b43',
      last: '#ecdfa855',
      legal: '#1d493acb',
    },
  },
] as const;

export type TokenTheme = (typeof TOKEN_THEMES)[number]['id'];
export type BoardTheme = (typeof BOARD_THEMES)[number]['id'];
export type Density = (typeof DENSITIES)[number]['id'];
export type UiTheme = keyof typeof catalog.themes;
export interface Appearance {
  version: 2;
  uiTheme: UiTheme;
  tokens: TokenTheme;
  board: BoardTheme;
  /** Absent means viewport-aware automatic scale; a valid saved value is explicit. */
  density?: Density;
}
export const APPEARANCE_KEY = 'hnefatafl-appearance';
export const LARGE_VIEWPORT_QUERY = catalog.largeViewport;
export const DEFAULT_UI_THEME = catalog.defaultTheme as UiTheme;
export const DEFAULT_APPEARANCE: Appearance = {
  version: 2,
  uiTheme: DEFAULT_UI_THEME,
  tokens: 'wood',
  board: 'walnut',
};
export const resolveUiTheme = (appearance: Appearance): UiTheme => appearance.uiTheme;
export const effectiveDensity = (appearance: Appearance, largeViewport: boolean): Density =>
  appearance.density ?? (largeViewport ? 'large' : 'default');

/** Idempotent migration: named themes win, legacy modes map without device dependence. */
export function parseAppearance(value: unknown): Appearance {
  const candidate =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const named = UI_THEMES.some((theme) => theme.id === candidate.uiTheme);
  const old = candidate.uiTheme == null || candidate.uiTheme === 'legacy';
  const legacy =
    typeof candidate.mode === 'string' &&
    Object.prototype.hasOwnProperty.call(catalog.legacyModes, candidate.mode)
      ? (catalog.legacyModes[candidate.mode as keyof typeof catalog.legacyModes] as UiTheme)
      : DEFAULT_UI_THEME;
  const density = DENSITIES.some((item) => item.id === candidate.density)
    ? (candidate.density as Density)
    : undefined;
  return {
    version: 2,
    uiTheme: named ? (candidate.uiTheme as UiTheme) : old ? legacy : DEFAULT_UI_THEME,
    tokens: TOKEN_THEMES.some((theme) => theme.id === candidate.tokens)
      ? (candidate.tokens as TokenTheme)
      : DEFAULT_APPEARANCE.tokens,
    board: BOARD_THEMES.some((theme) => theme.id === candidate.board)
      ? (candidate.board as BoardTheme)
      : DEFAULT_APPEARANCE.board,
    ...(density ? { density } : {}),
  };
}
