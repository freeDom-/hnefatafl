import { Component, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import { t } from './i18n';

function LoadFailure() {
  return (
    <main className="auth-screen" role="alert">
      <h1>{t('This page could not be loaded.')}</h1>
      <p>{t('Retry to load the latest application files.')}</p>
      <button className="button primary" onClick={() => location.reload()}>
        {t('Retry')}
      </button>
    </main>
  );
}
class EntryError extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <LoadFailure /> : this.props.children;
  }
}

/** Public entry mounts no Auth provider, account reads or protected App graph. */
export default function EntryRouter() {
  const [publicRoute, setPublicRoute] = useState(() => /^\/watch(?:\/|$)/.test(location.pathname));
  const [loaded, setLoaded] = useState<{ publicRoute: boolean; component: ComponentType } | null>(
    null,
  );
  const [failed, setFailed] = useState<boolean | null>(null);
  useEffect(() => {
    const changed = () => setPublicRoute(/^\/watch(?:\/|$)/.test(location.pathname));
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  useEffect(() => {
    let alive = true;
    setFailed(null);
    // Resolve the entry without a Suspense fallback's timed reveal. Frozen
    // advisor/debounce clocks must not prevent session/App initialization.
    const module = publicRoute ? import('./PublicArena') : import('./AuthenticatedArena');
    void module
      .then((value) => {
        if (alive) setLoaded({ publicRoute, component: value.default });
      })
      .catch(() => {
        if (alive) setFailed(publicRoute);
      });
    return () => {
      alive = false;
    };
  }, [publicRoute]);
  const Entry = loaded?.publicRoute === publicRoute ? loaded.component : null;
  return (
    <EntryError key={publicRoute ? 'public' : 'account'}>
      {failed === publicRoute ? (
        <LoadFailure />
      ) : Entry ? (
        <Entry />
      ) : (
        <main className="auth-screen" role="status">
          {t('Loading…')}
        </main>
      )}
    </EntryError>
  );
}
