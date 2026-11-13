// Apply the saved/device color mode and UI theme before the first painted frame.
try {
  const saved = JSON.parse(localStorage.getItem('hnefatafl-appearance') || 'null');
  const mode =
    saved && ['light', 'dark', 'wood', 'system'].includes(saved.mode) ? saved.mode : 'system';
  const dark =
    mode === 'dark' ||
    mode === 'wood' ||
    (mode === 'system' &&
      window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.colorMode = mode === 'wood' ? 'wood' : dark ? 'dark' : 'light';
  document.documentElement.dataset.density =
    saved && ['compact', 'default', 'large'].includes(saved.density) ? saved.density : 'default';
  // Mirror `appearanceConfig.ts`: the catalog default paints until another theme is
  // chosen, and only the explicit legacy option leaves `data-ui-theme` unset.
  const uiTheme =
    saved && ['kings-table', 'iron-ash', 'saga-hall'].includes(saved.uiTheme)
      ? saved.uiTheme
      : saved && saved.uiTheme === 'legacy'
        ? null
        : 'kings-table';
  if (uiTheme) document.documentElement.dataset.uiTheme = uiTheme;
  // A named theme owns `color-scheme` through `arena-themes.css`.
  if (!uiTheme) document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
} catch {
  const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.colorMode = dark ? 'dark' : 'light';
  document.documentElement.dataset.density = 'default';
  document.documentElement.dataset.uiTheme = 'kings-table';
}
