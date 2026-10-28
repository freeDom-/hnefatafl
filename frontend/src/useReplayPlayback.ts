import { useCallback, useEffect, useState } from 'react';

/**
 * Viewer-local replay cursor with a bounded one-move-per-second timer.
 * It never calls the API; the caller decides what the cursor means.
 *
 * The cursor is clamped to the current recording, so a later/smaller recording
 * cannot leave a stale position. The owning component is keyed by recording ID,
 * so no length-change effect is needed (and none can race a manual click).
 */
export function useReplayPlayback(length: number) {
  const max = Math.max(0, length - 1);
  const [cursorState, setCursorState] = useState(0);
  const [playing, setPlaying] = useState(false);
  const cursor = Math.min(cursorState, max);

  // Manual navigation stops playback before moving.
  const setCursor = useCallback(
    (value: number) => {
      setPlaying(false);
      setCursorState(Math.max(0, Math.min(max, value)));
    },
    [max],
  );

  useEffect(() => {
    if (!playing) return;
    if (cursor >= max) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => {
      setCursorState((current) => Math.min(max, current + 1));
    }, 1000);
    return () => clearTimeout(timer);
  }, [playing, cursor, max]);

  const toggle = useCallback(() => {
    setPlaying((current) => (current ? false : cursor < max));
  }, [cursor, max]);

  return { cursor, playing, setCursor, toggle };
}
