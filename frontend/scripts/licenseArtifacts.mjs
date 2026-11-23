import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const publicDirectory = join(frontend, 'public');

const LICENSE_FILE_PATTERN = /^(licen[cs]e|copying|notice)(?:[._-]|$)/i;
const PACKAGE_PREFIX = 'node_modules/';

function packageNameFromPath(path) {
  return path.slice(path.lastIndexOf(PACKAGE_PREFIX) + PACKAGE_PREFIX.length);
}

function declaredLicense(info) {
  const value = info?.license;
  if (typeof value === 'string') return value.trim();
  if (Array.isArray(value)) return value.filter(Boolean).join(' OR ');
  if (value && typeof value === 'object') return String(value.type ?? '').trim();
  return '';
}

function parentPackagePath(path) {
  const separator = path.lastIndexOf('/node_modules/');
  return separator === -1 ? '' : path.slice(0, separator);
}

/** Resolve a dependency the way Node does, using the lockfile's installed paths. */
export function resolveInstalledPath(packages, fromPath, dependency) {
  let current = fromPath;
  while (true) {
    const candidate = current
      ? `${current}/node_modules/${dependency}`
      : `${PACKAGE_PREFIX}${dependency}`;
    if (packages[candidate]) return candidate;
    if (!current) return null;
    current = parentPackagePath(current);
  }
}

/**
 * Walk only the production closure rooted at package.json dependencies.
 * Nested packages are resolved by their actual installed path, so distinct
 * versions remain distinct. Type-only packages are recorded but not traversed;
 * development-only packages are excluded from the runtime inventory.
 */
export function resolveRuntimePackages(lock, { isInstalled = () => true } = {}) {
  const packages = lock?.packages;
  if (!packages || typeof packages !== 'object') {
    throw new Error('frontend/package-lock.json has no packages object');
  }
  const root = packages[''];
  if (!root) throw new Error('frontend/package-lock.json has no root package entry');

  const runtime = new Map();
  const typeOnly = new Map();
  const missingOptional = new Set();
  const queue = [];
  const enqueue = (fromPath, name, optional) => {
    if (name) queue.push({ fromPath, name, optional });
  };
  for (const name of Object.keys(root.dependencies ?? {})) enqueue('', name, false);
  for (const name of Object.keys(root.optionalDependencies ?? {})) enqueue('', name, true);

  while (queue.length) {
    const { fromPath, name, optional } = queue.shift();
    const path = resolveInstalledPath(packages, fromPath, name);
    if (!path) {
      if (optional) missingOptional.add(name);
      else throw new Error(`Missing lockfile package for required dependency "${name}"`);
      continue;
    }
    const info = packages[path];
    if (info.dev === true) continue;
    if (!isInstalled(path)) {
      if (optional) missingOptional.add(path);
      else throw new Error(`Required package is not installed: ${path}`);
      continue;
    }
    if (name.startsWith('@types/') || packageNameFromPath(path).startsWith('@types/')) {
      typeOnly.set(path, {
        path,
        name: packageNameFromPath(path),
        version: String(info.version ?? ''),
        declaredLicense: declaredLicense(info),
      });
      continue;
    }
    if (runtime.has(path)) continue;
    runtime.set(path, {
      path,
      name: packageNameFromPath(path),
      version: String(info.version ?? ''),
      declaredLicense: declaredLicense(info),
    });
    for (const dependency of Object.keys(info.dependencies ?? {})) enqueue(path, dependency, false);
    for (const dependency of Object.keys(info.optionalDependencies ?? {})) {
      enqueue(path, dependency, true);
    }
    for (const dependency of Object.keys(info.peerDependencies ?? {})) {
      const peerOptional = info.peerDependenciesMeta?.[dependency]?.optional === true;
      enqueue(path, dependency, peerOptional);
    }
  }

  const byPath = (left, right) => left.path.localeCompare(right.path);
  return {
    runtime: [...runtime.values()].sort(byPath),
    typeOnly: [...typeOnly.values()].sort(byPath),
    developmentCount: Object.values(packages).filter((info) => info?.dev === true).length,
    missingOptional: [...missingOptional].sort(),
  };
}

async function licenseFileNames(packageDirectory) {
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

async function packageDetails(packageEntry, frontendDirectory, problems) {
  const packageDirectory = join(frontendDirectory, packageEntry.path);
  let metadata;
  try {
    metadata = JSON.parse(await readFile(join(packageDirectory, 'package.json'), 'utf8'));
  } catch {
    problems.push(`Missing package metadata: ${packageEntry.path}`);
    return null;
  }
  const files = await licenseFileNames(packageDirectory);
  if (!files.length) {
    problems.push(`Missing license text file: ${packageEntry.path}`);
  }
  const license = String(metadata.license ?? '').trim() || packageEntry.declaredLicense;
  if (!license) problems.push(`Missing license identifier: ${packageEntry.path}`);
  const texts = [];
  for (const file of files) {
    let text;
    try {
      text = await readFile(join(packageDirectory, file), 'utf8');
    } catch {
      problems.push(`Unreadable license text: ${packageEntry.path}/${file}`);
      continue;
    }
    text = text.replace(/\r\n/g, '\n').trim();
    if (!text) problems.push(`Empty license text: ${packageEntry.path}/${file}`);
    else texts.push({ file, text });
  }
  return {
    ...packageEntry,
    name: String(metadata.name ?? packageEntry.name),
    version: String(metadata.version ?? packageEntry.version),
    license: license || 'License identifier unavailable',
    repository:
      typeof metadata.repository === 'string'
        ? metadata.repository
        : String(metadata.repository?.url ?? ''),
    texts,
  };
}

function label(entry) {
  return `${entry.name}@${entry.version}`;
}

/** Render deterministic notices, repeating each distinct license text only once. */
export function renderThirdPartyLicenses(packages, counts) {
  const lines = [
    'Hnefatafl third-party licenses',
    '',
    'Generated offline from frontend/package-lock.json and the installed runtime',
    'packages. These texts are provided for redistribution notice compliance. The',
    'project license in LICENSE applies only to first-party Hnefatafl material.',
    '',
    `Runtime packages: ${counts.runtime}`,
    `Type-only packages not distributed: ${counts.typeOnly}`,
    `Development-only packages skipped: ${counts.development}`,
    `Optional packages not installed: ${counts.optionalMissing}`,
    '',
    'See THIRD_PARTY_NOTICES.txt and the repository THIRD_PARTY_NOTICES.md for the',
    'inventory and the project licensing scope.',
    '',
  ];
  const seenText = new Map();
  for (const entry of [...packages].sort((left, right) => left.path.localeCompare(right.path))) {
    lines.push(`===== ${label(entry)} =====`);
    lines.push(`Path: ${entry.path}`);
    lines.push(`License: ${entry.license}`);
    if (entry.repository) lines.push(`Source: ${entry.repository}`);
    for (const { file, text } of entry.texts) {
      const digest = createHash('sha256').update(text).digest('hex');
      const previous = seenText.get(digest);
      lines.push('', `--- ${file} ---`);
      if (previous) {
        lines.push(`Identical text already included for ${previous}; not repeated.`);
      } else {
        seenText.set(digest, `${label(entry)} (${file})`);
        lines.push(text);
      }
    }
    lines.push('', '');
  }
  return lines.join('\n');
}

export async function generateThirdPartyLicenses({ frontendDirectory = frontend } = {}) {
  const lock = JSON.parse(await readFile(join(frontendDirectory, 'package-lock.json'), 'utf8'));
  const closure = resolveRuntimePackages(lock, {
    isInstalled: (path) => existsSync(join(frontendDirectory, path, 'package.json')),
  });
  const problems = [];
  const packages = [];
  for (const entry of closure.runtime) {
    const details = await packageDetails(entry, frontendDirectory, problems);
    if (details) packages.push(details);
  }
  if (problems.length) {
    throw new Error(`Incomplete third-party license inventory:\n- ${problems.join('\n- ')}`);
  }
  const counts = {
    runtime: packages.length,
    typeOnly: closure.typeOnly.length,
    development: closure.developmentCount,
    optionalMissing: closure.missingOptional.length,
  };
  return { text: renderThirdPartyLicenses(packages, counts), counts };
}

export async function writeLicenseArtifacts({
  frontendDirectory = frontend,
  repositoryDirectory = repository,
  outputDirectory = join(frontendDirectory, 'public'),
} = {}) {
  const { text, counts } = await generateThirdPartyLicenses({ frontendDirectory });
  await mkdir(outputDirectory, { recursive: true });
  await copyFile(join(repositoryDirectory, 'LICENSE'), join(outputDirectory, 'LICENSE.txt'));
  await copyFile(
    join(repositoryDirectory, 'THIRD_PARTY_NOTICES.md'),
    join(outputDirectory, 'THIRD_PARTY_NOTICES.txt'),
  );
  await writeFile(join(outputDirectory, 'THIRD_PARTY_LICENSES.txt'), text);
  console.log(
    `Wrote LICENSE.txt, THIRD_PARTY_NOTICES.txt and ${counts.runtime} runtime package license records.`,
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await writeLicenseArtifacts();
}
