import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/dom';
import { afterEach, vi } from 'vitest';
import { clearNotifications } from './notifications';

// The suite runs many component tests in parallel. A loaded CI machine can delay
// a single requestAnimationFrame or microtask past the default one-second
// waitFor budget, so give asynchronous assertions a little more room without
// weakening what they check.
configure({ asyncUtilTimeout: 5000 });

// jsdom does not implement object-URL revocation. A component may schedule the
// revocation from a timer that fires after its test file has finished, so provide
// a no-op to keep teardown deterministic when the full suite runs in parallel.
if (typeof URL.revokeObjectURL !== 'function') {
  (URL as { revokeObjectURL: (url: string) => void }).revokeObjectURL = () => {};
}

// Existing component regressions render an authenticated administrator. Auth.test
// explicitly unmocks this hook to exercise real session restoration and login.
vi.mock('./Auth', async (original) => {
  const observeVerification = () => () => {};
  return {
    ...(await original<typeof import('./Auth')>()),
    useAuth: () => ({
      account: {
        id: 'fixture',
        username: 'Odin',
        email: 'odin@example.test',
        role: 'ADMIN',
        permissions: ['play', 'profile', 'training', 'tournaments', 'models', 'all_data'],
      },
      logout: async () => {},
      observeVerification,
    }),
  };
});

afterEach(() => {
  clearNotifications();
});
