import { Component, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import { t, useI18n } from './i18n';

type FeatureModule<Props> = { default: ComponentType<Props> };

function FeatureFailure({ retry }: { retry: () => void }) {
  return (
    <section className="panel empty-state" role="alert">
      <h2>{t('This page could not be loaded.')}</h2>
      <p>{t('Retry to load this feature. Reload the application if the problem continues.')}</p>
      <div className="page-toolbar">
        <button className="button primary" onClick={retry}>
          {t('Retry')}
        </button>
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

/** The URL in Vite's documented `vite:preloadError` CSS payload. */
const CSS_PRELOAD_ERROR = /^Unable to preload CSS for (.+)$/;

/** Code-only cache: component state, props, requests and account data stay local.
 * Explicit promises avoid Suspense's timed reveal under paused advisor clocks. */
export function deferredFeature<Props extends object>(load: () => Promise<FeatureModule<Props>>) {
  let cached: ComponentType<Props> | null = null;
  let pending: Promise<ComponentType<Props>> | null = null;
  // Vite adds a deferred feature's stylesheets as <link rel="stylesheet"> before
  // importing its module and only settles the import after every CSS preload has
  // settled. When one fails, Vite dispatches `vite:preloadError` and keeps the
  // failed link in the document; a later import can still succeed because the
  // helper skips the already-attempted link. The loader therefore remembers the
  // failed stylesheet and refuses to mount until a full reload can retry it. The
  // set is per deferred feature, so an unrelated feature that loads cleanly is
  // unaffected.
  const failedStylesheets = new Set<string>();
  const hasUnresolvedStylesheet = () =>
    [...failedStylesheets].some((href) => stylesheetLinks().some((link) => link.href === href));
  const resolve = () => {
    if (cached) return Promise.resolve(cached);
    if (!pending) {
      if (hasUnresolvedStylesheet())
        return Promise.reject(new Error('deferred stylesheet unavailable'));
      const before = new Set(stylesheetLinks().map((link) => link.href));
      const failed = new Set<string>();
      const onPreloadError = (event: Event) => {
        const payload = (event as Event & { payload?: unknown }).payload;
        const message = payload instanceof Error ? payload.message : String(payload ?? '');
        const match = CSS_PRELOAD_ERROR.exec(message);
        if (!match) return;
        const href = new URL(match[1], document.baseURI).href;
        if (!before.has(href) && stylesheetLinks().some((link) => link.href === href))
          failed.add(href);
      };
      window.addEventListener('vite:preloadError', onPreloadError);
      const settle = () => {
        window.removeEventListener('vite:preloadError', onPreloadError);
        for (const href of failed) failedStylesheets.add(href);
        return failed.size > 0;
      };
      pending = load()
        .then((module) => {
          // A module can resolve while a required stylesheet is still failed
          // (for example when the bundler skips an already-attempted link).
          if (settle()) {
            pending = null;
            throw new Error('deferred stylesheet unavailable');
          }
          cached = module.default;
          return cached;
        })
        .catch((error) => {
          settle();
          pending = null;
          throw error;
        });
    }
    return pending;
  };
  return function DeferredFeature(props: Props) {
    useI18n();
    const [state, setState] = useState<{ component: ComponentType<Props> | null; failed: boolean }>(
      { component: cached, failed: false },
    );
    const [attempt, setAttempt] = useState(0);
    useEffect(() => {
      let alive = true;
      void resolve().then(
        (component) => {
          if (alive) setState({ component, failed: false });
        },
        () => {
          if (alive) setState({ component: null, failed: true });
        },
      );
      return () => {
        alive = false;
      };
    }, [attempt]);
    const retry = () => {
      setState({ component: null, failed: false });
      setAttempt((value) => value + 1);
    };
    const Feature = state.component;
    return (
      <FeatureBoundary key={attempt} retry={retry}>
        {state.failed ? (
          <FeatureFailure retry={retry} />
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
