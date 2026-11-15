import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { t } from './i18n';

const PublicArena = lazy(() => import('./PublicArena'));
const AuthenticatedArena = lazy(() => import('./AuthenticatedArena'));

class EntryError extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <main className="auth-screen" role="alert">
          <h1>{t('This page could not be loaded.')}</h1>
          <p>{t('Retry to load the latest application files.')}</p>
          <button className="button primary" onClick={() => location.reload()}>
            {t('Retry')}
          </button>
        </main>
      );
    return this.props.children;
  }
}

/** The public branch mounts no Auth provider, account reads or protected App. */
export default function EntryRouter() {
  const [publicRoute, setPublicRoute] = useState(() => /^\/watch(?:\/|$)/.test(location.pathname));
  useEffect(() => {
    const changed = () => setPublicRoute(/^\/watch(?:\/|$)/.test(location.pathname));
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  return (
    <EntryError key={publicRoute ? 'public' : 'account'}>
      <Suspense
        fallback={
          <main className="auth-screen" role="status">
            {t('Loading…')}
          </main>
        }
      >
        {publicRoute ? <PublicArena /> : <AuthenticatedArena />}
      </Suspense>
    </EntryError>
  );
}
