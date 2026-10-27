// Apply the saved/device color mode before the first painted frame.
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
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
} catch {
  const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.colorMode = dark ? 'dark' : 'light';
  document.documentElement.dataset.density = 'default';
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
}
