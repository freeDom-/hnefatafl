import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from './api';
import {
  defaultAnalyticsSettings,
  type AnalyticsSettings,
  type PositionAnalysis,
} from './analysisTypes';
import { normalizeExecutionPolicy, type ExecutionMode } from './analysisPolicy';
import { loadAnalysisIntegration } from './browserAnalysis/loader';
import type { LocalAnalysisError, LocalAnalysisStage } from './browserAnalysis/integration';
import type { Game } from './types';

type RequestStatus = 'pending' | 'waiting' | 'done' | 'failed';

interface AnalysisRequestState {
  key: string;
  status: RequestStatus;
  report?: PositionAnalysis;
  /** Local execution stage; absent for server execution. */
  stage?: LocalAnalysisStage | 'fallback';
  local?: boolean;
  fallback?: boolean;
  offline?: boolean;
  error?: string;
}

function resolvePolicy(settings: AnalyticsSettings) {
  try {
    return normalizeExecutionPolicy(settings.execution, { browserDelivery: false });
  } catch {
    // A malformed projection must not silently enable local execution.
    return normalizeExecutionPolicy(undefined);
  }
}

/** Background requests are tied to both the position and the advisor revision. */
export function useGameAnalysis(
  game: Game | null,
  visible: boolean,
  historical = false,
  basePath = '/games',
  readOnlyHistory = false,
  accountId: string | null = null,
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
  const policy = useMemo(
    () => resolvePolicy(settings),
    // Policy fields, not the whole settings object, determine execution.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settingsPending, settings.execution],
  );
  const mode: ExecutionMode = policy.mode;
  const localMode = policy.browserExecution;
  const disabled = mode === 'disabled';
  const key = game
    ? `${accountId ?? ''}:${basePath}:${game.id}:${game.version}:${game.cursor}:${settings.revision}:${JSON.stringify([settings.advisor, settings.time_limit, settings.show_win_chances])}`
    : '';
  // Winning chances and the best-move hint belong to the selected move advisor.
  const needed = !!game && !!settings.advisor;
  const stored =
    !settingsPending &&
    game?.analysis?.cursor === game?.cursor &&
    (game?.analysis?.advisor_revision ?? 0) === settings.revision
      ? game?.analysis
      : null;
  const [request, setRequest] = useState<AnalysisRequestState | null>(null);
  const [fallbackFor, setFallbackFor] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [onlineEpoch, setOnlineEpoch] = useState(0);
  const creditedReplay = useRef('');

  // Revalidate the local authorization boundary when connectivity returns. An
  // offline transition never cancels warm work; only reconnection reruns it.
  useEffect(() => {
    const online = () => setOnlineEpoch((value) => value + 1);
    window.addEventListener('online', online);
    return () => window.removeEventListener('online', online);
  }, []);

  // Bind and invalidate the managed local runtime only when local execution is
  // permitted. A server-only or disabled deployment never imports this chunk.
  useEffect(() => {
    if (!localMode) return;
    let alive = true;
    void loadAnalysisIntegration().then((integration) => {
      if (!alive) return;
      void integration.setAnalysisAccount(accountId);
    });
    return () => {
      alive = false;
    };
  }, [localMode, accountId]);

  // Local execution path. It never runs concurrently with the server path: a
  // fallback is only requested after the local promise has settled.
  useEffect(() => {
    if (
      !game ||
      !localMode ||
      disabled ||
      !visible ||
      !needed ||
      settingsPending ||
      !settingsConfirmed ||
      stored?.status === 'complete'
    )
      return;
    let alive = true;
    const controller = new AbortController();
    setRequest({
      key,
      status: 'pending',
      stage: 'preparing',
      local: true,
      report: stored ?? undefined,
    });
    const run = async () => {
      try {
        const integration = await loadAnalysisIntegration();
        if (!alive || controller.signal.aborted) return;
        const outcome = await integration.runLocalAnalysis({
          accountId,
          game,
          basePath,
          cursor: game.cursor,
          settings,
          policy,
          signal: controller.signal,
          offline: typeof navigator !== 'undefined' && navigator.onLine === false,
          onStage: (stage) => {
            if (alive && !controller.signal.aborted) {
              setRequest((previous) =>
                previous?.key === key
                  ? { ...previous, status: 'pending', stage, local: true }
                  : previous,
              );
            }
          },
        });
        if (!alive || controller.signal.aborted) return;
        setRequest({
          key,
          status: 'done',
          report: outcome.report,
          local: true,
          offline: outcome.offline,
        });
      } catch (error) {
        if (!alive || controller.signal.aborted) return;
        const failure = error as Partial<LocalAnalysisError>;
        if (failure?.category === 'cancelled') return;
        if (
          failure?.fallbackEligible === true &&
          policy.fallback &&
          mode === 'browser_preferred' &&
          (typeof navigator === 'undefined' || navigator.onLine !== false)
        ) {
          // The server policy explicitly permits a bounded compatibility
          // fallback. Do not retry locally in a loop; let the server path own it.
          setRequest({
            key,
            status: 'waiting',
            stage: 'fallback',
            local: true,
            fallback: true,
            report: stored ?? undefined,
          });
          setFallbackFor(key);
          return;
        }
        setRequest({
          key,
          status: 'failed',
          local: true,
          report: stored ?? undefined,
          error: failure?.message,
        });
      }
    };
    void run();
    return () => {
      alive = false;
      controller.abort();
    };
    // Stored reports arrive with gameplay responses; local work is keyed explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    key,
    visible,
    needed,
    settingsPending,
    settingsConfirmed,
    localMode,
    disabled,
    mode,
    policy.fallback,
    accountId,
    attempt,
    historical,
    onlineEpoch,
  ]);

  const serverEligible = !localMode || fallbackFor === key;
  useEffect(() => {
    if (
      !game ||
      !serverEligible ||
      disabled ||
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
    if (localMode) {
      setRequest((previous) =>
        previous?.key === key
          ? { ...previous, status: 'waiting', stage: 'fallback', fallback: true }
          : { key, status: 'waiting', stage: 'fallback', fallback: true },
      );
    } else {
      setRequest((previous) => ({
        key,
        status: 'pending',
        report: previous?.key === key ? previous.report : (stored ?? undefined),
      }));
    }
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
          localMode ? { 'X-Analysis-Fallback': '1' } : undefined,
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
        const replayKey = `${basePath}/${game.id}`;
        if (
          !pending &&
          (game.recorded_winner || game.winner) &&
          creditedReplay.current !== replayKey &&
          report.evaluations.some(
            (entry) =>
              entry.agent_id === 'advisor' &&
              entry.source !== 'unavailable' &&
              !entry.unavailable_reason,
          )
        ) {
          creditedReplay.current = replayKey;
          window.dispatchEvent(new Event('progression-changed'));
        }
        setRequest({
          key,
          status: pending ? 'pending' : 'done',
          report,
          local: localMode,
          fallback: localMode,
        });
        if (pending) timer = setTimeout(() => void poll(), 300);
      } catch (error) {
        if (!current()) return;
        const temporary = error instanceof ApiError && error.status === 503;
        setRequest((previous) => ({
          key,
          status: temporary ? 'waiting' : 'failed',
          report: previous?.key === key ? previous.report : (stored ?? undefined),
          local: localMode,
          fallback: localMode,
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
    serverEligible,
    disabled,
    localMode,
  ]);
  const current = request?.key === key ? request : null;
  const report = current?.report ?? stored ?? null;
  return {
    report,
    pending:
      needed &&
      !settingsPending &&
      settingsConfirmed &&
      !disabled &&
      (current
        ? current.status === 'pending' || current.status === 'waiting'
        : !stored || stored.status === 'pending'),
    waiting: current?.status === 'waiting',
    failed: current?.status === 'failed',
    retry: () => {
      setFallbackFor(null);
      setAttempt((value) => value + 1);
    },
    show: !!game && !!settings.advisor,
    settings,
    updateSettings,
    settingsPending,
    settingsError,
    // Additive presentation state; existing consumers ignore unknown fields.
    executionMode: mode,
    local: current?.local === true,
    stage: current?.stage,
    fallback: current?.fallback === true,
    offline: current?.offline === true,
    analysisError: current?.error,
  };
}
