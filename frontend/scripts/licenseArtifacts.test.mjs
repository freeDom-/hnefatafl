import assert from 'node:assert/strict';
import test from 'node:test';
import { runtimePackages } from './licenseArtifacts.mjs';

test('runtime package inventory excludes development and nested duplicate entries', () => {
  const packages = runtimePackages({
    packages: {
      '': { dependencies: { react: '^19' } },
      'node_modules/react': { version: '19.3.1', license: 'MIT' },
      'node_modules/@scope/runtime': { version: '1.2.3', license: 'ISC' },
      'node_modules/dev-tool': { version: '9.9.9', license: 'Apache-2.0', dev: true },
      'node_modules/react/node_modules/nested': { version: '2.0.0', license: 'MIT' },
    },
  });
  assert.deepEqual(packages, [
    { name: '@scope/runtime', version: '1.2.3', declaredLicense: 'ISC' },
    { name: 'react', version: '19.3.1', declaredLicense: 'MIT' },
  ]);
});
