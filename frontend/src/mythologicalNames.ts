const patrons = [
  'Odin’s',
  'Freyja’s',
  'Thor’s',
  'Tyr’s',
  'Skadi’s',
  'Heimdall’s',
  'Baldr’s',
  'Njord’s',
  'Idunn’s',
  'Frigg’s',
  'Huginn’s',
  'Muninn’s',
];
const gatherings = [
  'Challenge',
  'Tourney',
  'Moot',
  'Trial',
  'Saga',
  'Contest',
  'Assembly',
  'Gambit',
];
const names = patrons.flatMap((patron) => gatherings.map((gathering) => `${patron} ${gathering}`));
const germanNames = [
  'Odins',
  'Freyjas',
  'Thors',
  'Tyrs',
  'Skadis',
  'Heimdalls',
  'Baldrs',
  'Njörds',
  'Idunns',
  'Friggs',
  'Hugins',
  'Munins',
].flatMap((patron) =>
  [
    'Herausforderung',
    'Turnier',
    'Thing',
    'Prüfung',
    'Saga',
    'Wettstreit',
    'Versammlung',
    'Gambit',
  ].map((gathering) => `${patron} ${gathering}`),
);
const normalize = (name: string) => name.trim().normalize('NFKC').toLocaleLowerCase();

function displayRandom(): number {
  if (globalThis.crypto?.getRandomValues) {
    const value = new Uint32Array(1);
    globalThis.crypto.getRandomValues(value);
    return value[0] / 2 ** 32;
  }
  return Math.random();
}

/** Display names only: avoid saved titles and the current/previous suggestion. */
export function suggestTournamentName(
  existing: string[],
  random: () => number = displayRandom,
  language: 'en' | 'de' = 'en',
): string {
  const used = new Set(existing.map(normalize));
  const available = (language === 'de' ? germanNames : names).filter(
    (name) => !used.has(normalize(name)),
  );
  if (available.length) {
    const sample = random();
    const index = Number.isFinite(sample)
      ? Math.max(0, Math.min(available.length - 1, Math.floor(sample * available.length)))
      : 0;
    return available[index];
  }
  let suffix = 2;
  const fallback = language === 'de' ? 'Yggdrasil-Treffen' : 'Yggdrasil Gathering';
  while (used.has(normalize(`${fallback} ${suffix}`))) suffix++;
  return `${fallback} ${suffix}`;
}
