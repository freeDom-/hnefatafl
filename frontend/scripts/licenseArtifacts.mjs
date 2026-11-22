import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const publicDirectory = join(frontend, 'public');

const LICENSE_FILE_PATTERN = /^(licen[cs]e|copying|notice)(?:[._-]|$)/i;

/**
 * Package-lock v3 marks the runtime closure with `dev: false` (or no `dev`
 * field). Keep only top-level installed entries; nested duplicate versions are
 * represented by their top-level package. This intentionally over-includes
 * transitive type-only packages, which is safer than omitting a notice.
 */
export function runtimePackages(lock) {
  const packages = lock?.packages;
  if (!packages || typeof packages !== 'object') {
    throw new Error('frontend/package-lock.json has no packages object');
  }
  return Object.entries(packages)
    .filter(([path, info]) => {
      if (!/^node_modules\/(?:@[^/]+\/)?[^/]+$/.test(path)) return false;
      return info?.dev !== true && info?.link !== true;
    })
    .map(([path, info]) => ({
      name: path.slice('node_modules/'.length),
      version: String(info.version ?? ''),
      declaredLicense: String(info.license ?? ''),
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

async function licenseFiles(packageDirectory) {
  let entries;
  try {
    entries = await readdir(packageDirectory, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => entry.isFile() && LICENSE_FILE_PATTERN.test(entry.name))
    .map((entry) => entry.name)
    .sort();
}

async function packageDetails(packageEntry) {
  const packageDirectory = join(frontend, 'node_modules', packageEntry.name);
  let metadata = {};
  try {
    metadata = JSON.parse(await readFile(join(packageDirectory, 'package.json'), 'utf8'));
  } catch {
    // The lockfile is authoritative; the upstream package metadata is only a
    // fallback when the installed tree is incomplete.
  }
  const files = await licenseFiles(packageDirectory);
  const texts = [];
  for (const file of files) {
    const text = (await readFile(join(packageDirectory, file), 'utf8')).trim();
    if (text) texts.push({ file, text });
  }
  return {
    ...packageEntry,
    license:
      String(metadata.license ?? '') || packageEntry.declaredLicense || 'License text unavailable',
    repository:
      typeof metadata.repository === 'string'
        ? metadata.repository
        : String(metadata.repository?.url ?? ''),
    texts,
  };
}

function renderNotices(packages) {
  const lines = [
    'Hnefatafl third-party licenses',
    '',
    'Generated from the installed frontend runtime packages selected by',
    'frontend/package-lock.json. These texts are provided for redistribution',
    'notice compliance. The project license in LICENSE applies only to',
    'first-party Hnefatafl material, not to these packages.',
    '',
    'See THIRD_PARTY_NOTICES.txt and the repository THIRD_PARTY_NOTICES.md for the',
    'inventory and the project licensing scope.',
    '',
  ];
  for (const entry of packages) {
    lines.push(`===== ${entry.name}${entry.version ? `@${entry.version}` : ''} =====`);
    lines.push(`License: ${entry.license}`);
    if (entry.repository) lines.push(`Source: ${entry.repository}`);
    if (!entry.texts.length) {
      lines.push('No standalone license file was present in the installed package.');
      lines.push('The package metadata and source repository govern this package.');
    }
    for (const { file, text } of entry.texts) {
      lines.push('', `--- ${file} ---`, text);
    }
    lines.push('', '');
  }
  return lines.join('\n');
}

export async function writeLicenseArtifacts() {
  await mkdir(publicDirectory, { recursive: true });
  await copyFile(join(repository, 'LICENSE'), join(publicDirectory, 'LICENSE.txt'));
  await copyFile(
    join(repository, 'THIRD_PARTY_NOTICES.md'),
    join(publicDirectory, 'THIRD_PARTY_NOTICES.txt'),
  );

  const lock = JSON.parse(await readFile(join(frontend, 'package-lock.json'), 'utf8'));
  const entries = runtimePackages(lock);
  const packages = [];
  for (const entry of entries) packages.push(await packageDetails(entry));
  const missing = packages.filter((entry) => !entry.texts.length);
  if (missing.length) {
    console.warn(
      `No standalone license file found for: ${missing.map((entry) => entry.name).join(', ')}`,
    );
  }
  await writeFile(join(publicDirectory, 'THIRD_PARTY_LICENSES.txt'), renderNotices(packages));
  console.log(
    `Wrote LICENSE.txt, THIRD_PARTY_NOTICES.txt and ${packages.length} package license records.`,
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await writeLicenseArtifacts();
}
