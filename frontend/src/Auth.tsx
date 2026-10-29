import {
  createContext,
  useContext,
  useEffect,
  useState,
  useRef,
  type FormEvent,
  type ReactNode,
} from 'react';
import { api, setCSRF } from './api';
import AccountForms from './AccountForms';
import { safeDestination } from './navigation';
import { useI18n } from './i18n';

export type Account = {
  id: string;
  username: string;
  email: string;
  role: string;
  permissions: string[];
  email_verified?: boolean;
};
type Session = { account: Account; csrf_token: string };
type Authentication = { account: Account | null; logout: () => Promise<void> };
const Context = createContext<Authentication>({ account: null, logout: async () => {} });
export const useAuth = () => useContext(Context);

export default function Auth({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const generation = useRef(0);
  const acceptedAccountId = useRef<string | null>(null);
  const signingOut = useRef(false);
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  function accept(session: Session) {
    let changed =
      acceptedAccountId.current !== null && acceptedAccountId.current !== session.account.id;
    try {
      if (localStorage.getItem('hnefatafl-account-cache') !== session.account.id) {
        changed = true;
        localStorage.removeItem('hnefatafl-game');
        localStorage.removeItem('hnefatafl-play-route');
        localStorage.removeItem('hnefatafl-new-game-options');
      }
      localStorage.setItem('hnefatafl-account-cache', session.account.id);
    } catch {
      /* Private browser mode must not prevent authentication. */
    }
    if (changed) {
      try {
        sessionStorage.removeItem('hnefatafl-online-intent');
      } catch {
        /* Private mode. */
      }
    }
    acceptedAccountId.current = session.account.id;
    setCSRF(session.csrf_token);
    setAccount(session.account);
  }
  function clear(clearPreferences = true) {
    generation.current++;
    acceptedAccountId.current = null;
    if (clearPreferences) {
      try {
        sessionStorage.removeItem('hnefatafl-online-intent');
      } catch {
        /* Private mode. */
      }
    }
    setCSRF('');
    setAccount(null);
    if (clearPreferences) {
      try {
        for (const key of [
          'hnefatafl-game',
          'hnefatafl-play-route',
          'hnefatafl-new-game-options',
          'hnefatafl-account-cache',
        ])
          localStorage.removeItem(key);
      } catch {
        /* Protected React state is still cleared. */
      }
    }
  }
  useEffect(() => {
    let alive = true;
    const expected = generation.current;
    const controller = new AbortController();
    api<Session>('/auth/me', undefined, undefined, controller.signal)
      .then((session) => {
        if (alive && expected === generation.current) accept(session);
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
      generation.current++;
      setCSRF('');
      controller.abort();
      window.removeEventListener('authentication-required', expired);
    };
  }, []);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (signingOut.current) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const expected = ++generation.current;
    setBusy(true);
    setError('');
    try {
      const session = await api<Session>('/auth/login', {
        identity: values.get('identity'),
        password: values.get('password'),
      });
      if (expected !== generation.current) return;
      accept(session);
      form.reset();
      if (location.pathname === '/login') {
        history.replaceState(
          null,
          '',
          safeDestination(new URLSearchParams(location.search).get('next')),
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    signingOut.current = true;
    generation.current++;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const pending = api('/auth/logout', {}, undefined, controller.signal);
      clear(true);
      setBusy(true);
      await pending;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      clearTimeout(timeout);
      signingOut.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!account) return;
    let alive = true;
    let controller: AbortController | null = null;
    const refresh = () => {
      if (controller || signingOut.current) return;
      const expected = generation.current;
      const attempt = new AbortController();
      controller = attempt;
      void api<Session>('/auth/me', undefined, undefined, attempt.signal)
        .then((session) => {
          if (alive && expected === generation.current && !signingOut.current) accept(session);
        })
        .catch(() => {
          if (alive && expected === generation.current && !attempt.signal.aborted) clear();
        })
        .finally(() => {
          if (controller === attempt) controller = null;
        });
    };
    const timer = window.setInterval(refresh, 60000);
    window.addEventListener('focus', refresh);
    window.addEventListener('permissions-changed', refresh);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('permissions-changed', refresh);
    };
  }, [account?.id]);
  if (loading || signingOut.current)
    return (
      <main className="auth-screen" role="status">
        {t(signingOut.current ? 'Logging out…' : 'Loading your session…')}
      </main>
    );
  if (
    !account ||
    ['/register', '/verify-email', '/forgot-password', '/reset-password'].includes(
      location.pathname,
    )
  )
    return <AccountForms onLogin={login} loginBusy={busy} loginError={error} onReset={clear} />;
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
          <h1>{t('Access denied')}</h1>
          <a href="/">{t('Return to gameplay')}</a>
        </main>
      ) : (
        children
      )}
      {error && <p role="alert">{error}</p>}
    </Context.Provider>
  );
}
