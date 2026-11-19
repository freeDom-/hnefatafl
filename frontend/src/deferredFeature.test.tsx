import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useEffect, useState } from 'react';
import { deferredFeature } from './deferredFeature';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  setLanguage('en', false);
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
};

it('does not load code until mounted and coalesces concurrent code loads without sharing component state', async () => {
  function Counter({ name }: { name: string }) {
    const [count, setCount] = useState(0);
    return (
      <button onClick={() => setCount(count + 1)}>
        {name}:{count}
      </button>
    );
  }
  const pending = deferred<{ default: typeof Counter }>();
  const load = vi.fn(() => pending.promise);
  const Feature = deferredFeature(load);
  expect(load).not.toHaveBeenCalled();
  render(
    <>
      <Feature name="A" />
      <Feature name="B" />
    </>,
  );
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getAllByRole('status')).toHaveLength(2);
  await act(async () => {
    pending.resolve({ default: Counter });
    await pending.promise;
  });
  fireEvent.click(screen.getByRole('button', { name: 'A:0' }));
  expect(screen.getByRole('button', { name: 'A:1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'B:0' })).toBeVisible();
});

it('reveals a resolved feature with timers frozen, without advancing the advisor clock', async () => {
  vi.useFakeTimers();
  const pending = deferred<{ default: () => React.ReactNode }>();
  const Feature = deferredFeature(() => pending.promise);
  render(<Feature />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  await act(async () => {
    pending.resolve({ default: () => <p>Ready while paused</p> });
    await pending.promise;
  });
  expect(screen.getByText('Ready while paused')).toBeVisible();
});

it('retries an import failure and displays accessible German feedback', async () => {
  setLanguage('de', false);
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('network failed'))
    .mockResolvedValueOnce({ default: () => <p>Recovered feature</p> });
  const Feature = deferredFeature(load);
  render(<Feature />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Diese Seite konnte nicht geladen werden.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }));
  expect(await screen.findByText('Recovered feature')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
});

it('catches a feature render failure and allows remounting after recovery', async () => {
  let broken = true;
  function FeatureBody() {
    if (broken) throw new Error('render failed');
    return <p>Recovered render</p>;
  }
  const reported = vi.spyOn(console, 'error').mockImplementation(() => {});
  const Feature = deferredFeature(async () => ({ default: FeatureBody }));
  render(<Feature />);
  expect(await screen.findByRole('alert')).toHaveTextContent('This page could not be loaded.');
  expect(reported).toHaveBeenCalled();
  broken = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('Recovered render')).toBeVisible();
});

it('never mounts an obsolete account after an import finishes and remounts private feature state per account', async () => {
  const mounted = vi.fn();
  function PrivateFeature({ account }: { account: string }) {
    const [remembered] = useState(account);
    useEffect(() => {
      mounted(account);
    }, [account]);
    return <p>Private state for {remembered}</p>;
  }
  const pending = deferred<{ default: typeof PrivateFeature }>();
  const Feature = deferredFeature(() => pending.promise);
  const view = render(<Feature key="A" account="A" />);
  view.rerender(<Feature key="B" account="B" />);
  await act(async () => {
    pending.resolve({ default: PrivateFeature });
    await pending.promise;
  });
  expect(screen.getByText('Private state for B')).toBeVisible();
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(mounted).toHaveBeenCalledWith('B');
  view.rerender(<Feature key="C" account="C" />);
  expect(screen.getByText('Private state for C')).toBeVisible();
  expect(screen.queryByText('Private state for B')).toBeNull();
});

it('does not commit a resolved import after the feature unmounts', async () => {
  const mounted = vi.fn();
  function LateFeature() {
    useEffect(() => mounted(), []);
    return <p>Mounted late</p>;
  }
  const pending = deferred<{ default: typeof LateFeature }>();
  const Feature = deferredFeature(() => pending.promise);
  const view = render(<Feature />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading…');
  view.unmount();
  await act(async () => {
    pending.resolve({ default: LateFeature });
    await pending.promise;
  });
  expect(mounted).not.toHaveBeenCalled();
  expect(screen.queryByText('Mounted late')).toBeNull();
});

/**
 * Simulate Vite's production preload failure: the stylesheet link is appended
 * and `vite:preloadError` carries the CSS payload before the import settles.
 */
function failStylesheet(href: string): HTMLLinkElement {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.append(link);
  const event = new CustomEvent('vite:preloadError');
  Object.assign(event, { payload: new Error(`Unable to preload CSS for ${href}`) });
  window.dispatchEvent(event);
  return link;
}

it('keeps the error panel after Retry while a required stylesheet is unresolved', async () => {
  let link!: HTMLLinkElement;
  const load = vi.fn(async () => {
    link = failStylesheet('https://example.test/deferred.css');
    throw new Error('Unable to preload CSS');
  });
  const Feature = deferredFeature(load);
  render(<Feature />);
  expect(await screen.findByRole('alert')).toHaveTextContent('This page could not be loaded.');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  // Same-document recovery is unsafe, so the feature must not mount and the
  // loader must not re-import as if it had recovered.
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Reload application' })).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  link.remove();
});

it('does not cache or mount a module that resolves while its stylesheet is unresolved', async () => {
  let link!: HTMLLinkElement;
  const Feature = deferredFeature(async () => {
    link = failStylesheet('https://example.test/partial.css');
    return { default: () => <p>Unstyled feature</p> };
  });
  render(<Feature />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Unstyled feature')).toBeNull();
  link.remove();
});

it('keeps a stylesheet failure across unmount and remount', async () => {
  let link!: HTMLLinkElement;
  const load = vi.fn(async () => {
    link = failStylesheet('https://example.test/navigation.css');
    throw new Error('Unable to preload CSS');
  });
  const Feature = deferredFeature(load);
  const view = render(<Feature />);
  expect(await screen.findByRole('alert')).toBeVisible();
  view.unmount();
  render(<Feature />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  link.remove();
});

it('conservatively blocks an unrelated deferred feature while a stylesheet failure is unresolved', async () => {
  let link!: HTMLLinkElement;
  const Broken = deferredFeature(async () => {
    link = failStylesheet('https://example.test/broken.css');
    throw new Error('Unable to preload CSS');
  });
  const Healthy = deferredFeature(async () => ({ default: () => <p>Healthy feature</p> }));
  const view = render(<Broken />);
  expect(await screen.findByRole('alert')).toBeVisible();
  view.rerender(<Healthy />);
  // Dependency ownership is not reliably available at runtime, so the document
  // stays reload-required until the failed link is gone.
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Healthy feature')).toBeNull();
  view.unmount();
  link.remove();
  // A full reload re-requests the asset; once the failed link is gone the
  // feature loads normally.
  render(<Healthy />);
  expect(await screen.findByText('Healthy feature')).toBeVisible();
});

it('blocks a second sequential deferred import that shares a failed stylesheet', async () => {
  let link!: HTMLLinkElement;
  const shared = 'https://example.test/shared.css';
  const First = deferredFeature(async () => {
    link = failStylesheet(shared);
    throw new Error('Unable to preload CSS');
  });
  // The second feature shares the asset but, like Vite, does not add a new link
  // for an already-attempted stylesheet.
  const Second = deferredFeature(async () => ({ default: () => <p>Second feature</p> }));
  const view = render(<First />);
  expect(await screen.findByRole('alert')).toBeVisible();
  view.rerender(<Second />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Second feature')).toBeNull();
  link.remove();
});

it('blocks an overlapping deferred import when a shared stylesheet fails', async () => {
  let link!: HTMLLinkElement;
  const shared = 'https://example.test/overlap.css';
  const first = deferred<{ default: () => React.ReactNode }>();
  const second = deferred<{ default: () => React.ReactNode }>();
  const First = deferredFeature(() => first.promise);
  const Second = deferredFeature(() => second.promise);
  render(
    <>
      <First />
      <Second />
    </>,
  );
  await act(async () => {
    link = failStylesheet(shared);
    first.resolve({ default: () => <p>First feature</p> });
    second.resolve({ default: () => <p>Second feature</p> });
    await Promise.all([first.promise, second.promise]);
  });
  // Neither feature may cache or mount while the shared stylesheet is failed.
  expect(screen.queryByText('First feature')).toBeNull();
  expect(screen.queryByText('Second feature')).toBeNull();
  expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
  link.remove();
});

it('blocks a feature reached by navigation from an affected one', async () => {
  let link!: HTMLLinkElement;
  const shared = 'https://example.test/nav-shared.css';
  const First = deferredFeature(async () => {
    link = failStylesheet(shared);
    throw new Error('Unable to preload CSS');
  });
  const Second = deferredFeature(async () => ({ default: () => <p>Second feature</p> }));
  const view = render(<First />);
  expect(await screen.findByRole('alert')).toBeVisible();
  view.unmount();
  render(<Second />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Second feature')).toBeNull();
  link.remove();
});

it('does not let a cached module bypass an unresolved shared stylesheet', async () => {
  let link!: HTMLLinkElement;
  const shared = 'https://example.test/cached-shared.css';
  const Broken = deferredFeature(async () => {
    link = failStylesheet(shared);
    throw new Error('Unable to preload CSS');
  });
  const Cached = deferredFeature(async () => ({ default: () => <p>Resolved feature</p> }));
  const view = render(<Broken />);
  expect(await screen.findByRole('alert')).toBeVisible();
  // The module resolves successfully, but the shared stylesheet is unresolved,
  // so it must not be cached or mounted.
  view.rerender(<Cached />);
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByText('Resolved feature')).toBeNull();
  link.remove();
});
