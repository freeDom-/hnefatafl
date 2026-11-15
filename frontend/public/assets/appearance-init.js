/* BEGIN GENERATED APPEARANCE CATALOG */
const UI_THEME_CATALOG = {
  defaultTheme: 'kings-table',
  legacyModes: {
    light: 'classic-table',
    dark: 'iron-ash',
    wood: 'saga-hall',
    system: 'kings-table',
  },
  largeViewport: '(min-width: 3000px) and (min-height: 1600px)',
  themes: { 'kings-table': true, 'iron-ash': true, 'saga-hall': true, 'classic-table': true },
};
/* END GENERATED APPEARANCE CATALOG */
// Synchronous, external and CSP-compatible: match React before the first frame.
try {
  const raw = JSON.parse(localStorage.getItem('hnefatafl-appearance') || 'null');
  const saved = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const named =
    typeof saved.uiTheme === 'string' &&
    Object.prototype.hasOwnProperty.call(UI_THEME_CATALOG.themes, saved.uiTheme);
  const old = saved.uiTheme == null || saved.uiTheme === 'legacy';
  const mapped =
    typeof saved.mode === 'string' &&
    Object.prototype.hasOwnProperty.call(UI_THEME_CATALOG.legacyModes, saved.mode)
      ? UI_THEME_CATALOG.legacyModes[saved.mode]
      : UI_THEME_CATALOG.defaultTheme;
  document.documentElement.dataset.uiTheme = named
    ? saved.uiTheme
    : old
      ? mapped
      : UI_THEME_CATALOG.defaultTheme;
  const large = window.matchMedia && window.matchMedia(UI_THEME_CATALOG.largeViewport).matches;
  document.documentElement.dataset.density = ['compact', 'default', 'large'].includes(saved.density)
    ? saved.density
    : large
      ? 'large'
      : 'default';
  delete document.documentElement.dataset.colorMode;
} catch {
  document.documentElement.dataset.uiTheme = UI_THEME_CATALOG.defaultTheme;
  document.documentElement.dataset.density =
    window.matchMedia && window.matchMedia(UI_THEME_CATALOG.largeViewport).matches
      ? 'large'
      : 'default';
  delete document.documentElement.dataset.colorMode;
}
