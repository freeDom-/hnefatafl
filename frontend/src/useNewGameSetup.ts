import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Owns the asynchronous New Game setup dialog lifecycle.
 *
 * Route equality alone cannot tell overlapping option requests apart, so every
 * start takes a generation. Only the newest generation may publish options,
 * open the dialog or report a failure. Cancelling, navigating away, switching
 * account, logging out or unmounting advances the generation and aborts the
 * pending option request, so a late response can never reopen a dismissed
 * dialog, replace a newer operation's options or surface a stale error.
 *
 * `session()` reports the generation that owns the currently open dialog and
 * `owns(session)` verifies a specific completion against it. Game creation binds
 * to the session that opened the dialog rather than to whichever dialog happens
 * to be open when the POST settles.
 */
export function useNewGameSetup() {
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const owner = useRef(0);
  const [open, setOpen] = useState(false);

  /** Invalidate the current operation and dismiss any dialog it owns. */
  const cancel = useCallback(() => {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    setOpen(false);
  }, []);

  // Unmounting the owner invalidates every generation and aborts its pending
  // option request. This must not set state, and it must not wait for a later
  // account or route effect: Auth can unmount the whole application on an
  // account switch or logout without an App re-render.
  useEffect(
    () => () => {
      generation.current += 1;
      request.current?.abort();
      request.current = null;
    },
    [],
  );

  /**
   * Start a setup operation. `load` returns the fetched options (or `null` when
   * the destination changed), `commit` publishes them, and `onError` reports a
   * failure. The hook verifies generation and cancellation before either
   * callback, so a stale promise can never publish state or notify.
   */
  const start = useCallback(
    <T>(
      load: (signal: AbortSignal) => Promise<T | null>,
      commit: (value: T) => void,
      onError: (error: unknown) => void,
    ) => {
      // A newer setup supersedes any pending request and any dialog it owns.
      cancel();
      const controller = new AbortController();
      request.current = controller;
      const current = generation.current;
      void load(controller.signal).then(
        (value) => {
          if (value === null || generation.current !== current || controller.signal.aborted) return;
          request.current = null;
          owner.current = current;
          commit(value);
          setOpen(true);
        },
        (error: unknown) => {
          if (generation.current !== current || controller.signal.aborted) return;
          request.current = null;
          onError(error);
        },
      );
    },
    [cancel],
  );

  /** The generation owning the currently open dialog, or `0` when none is open. */
  const session = useCallback(() => owner.current, []);
  /** Whether a specific completion still belongs to the current generation. */
  const owns = useCallback((value: number) => value !== 0 && generation.current === value, []);

  return { open, start, cancel, owns, session };
}
