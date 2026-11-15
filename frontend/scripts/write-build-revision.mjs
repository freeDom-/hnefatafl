/** Record the production build revision and asset fingerprint for benchmarks. */
import { writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fingerprintDirectory, readGitRevision } from '../benchmarks/buildRevision.mjs';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const revision = readGitRevision(repository);
const fingerprint = await fingerprintDirectory(join(frontend, 'dist', 'assets'));
const record = {
  schema: 1,
  ...revision,
  fingerprint,
  builtAt: new Date().toISOString(),
};
await writeFile(join(frontend, 'dist', 'arena-build.json'), JSON.stringify(record, null, 2) + '\n');
console.log(
  `Recorded build revision ${revision.revision}${revision.dirty ? ' (dirty working tree)' : ''}.`,
);
