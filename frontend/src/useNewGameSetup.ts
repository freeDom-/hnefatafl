import { useCallback, useRef, useState } from 'react';

/**
 * Owns the asynchronous New Game setup dialog lifecycle.
 *
 * Route equality alone cannot tell overlapping option requests apart, so every
 * start takes a generation. Only the newest generation may publish options,
 * open the dialog or report a failure. Cancelling, navigating away, switching
 * account or logging out advances the generation, so a late response can never
 * reopen a dismissed dialog, replace a newer operation's options or surface a
 * stale error on an unrelated route. `owns()` reports whether the open dialog
 * still belongs to the current generation; game creation uses it to discard a
 * completion that arrives after the dialog was dismissed.
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

  /**
   * Start a setup operation. `load` returns whether the options still apply to
   * the current destination; a superseded or stale load is discarded silently.
   * A rejection reports through `onError` only while the operation is current.
   */
  const start = useCallback(
    (load: (signal: AbortSignal) => Promise<boolean>, onError: (error: unknown) => void) => {
      // A newer setup supersedes any pending request and any dialog it owns.
      cancel();
      const controller = new AbortController();
      request.current = controller;
      const current = generation.current;
      void load(controller.signal).then(
        (commit) => {
          if (!commit || generation.current !== current || controller.signal.aborted) return;
          request.current = null;
          owner.current = current;
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

  /** Whether the currently open dialog still owns the latest generation. */
  const owns = useCallback(() => owner.current !== 0 && generation.current === owner.current, []);

  return { open, start, cancel, owns };
}
