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

/** Code-only cache: component state, props, requests and account data stay local.
 * Explicit promises avoid Suspense's timed reveal under paused advisor clocks. */
export function deferredFeature<Props extends object>(load: () => Promise<FeatureModule<Props>>) {
  let cached: ComponentType<Props> | null = null;
  let pending: Promise<ComponentType<Props>> | null = null;
  const resolve = () => {
    if (cached) return Promise.resolve(cached);
    if (!pending)
      pending = load()
        .then((module) => {
          cached = module.default;
          return cached;
        })
        .catch((error) => {
          pending = null;
          throw error;
        });
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
