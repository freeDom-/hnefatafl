import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import {
  defaultAnalyticsSettings,
  type AnalyticsSettings,
  type PositionAnalysis,
} from './analysisTypes';
import type { Game } from './types';

/** Background requests are tied to both the position and the advisor revision. */
export function useGameAnalysis(
  game: Game | null,
  visible: boolean,
  historical = false,
  basePath = '/games',
  readOnlyHistory = false,
) {
  const gameId = game?.id ?? '';
  const currentGame = useRef(gameId);
  currentGame.current = gameId;
  const [preferences, setPreferences] = useState<{
    gameId: string;
    value: AnalyticsSettings;
    pending: boolean;
    confirmed: boolean;
    error?: string;
  } | null>(null);
  const settingsQueue = useRef<Promise<unknown>>(Promise.resolve());
  const settingsSequence = useRef(0);
  const inlineSettings = game?.analytics;
  const confirmedSettings = useRef<{ gameId: string; value: AnalyticsSettings } | null>(null);
  useEffect(() => {
    if (!gameId) return;
    let alive = true;
    const sequence = ++settingsSequence.current;
    if (inlineSettings) {
      confirmedSettings.current = { gameId, value: inlineSettings };
      setPreferences({ gameId, value: inlineSettings, pending: false, confirmed: true });
      return;
    }
    setPreferences({ gameId, value: defaultAnalyticsSettings, pending: true, confirmed: false });
    void api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`)
      .then((value) => {
        if (alive && settingsSequence.current === sequence) {
          confirmedSettings.current = { gameId, value };
          setPreferences({ gameId, value, pending: false, confirmed: true });
        }
      })
      .catch((error: Error) => {
        if (alive && settingsSequence.current === sequence)
          setPreferences({
            gameId,
            value: defaultAnalyticsSettings,
            pending: false,
            confirmed: false,
            error: error.message,
          });
      });
    return () => {
      alive = false;
    };
    // A settings update is independent of subsequent gameplay responses.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameId, basePath]);
  const settings =
    preferences?.gameId === gameId
      ? preferences.value
      : (inlineSettings ?? defaultAnalyticsSettings);
  const settingsPending = !!gameId && (preferences?.gameId !== gameId || preferences.pending);
  const settingsError = preferences?.gameId === gameId ? preferences.error : undefined;
  const settingsConfirmed = preferences?.gameId === gameId && preferences.confirmed;
  const updateSettings = useCallback(
    async (next: AnalyticsSettings) => {
      if (!gameId) return;
      const sequence = ++settingsSequence.current;
      setPreferences({ gameId, value: next, pending: true, confirmed: false });
      // Serialize writes so a slow older request cannot undo the latest selection.
      const operation = settingsQueue.current
        .catch(() => undefined)
        .then(() =>
          api<AnalyticsSettings>(
            `${basePath}/${gameId}/analytics`,
            {
              advisor: next.advisor && {
                name: next.advisor.name,
                profile_id: next.advisor.profile_id,
                config: next.advisor.config,
              },
              time_limit: next.time_limit,
              // Removing the checkbox makes the advisor the single trigger for
              // winning chances, so keep the persisted gate enabled.
              show_win_chances: true,
            },
            'PUT',
          ),
        );
      settingsQueue.current = operation;
      try {
        const value = await operation;
        if (currentGame.current === gameId) confirmedSettings.current = { gameId, value };
        if (currentGame.current === gameId && settingsSequence.current === sequence)
          setPreferences({ gameId, value, pending: false, confirmed: true });
      } catch (error) {
        if (currentGame.current !== gameId || settingsSequence.current !== sequence) return;
        const message = (error as Error).message;
        // A failed response may still have committed. Read the server's effective
        // preferences before showing results or enabling a new analysis request.
        try {
          const value = await api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`);
          if (currentGame.current === gameId && settingsSequence.current === sequence) {
            confirmedSettings.current = { gameId, value };
            setPreferences({ gameId, value, pending: false, confirmed: true, error: message });
          }
        } catch {
          if (currentGame.current === gameId && settingsSequence.current === sequence) {
            const value =
              confirmedSettings.current?.gameId === gameId
                ? confirmedSettings.current.value
                : defaultAnalyticsSettings;
            setPreferences({ gameId, value, pending: false, confirmed: false, error: message });
          }
        }
      }
    },
    [gameId, basePath],
  );
  const key = game
    ? `${game.id}:${game.version}:${game.cursor}:${settings.revision}:${JSON.stringify([settings.advisor, settings.time_limit, settings.show_win_chances])}`
    : '';
  // Winning chances and the best-move hint belong to the selected move advisor.
  const needed = !!game && !!settings.advisor;
  const stored =
    !settingsPending &&
    game?.analysis?.cursor === game?.cursor &&
    (game?.analysis?.advisor_revision ?? 0) === settings.revision
      ? game?.analysis
      : null;
  const [request, setRequest] = useState<{
    key: string;
    status: 'pending' | 'waiting' | 'done' | 'failed';
    report?: PositionAnalysis;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (
      !game ||
      !visible ||
      !needed ||
      settingsPending ||
      !settingsConfirmed ||
      stored?.status === 'complete'
    )
      return;
    let alive = true;
    let running = false;
    let failures = 0;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sequence = settingsSequence.current;
    const current = () =>
      alive && !controller.signal.aborted && settingsSequence.current === sequence;
    setRequest((previous) => ({
      key,
      status: 'pending',
      report: previous?.key === key ? previous.report : (stored ?? undefined),
    }));
    const poll = async () => {
      if (!current() || running) return;
      running = true;
      try {
        const report = await api<PositionAnalysis>(
          `${basePath}/${game.id}/analysis`,
          {
            version: game.version,
            cursor: game.cursor,
            background: !readOnlyHistory,
            historical,
          },
          undefined,
          controller.signal,
        );
        if (!current()) return;
        // A response from before an advisor edit must never redraw its arrow.
        if ((report.advisor_revision ?? 0) !== settings.revision) {
          const value = await api<AnalyticsSettings>(
            `${basePath}/${game.id}/analytics`,
            undefined,
            undefined,
            controller.signal,
          );
          if (current()) {
            confirmedSettings.current = { gameId, value };
            setPreferences({ gameId, value, pending: false, confirmed: true });
          }
          return;
        }
        failures = 0;
        const pending = report.status === 'pending';
        setRequest({ key, status: pending ? 'pending' : 'done', report });
        if (pending) timer = setTimeout(() => void poll(), 300);
      } catch (error) {
        if (!current()) return;
        const temporary = error instanceof ApiError && error.status === 503;
        setRequest((previous) => ({
          key,
          status: temporary ? 'waiting' : 'failed',
          report: previous?.key === key ? previous.report : (stored ?? undefined),
        }));
        if (temporary) {
          // Match the existing poller's capped backoff, while retaining fast
          // progress polling for an admitted search. Never spin during overload.
          const delay = Math.min(30000, Math.max(1000 * 2 ** failures, error.retryAfterMs ?? 0));
          failures = Math.min(failures + 1, 5);
          timer = setTimeout(() => void poll(), delay);
        }
      } finally {
        running = false;
      }
    };
    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      controller.abort();
    };
    // Stored reports arrive with gameplay responses; requests are keyed explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    key,
    visible,
    needed,
    settingsPending,
    settingsConfirmed,
    attempt,
    historical,
    readOnlyHistory,
  ]);
  const current = request?.key === key ? request : null;
  const report = current?.report ?? stored ?? null;
  return {
    report,
    pending:
      needed &&
      !settingsPending &&
      settingsConfirmed &&
      (current
        ? current.status === 'pending' || current.status === 'waiting'
        : !stored || stored.status === 'pending'),
    waiting: current?.status === 'waiting',
    failed: current?.status === 'failed',
    retry: () => setAttempt((value) => value + 1),
    show: !!game && !!settings.advisor,
    settings,
    updateSettings,
    settingsPending,
    settingsError,
  };
}
