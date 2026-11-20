import { Component, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import { t, useI18n } from './i18n';

type FeatureModule<Props> = { default: ComponentType<Props> };

function FeatureFailure({
  retry,
  reloadRequired = false,
}: {
  retry: () => void;
  reloadRequired?: boolean;
}) {
  return (
    <section className="panel empty-state" role="alert">
      <h2>{t('This page could not be loaded.')}</h2>
      <p>
        {t(
          reloadRequired
            ? 'This feature could not load its styles. Reload the application to try again.'
            : 'Retry to load this feature. Reload the application if the problem continues.',
        )}
      </p>
      <div className="page-toolbar">
        {!reloadRequired && (
          <button className="button primary" onClick={retry}>
            {t('Retry')}
          </button>
        )}
        <button className="button secondary" onClick={() => location.reload()}>
          {t('Reload application')}
        </button>
      </div>
    </section>
  );
}

class FeatureBoundary extends Component<
  { children: ReactNode; retry: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <FeatureFailure retry={this.props.retry} /> : this.props.children;
  }
}

function stylesheetLinks(): HTMLLinkElement[] {
  return Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'));
}

/**
 * Vite's preload helper keeps a document-wide set of attempted stylesheet URLs:
 * a concurrent import skips a stylesheet another import has already requested
 * but not finished loading, so its JavaScript can resolve and mount before a
 * shared stylesheet failure is known. Every stylesheet link added by a deferred
 * import is therefore tracked until it settles, and a module may only enter the
 * cache once no stylesheet is pending. A failed link is recorded document-wide;
 * because dependency ownership is not reliably available at runtime, every
 * deferred feature is conservatively blocked until a full reload re-requests the
 * asset.
 */
const failedStylesheets = new Set<string>();
const pendingStylesheets = new Set<HTMLLinkElement>();
let pendingWaiters: Array<() => void> = [];

function releasePendingStylesheets() {
  for (const link of [...pendingStylesheets]) {
    if (!link.isConnected) pendingStylesheets.delete(link);
  }
  if (pendingStylesheets.size) return;
  const waiters = pendingWaiters;
  pendingWaiters = [];
  for (const resolve of waiters) resolve();
}

function trackNewStylesheets(before: Set<string>) {
  for (const link of stylesheetLinks()) {
    if (before.has(link.href) || pendingStylesheets.has(link)) continue;
    pendingStylesheets.add(link);
    const settle = () => {
      link.removeEventListener('load', settle);
      link.removeEventListener('error', settle);
      pendingStylesheets.delete(link);
      releasePendingStylesheets();
    };
    link.addEventListener('load', settle);
    link.addEventListener('error', () => {
      failedStylesheets.add(link.href);
      settle();
    });
  }
}

function waitForPendingStylesheets(): Promise<void> {
  for (const link of [...pendingStylesheets]) {
    if (!link.isConnected) pendingStylesheets.delete(link);
  }
  if (!pendingStylesheets.size) return Promise.resolve();
  return new Promise((resolve) => pendingWaiters.push(resolve));
}

const hasUnresolvedStylesheet = () =>
  [...failedStylesheets].some((href) => stylesheetLinks().some((link) => link.href === href));

/** Code-only cache: component state, props, requests and account data stay local.
 * Explicit promises avoid Suspense's timed reveal under paused advisor clocks. */
export function deferredFeature<Props extends object>(load: () => Promise<FeatureModule<Props>>) {
  let cached: ComponentType<Props> | null = null;
  let pending: Promise<ComponentType<Props>> | null = null;
  const run = (): Promise<ComponentType<Props>> => {
    // Dependency ownership is not reliably available at runtime, so a known
    // failed shared stylesheet conservatively blocks this import too.
    if (hasUnresolvedStylesheet())
      return Promise.reject(new Error('deferred stylesheet unavailable'));
    const before = new Set(stylesheetLinks().map((link) => link.href));
    const loading = load();
    trackNewStylesheets(before);
    return loading.then((module) =>
      // Wait for every stylesheet still loading, including a shared one another
      // concurrent import requested, before caching or mounting the module.
      waitForPendingStylesheets().then(() => {
        if (hasUnresolvedStylesheet()) throw new Error('deferred stylesheet unavailable');
        cached = module.default;
        return cached;
      }),
    );
  };
  const resolve = () => {
    if (cached) return Promise.resolve(cached);
    if (!pending) {
      const queued = run();
      pending = queued;
      const clear = () => {
        if (pending === queued) pending = null;
      };
      queued.then(clear, clear);
    }
    return pending;
  };
  return function DeferredFeature(props: Props) {
    useI18n();
    const [state, setState] = useState<{
      component: ComponentType<Props> | null;
      failed: boolean;
      reloadRequired: boolean;
    }>({ component: cached, failed: false, reloadRequired: false });
    const [attempt, setAttempt] = useState(0);
    useEffect(() => {
      let alive = true;
      void resolve().then(
        (component) => {
          if (alive) setState({ component, failed: false, reloadRequired: false });
        },
        () => {
          if (alive)
            setState({ component: null, failed: true, reloadRequired: hasUnresolvedStylesheet() });
        },
      );
      return () => {
        alive = false;
      };
    }, [attempt]);
    const retry = () => {
      setState({ component: null, failed: false, reloadRequired: false });
      setAttempt((value) => value + 1);
    };
    const Feature = state.component;
    return (
      <FeatureBoundary key={attempt} retry={retry}>
        {state.failed ? (
          <FeatureFailure retry={retry} reloadRequired={state.reloadRequired} />
        ) : Feature ? (
          <Feature {...props} />
        ) : (
          <section className="empty-state" role="status">
            {t('Loading…')}
          </section>
        )}
      </FeatureBoundary>
    );
  };
}
