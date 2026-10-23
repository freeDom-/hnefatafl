import {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { api, setCSRF } from './api';

export type Account = {
  id: string;
  username: string;
  email: string;
  role: string;
  permissions: string[];
};
type Session = { account: Account; csrf_token: string };
type Authentication = { account: Account | null; logout: () => Promise<void> };
const Context = createContext<Authentication>({ account: null, logout: async () => {} });
export const useAuth = () => useContext(Context);

export default function Auth({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  function accept(session: Session) {
    try {
      if (localStorage.getItem('hnefatafl-account-cache') !== session.account.id) {
        localStorage.removeItem('hnefatafl-game');
        localStorage.removeItem('hnefatafl-new-game-options');
      }
      localStorage.setItem('hnefatafl-account-cache', session.account.id);
    } catch {
      /* Private browser mode must not prevent authentication. */
    }
    setCSRF(session.csrf_token);
    setAccount(session.account);
  }
  function clear() {
    setCSRF('');
    setAccount(null);
    try {
      for (const key of ['hnefatafl-game', 'hnefatafl-new-game-options', 'hnefatafl-account-cache'])
        localStorage.removeItem(key);
    } catch {
      /* Protected React state is still cleared. */
    }
  }
  useEffect(() => {
    let alive = true;
    api<Session>('/auth/me')
      .then((session) => {
        if (alive) accept(session);
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setLoading(false);
      });
    const expired = () => {
      clear();
      setError('Your session ended. Please log in again.');
    };
    window.addEventListener('authentication-required', expired);
    return () => {
      alive = false;
      window.removeEventListener('authentication-required', expired);
    };
  }, []);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError('');
    setNotice('');
    try {
      accept(
        await api<Session>('/auth/login', {
          identity: values.get('identity'),
          password: values.get('password'),
        }),
      );
      form.reset();
      if (location.pathname === '/login') {
        const next = new URLSearchParams(location.search).get('next') ?? '/';
        history.replaceState(
          null,
          '',
          ['/', '/training', '/tournaments', '/archive'].includes(next) ? next : '/',
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    try {
      await api('/auth/logout', {});
      clear();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (!account) return;
    const refresh = () => {
      void api<Session>('/auth/me')
        .then(accept)
        .catch(() => {
          clear();
        });
    };
    const timer = window.setInterval(refresh, 60000);
    window.addEventListener('focus', refresh);
    window.addEventListener('permissions-changed', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('permissions-changed', refresh);
    };
  }, [account?.id]);
  if (loading)
    return (
      <main className="auth-screen" role="status">
        Loading your session…
      </main>
    );
  if (!account)
    return (
      <main className="auth-screen">
        <section className="auth-card">
          <h1>HNEFATAFL</h1>
          <p>The King’s Table</p>
          <h2>Log in</h2>
          <form onSubmit={login}>
            <label>
              Username or email
              <input name="identity" autoComplete="username" required maxLength={254} autoFocus />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
              />
            </label>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button className="button primary" type="submit" disabled={busy}>
              {busy ? 'Logging in…' : 'Log in'}
            </button>
          </form>
          <button
            className="button secondary"
            type="button"
            onClick={() => setNotice('Registration is not available yet.')}
          >
            Sign up / Register
          </button>
          {notice && <p role="status">{notice}</p>}
        </section>
      </main>
    );
  const required =
    location.pathname.replace(/\/$/, '') === '/training'
      ? 'training'
      : location.pathname.replace(/\/$/, '') === '/tournaments'
        ? 'tournaments'
        : '';
  return (
    <Context.Provider
      key={account.id + ':' + account.permissions.join(',')}
      value={{ account, logout }}
    >
      {required && !account.permissions.includes(required) ? (
        <main>
          <h1>Access denied</h1>
          <a href="/">Return to gameplay</a>
        </main>
      ) : (
        children
      )}
      {error && <p role="alert">{error}</p>}
    </Context.Provider>
  );
}
