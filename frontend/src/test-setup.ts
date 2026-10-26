import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { clearNotifications } from './notifications';

// Existing component regressions render an authenticated administrator. Auth.test
// explicitly unmocks this hook to exercise real session restoration and login.
vi.mock('./Auth', async (original) => ({
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
  }),
}));

afterEach(() => {
  clearNotifications();
});
