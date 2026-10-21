const givenNames = [
  'Astrid',
  'Bjorn',
  'Eirik',
  'Freydis',
  'Gunnar',
  'Helga',
  'Ingrid',
  'Leif',
  'Ragnar',
  'Sigrid',
  'Sven',
  'Thora',
  'Ulf',
  'Yrsa',
  'Runa',
  'Harald',
];
const epithets = [
  'Ravenwise',
  'Stormshield',
  'Oakheart',
  'Frostwalker',
  'Ironwill',
  'Seafarer',
  'Wolfguard',
  'Runekeeper',
  'Emberhelm',
  'Northstar',
  'Silveroak',
  'Dawnseeker',
];

/** Non-secret display names; avoid existing names and the current suggestion. */
export function suggestVikingName(existing: string[], random: () => number = Math.random): string {
  const used = new Set(existing.map((name) => name.trim().toLocaleLowerCase()));
  const available = givenNames
    .flatMap((name) => epithets.map((epithet) => `${name} ${epithet}`))
    .filter((name) => !used.has(name.toLocaleLowerCase()));
  if (available.length)
    return available[Math.min(available.length - 1, Math.floor(random() * available.length))];
  let suffix = 2;
  while (used.has(`Runa Runekeeper ${suffix}`.toLocaleLowerCase())) suffix++;
  return `Runa Runekeeper ${suffix}`;
}
