import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import type { Game } from './types';

type Position = Pick<Game, 'board' | 'turn' | 'ply' | 'winner' | 'reason'>;
type Replay = { validated: boolean; states: Position[] };

/** History changes only this viewer's board, never the saved game's cursor. */
export function usePlayHistory(live: Game | null) {
  const [view, setView] = useState<{ id: string; cursor: number | null } | null>(null);
  const cursor =
    (view?.id === live?.id ? view?.cursor : null) ??
    (live && live.cursor < live.total_moves ? live.cursor : null);
  const cursorRef = useRef<number | null>(null);
  cursorRef.current = cursor;
  const [snapshot, setSnapshot] = useState<{
    key: string;
    states?: Position[];
    error?: boolean;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = live ? `${live.id}:${live.version}` : '';
  const reviewing = cursor !== null;
  const cached = snapshot?.key === key ? snapshot : null;
  useEffect(() => {
    if (!live || !reviewing || cached?.states) return;
    const controller = new AbortController();
    setSnapshot({ key });
    void api<Replay>(`/games/${live.id}/replay`, undefined, undefined, controller.signal)
      .then((replay) => {
        if (!controller.signal.aborted) {
          if (!replay.validated || replay.states.length !== live.total_moves + 1)
            throw new Error('Invalid replay');
          setSnapshot({ key, states: replay.states });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setSnapshot({ key, error: true });
      });
    return () => controller.abort();
    // A fetched snapshot is retained while switching between positions or tabs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reviewing, attempt]);
  const game = useMemo(() => {
    if (!live || cursor === null) return live;
    const position = cached?.states?.[cursor];
    if (!position) return { ...live, legal_moves: [] };
    const initial = cached!.states![0].board;
    const count = (board: string[], pieces: string) =>
      board.filter((piece) => pieces.includes(piece)).length;
    return {
      ...live,
      ...position,
      cursor,
      legal_moves: [],
      captured: {
        attackers: count(initial, 'A') - count(position.board, 'A'),
        defenders: count(initial, 'DK') - count(position.board, 'DK'),
      },
      analysis: live.analysis?.cursor === cursor ? live.analysis : null,
    };
  }, [live, cursor, cached?.states]);
  return {
    game,
    reviewing,
    cursorRef,
    loading: reviewing && !cached?.states && !cached?.error,
    error: !!cached?.error,
    retry: () => setAttempt((value) => value + 1),
    showAt: (ply: number | null) => {
      if (!live) return;
      cursorRef.current = ply;
      setView({ id: live.id, cursor: ply });
    },
  };
}
