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
    setCSRF(session.csrf_token, session.account.id);
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
    // A loaded browser-analysis runtime must discard its workers, cached
    // positions and warm-offline lease before another account can use this tab.
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('hnefatafl:session-ended'));
    }
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
    // Every permissions-changed event gets a sequence number. A successful read
    // acknowledges only the events that existed when it started, so a newer
    // event received during an active security read is never lost.
    let permissionSeq = 0;
    let satisfiedSeq = 0;
    let queuedRefresh = false;
    const securityPending = () => permissionSeq > satisfiedSeq;
    const pump = () => {
      if (!alive || controller || signingOut.current) return;
      const permissions = securityPending();
      // Ordinary reads pause while hidden; a security revalidation may run so a
      // permission change is not lost across the hidden transition.
      if (!permissions && document.visibilityState === 'hidden') return;
      const expected = generation.current;
      const revision = ++readSequence.current;
      const requestSeq = permissionSeq;
      const attempt = new AbortController();
      controller = attempt;
      void api<Session>('/auth/me', undefined, undefined, attempt.signal)
        .then((session) => {
          if (
            alive &&
            expected === generation.current &&
            !signingOut.current &&
            !attempt.signal.aborted
          ) {
            // Acknowledge only invalidations known when this read started; a
            // later event keeps the pending flag set for the follow-up read.
            if (requestSeq > satisfiedSeq) satisfiedSeq = requestSeq;
            accept(session, revision);
          }
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
          if (!alive || expected !== generation.current || signingOut.current) return;
          // An ordinary read, a read aborted by hiding, or a newer permission
          // event all reissue a pending security read. A failed security read
          // with no newer event waits for the normal cadence instead of spinning.
          if (
            securityPending() &&
            (!permissions || requestSeq < permissionSeq || attempt.signal.aborted)
          ) {
            pump();
            return;
          }
          if (queuedRefresh && document.visibilityState !== 'hidden') {
            queuedRefresh = false;
            pump();
          }
        });
    };
    const refresh = (event?: Event) => {
      if (signingOut.current) return;
      if (event?.type === 'permissions-changed') permissionSeq++;
      if (controller) {
        // An outstanding security invalidation or read already provides fresh
        // data; ordinary refreshes otherwise coalesce into one follow-up.
        if (!securityPending()) queuedRefresh = true;
        return;
      }
      pump();
    };
    let timer: number | undefined;
    const startTimer = () => {
      window.clearInterval(timer);
      if (document.visibilityState !== 'hidden') timer = window.setInterval(refresh, 60000);
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        window.clearInterval(timer);
        controller?.abort();
      } else {
        startTimer();
        // A hidden request may still be unwinding after abort. Queue one fresh
        // session read rather than reusing its stale completion on visibility.
        refresh();
      }
    };
    startTimer();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('focus', refresh);
    window.addEventListener('permissions-changed', refresh);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', visibility);
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
