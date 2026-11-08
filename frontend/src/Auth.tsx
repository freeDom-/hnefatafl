import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  useRef,
  type FormEvent,
  type ReactNode,
} from 'react';
import { api, ApiError, setCSRF } from './api';
import AccountForms from './AccountForms';
import { safeDestination } from './navigation';
import { useI18n } from './i18n';
import { validatePasswordLength } from './passwordPolicy';

export type Account = {
  id: string;
  username: string;
  email: string;
  role: string;
  permissions: string[];
  email_verified?: boolean;
};
type Session = { account: Account; csrf_token: string };
type Authentication = {
  account: Account | null;
  logout: () => Promise<void>;
  observeVerification: () => (verified: boolean) => void;
};
const Context = createContext<Authentication>({
  account: null,
  logout: async () => {},
  observeVerification: () => () => {},
});
export const useAuth = () => useContext(Context);

export default function Auth({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const generation = useRef(0);
  const acceptedAccount = useRef<Account | null>(null);
  const readSequence = useRef(0);
  const verificationRevision = useRef(0);
  const signingOut = useRef(false);
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Both existing /auth/me reads and private profile reads use the same order.
  // Only successful observations advance the applied revision; a failed newer
  // request must not discard an earlier successful one. Generation/owner guards
  // also prevent a response from crossing logout or an account switch.
  const observeVerification = useCallback(() => {
    const revision = ++readSequence.current;
    const expected = generation.current;
    const owner = acceptedAccount.current?.id;
    return (verified: boolean) => {
      const current = acceptedAccount.current;
      if (
        !current ||
        current.id !== owner ||
        expected !== generation.current ||
        signingOut.current ||
        revision < verificationRevision.current
      )
        return;
      verificationRevision.current = revision;
      if (current.email_verified !== verified) {
        const updated = { ...current, email_verified: verified };
        acceptedAccount.current = updated;
        setAccount(updated);
      }
    };
  }, []);
  function accept(session: Session, revision: number) {
    let changed =
      acceptedAccount.current !== null && acceptedAccount.current.id !== session.account.id;
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
    const current = acceptedAccount.current;
    const updated =
      current?.id === session.account.id && revision < verificationRevision.current
        ? { ...session.account, email_verified: current.email_verified }
        : session.account;
    if (current?.id !== session.account.id || revision >= verificationRevision.current)
      verificationRevision.current = revision;
    acceptedAccount.current = updated;
    setCSRF(session.csrf_token);
    setAccount(updated);
  }
  function clear(clearPreferences = true) {
    generation.current++;
    acceptedAccount.current = null;
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
    const revision = ++readSequence.current;
    const controller = new AbortController();
    api<Session>('/auth/me', undefined, undefined, controller.signal)
      .then((session) => {
        if (alive && expected === generation.current) accept(session, revision);
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
    const revision = ++readSequence.current;
    setBusy(true);
    setError('');
    try {
      validatePasswordLength(String(values.get('password') ?? ''));
      const session = await api<Session>('/auth/login', {
        identity: values.get('identity'),
        password: values.get('password'),
      });
      if (expected !== generation.current) return;
      accept(session, revision);
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
    let invalidated = false;
    const refresh = (event?: Event) => {
      if (signingOut.current) return;
      if (controller) {
        if (event?.type === 'permissions-changed') invalidated = true;
        return;
      }
      const expected = generation.current;
      const revision = ++readSequence.current;
      const attempt = new AbortController();
      controller = attempt;
      void api<Session>('/auth/me', undefined, undefined, attempt.signal)
        .then((session) => {
          if (alive && expected === generation.current && !signingOut.current)
            accept(session, revision);
        })
        .catch((failure) => {
          if (
            alive &&
            expected === generation.current &&
            !attempt.signal.aborted &&
            failure instanceof ApiError &&
            [401, 403].includes(failure.status)
          )
            clear();
        })
        .finally(() => {
          if (controller === attempt) controller = null;
          if (alive && expected === generation.current && invalidated && !signingOut.current) {
            invalidated = false;
            refresh();
          }
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
      value={{ account, logout, observeVerification }}
    >
      {required && !account.permissions.includes(required) ? (
        <main>
          <h1 className="sr-only" tabIndex={-1}>
            {t('Access denied')}
          </h1>
          <p role="alert">{t('Access denied')}</p>
          <a href="/">{t('Return to gameplay')}</a>
        </main>
      ) : (
        children
      )}
      {error && <p role="alert">{error}</p>}
    </Context.Provider>
  );
}
