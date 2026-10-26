import { useEffect, useRef, useState, type FormEvent } from 'react';
import { surfaceLink } from './surfaceNavigation';
import { api } from './api';
import { LanguageSelect, useI18n } from './i18n';

type View = 'login' | 'register' | 'verify' | 'forgot' | 'reset';
type Configuration = { enabled: boolean; email_available: boolean };
// StrictMode may evaluate an initializer twice. Keep the removed fragment only
// in memory until the form submits or the visitor leaves this flow.
let captured: { path: string; token: string } | null = null;
function takeToken() {
  if (['/verify-email', '/reset-password'].includes(location.pathname)) {
    const token = new URLSearchParams(location.hash.slice(1)).get('token');
    if (token) {
      captured = { path: location.pathname, token };
      history.replaceState(null, '', location.pathname);
    }
  }
  return captured?.path === location.pathname ? captured.token : '';
}
if (typeof window !== 'undefined') takeToken();

export default function AccountForms({
  onLogin,
  loginBusy,
  loginError,
  onReset,
}: {
  onLogin: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  loginBusy: boolean;
  loginError: string;
  onReset: () => void;
}) {
  const { t } = useI18n();
  const [token, setToken] = useState(takeToken);
  const [view, setView] = useState<View>(
    () =>
      (({
        '/register': 'register',
        '/verify-email': 'verify',
        '/forgot-password': 'forgot',
        '/reset-password': 'reset',
      })[location.pathname] as View) || 'login',
  );
  const [configuration, setConfiguration] = useState<Configuration>({
    enabled: false,
    email_available: false,
  });
  const [busy, setBusy] = useState(false);
  const [configurationLoaded, setConfigurationLoaded] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [complete, setComplete] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const changed = () => {
      if (!['/verify-email', '/reset-password'].includes(location.pathname)) return;
      setToken(takeToken());
      setView(location.pathname === '/verify-email' ? 'verify' : 'reset');
      setComplete(false);
      setError('');
      setNotice('');
    };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => {
    let alive = true;
    void api<Configuration>('/auth/registration')
      .then((value) => {
        if (alive)
          setConfiguration({
            enabled: value.enabled === true,
            email_available: value.email_available === true,
          });
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setConfigurationLoaded(true);
      });
    return () => {
      alive = false;
      captured = null;
    };
  }, []);
  useEffect(() => {
    heading.current?.focus();
  }, [view]);
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);
  function navigate(next: View) {
    captured = null;
    setToken('');
    setError('');
    setNotice('');
    setComplete(false);
    setView(next);
    history.replaceState(
      null,
      '',
      {
        login: '/login',
        register: '/register',
        verify: '/verify-email',
        forgot: '/forgot-password',
        reset: '/reset-password',
      }[next],
    );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const password = String(values.get('password') ?? '');
      if ((view === 'register' || view === 'reset') && password !== values.get('confirm'))
        throw new Error('Passwords do not match.');
      const [path, body] =
        view === 'register'
          ? [
              '/auth/register',
              { username: values.get('username'), email: values.get('email'), password },
            ]
          : view === 'verify' && token
            ? ['/auth/verify-email', { token }]
            : view === 'reset'
              ? ['/auth/password-reset/confirm', { token, password }]
              : [
                  view === 'verify' ? '/auth/verification/resend' : '/auth/password-reset/request',
                  { email: values.get('email') },
                ];
      const result = await api<{ message: string }>(String(path), body);
      form.reset();
      setNotice(result.message);
      if (view === 'register') {
        setView('verify');
        history.replaceState(null, '', '/verify-email');
      }
      if (token) {
        captured = null;
        setComplete(true);
        if (view === 'reset') onReset();
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const titles = {
    login: 'Log in',
    register: 'Create account',
    verify: token ? 'Confirm your email' : 'Verify your email',
    forgot: 'Reset your password',
    reset: 'Choose a new password',
  };
  const passwordFields = (
    <>
      <label>
        {t('Password')}
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={256}
        />
      </label>
      <label>
        {t('Confirm password')}
        <input
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={256}
        />
      </label>
      <p>{t('Use 12–256 characters with at least four distinct characters.')}</p>
    </>
  );
  return (
    <main className="auth-screen">
      <section className="auth-card">
        <LanguageSelect />
        <h1>HNEFATAFL</h1>
        <p>{t('The King’s Table')}</p>
        <a className="button secondary" href="/guest" onClick={surfaceLink}>
          {t('Play as guest on this device')}
        </a>
        <h2 ref={heading} tabIndex={-1}>
          {t(titles[view])}
        </h2>
        {view === 'login' ? (
          <form onSubmit={(event) => void onLogin(event)}>
            <label>
              {t('Username or email')}
              <input name="identity" autoComplete="username" required maxLength={254} />
            </label>
            <label>
              {t('Password')}
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
              />
            </label>
            {loginError && (
              <p className="form-error" role="alert">
                {t(loginError)}
              </p>
            )}
            <button className="button primary" type="submit" disabled={loginBusy}>
              {t(loginBusy ? 'Logging in…' : 'Log in')}
            </button>
          </form>
        ) : view === 'register' && !configuration.enabled ? (
          <p role="status">{t('Registration is not available yet.')}</p>
        ) : view === 'reset' && !token ? (
          <p role="alert">{t('This link is invalid, expired or replaced. Request a new link.')}</p>
        ) : (
          !complete && (
            <form onSubmit={(event) => void submit(event)}>
              {view === 'register' && (
                <label>
                  {t('Username')}
                  <input
                    name="username"
                    autoComplete="username"
                    required
                    minLength={2}
                    maxLength={64}
                  />
                </label>
              )}
              {(view === 'register' || view === 'forgot' || (view === 'verify' && !token)) && (
                <label>
                  {t('Email address')}
                  <input name="email" type="email" autoComplete="email" required maxLength={254} />
                </label>
              )}
              {(view === 'register' || view === 'reset') && passwordFields}
              {view === 'verify' && token && (
                <p>
                  {t('Opening this link has not verified your email. Confirm below to continue.')}
                </p>
              )}
              <button className="button primary" type="submit" disabled={busy}>
                {t(
                  busy
                    ? 'Please wait…'
                    : view === 'register'
                      ? 'Create account'
                      : view === 'reset'
                        ? 'Set new password'
                        : view === 'verify' && token
                          ? 'Verify email'
                          : 'Send email',
                )}
              </button>
            </form>
          )
        )}
        {error && (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        )}
        {notice && (
          <p role="status" tabIndex={-1} ref={noticeRef}>
            {t(notice)}
          </p>
        )}
        {view !== 'login' && (
          <p>
            {t(
              'Only the newest email link works. If a link expires or is replaced, request another. You can log in before verification; online eligibility requires verification.',
            )}
          </p>
        )}
        {view === 'login' && (
          <>
            <button
              className="button secondary"
              type="button"
              disabled={!configurationLoaded}
              onClick={() =>
                configuration.enabled
                  ? navigate('register')
                  : setNotice('Registration is not available yet.')
              }
            >
              {t('Sign up / Register')}
            </button>
            <button className="button secondary" type="button" onClick={() => navigate('forgot')}>
              {t('Forgot password?')}
            </button>
            <button className="button secondary" type="button" onClick={() => navigate('verify')}>
              {t('Verify email / Resend')}
            </button>
          </>
        )}
        {view !== 'login' && (
          <button className="button secondary" type="button" onClick={() => navigate('login')}>
            {t('Back to log in')}
          </button>
        )}
        {(error || (view === 'reset' && !token)) && (
          <button
            className="button secondary"
            type="button"
            onClick={() => navigate(view === 'reset' ? 'forgot' : 'verify')}
          >
            {t('Request a new link')}
          </button>
        )}
      </section>
    </main>
  );
}
