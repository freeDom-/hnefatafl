import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from './api';
import {
  defaultAnalyticsSettings,
  type AnalyticsSettings,
  type PositionAnalysis,
} from './analysisTypes';
import {
  normalizeExecutionPolicy,
  type ExecutionMode,
  type ExecutionPolicy,
} from './analysisPolicy';
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
  errorCategory?: string;
  download?: { transferred: number; total: number };
}

function mergeAdvisorReport(
  stored: PositionAnalysis | undefined,
  local: PositionAnalysis,
): PositionAnalysis {
  if (!stored || stored.cursor !== local.cursor || stored.position_key !== local.position_key) {
    return local;
  }
  return {
    ...local,
    evaluations: [
      ...stored.evaluations.filter((entry) => entry.agent_id !== 'advisor'),
      ...local.evaluations,
    ],
    // The newly calculated advice is the requested hint; unrelated historical
    // evaluations remain untouched.
    hint: local.hint ?? stored.hint,
  };
}

function resolvePolicy(settings: AnalyticsSettings): ExecutionPolicy {
  try {
    return normalizeExecutionPolicy(settings.execution, { browserDelivery: false });
  } catch {
    // A malformed modern projection fails closed: do not guess server-only or
    // silently enable local execution.
    return {
      ...normalizeExecutionPolicy(undefined),
      mode: 'disabled' as const,
      browserDelivery: false,
      browserExecution: false,
      serverComputation: false,
      fallback: false,
    };
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
    [settings.execution],
  );
  const mode: ExecutionMode = policy.mode;
  const localMode = policy.browserExecution;
  const disabled = mode === 'disabled';
  const key = game
    ? `${accountId ?? ''}:${basePath}:${game.id}:${game.version}:${game.cursor}:${settings.revision}:${JSON.stringify([settings.advisor, settings.time_limit, settings.show_win_chances])}`
    : '';
  // Winning chances and the best-move hint belong to the selected move advisor.
  const needed = !!game && !!settings.advisor;
  const rawStored =
    !settingsPending && game?.analysis?.cursor === game?.cursor ? game?.analysis : null;
  const storedAdvisorCurrent = (rawStored?.advisor_revision ?? 0) === settings.revision;
  const stored = rawStored
    ? storedAdvisorCurrent
      ? rawStored
      : {
          ...rawStored,
          evaluations: rawStored.evaluations.filter((entry) => entry.agent_id !== 'advisor'),
          hint: null,
        }
    : null;
  const storedAdvisorComplete = Boolean(
    stored &&
      stored.cursor === game?.cursor &&
      stored.status === 'complete' &&
      stored.evaluations.some(
        (entry) =>
          entry.agent_id === 'advisor' &&
          entry.source !== 'unavailable' &&
          !entry.unavailable_reason,
      ) &&
      ((stored.advisor_revision ?? 0) === settings.revision || settings.revision === 0),
  );
  const [request, setRequest] = useState<AnalysisRequestState | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [onlineEpoch, setOnlineEpoch] = useState(0);
  const activeController = useRef<AbortController | null>(null);
  const serverFallbackKeys = useRef(new Set<string>());
  const creditedReplay = useRef('');

  const refreshSettings = useCallback(async () => {
    if (!gameId) return;
    const sequence = ++settingsSequence.current;
    try {
      const value = await api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`);
      if (currentGame.current === gameId && settingsSequence.current === sequence) {
        confirmedSettings.current = { gameId, value };
        setPreferences({ gameId, value, pending: false, confirmed: true });
      }
    } catch {
      // Keep the last confirmed settings; the failed execution remains visible
      // and retry re-reads them.
    }
  }, [gameId, basePath]);

  // Revalidate the local authorization boundary when connectivity returns.
  useEffect(() => {
    const online = () => setOnlineEpoch((value) => value + 1);
    window.addEventListener('online', online);
    return () => window.removeEventListener('online', online);
  }, []);

  // Refresh the server-owned policy projection on reconnect or when a visible
  // analysis surface resumes after a server restart/policy change.
  useEffect(() => {
    if (!gameId || !visible) return;
    const refresh = () => {
      if (!document.hidden) void refreshSettings();
    };
    window.addEventListener('online', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.removeEventListener('online', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [gameId, visible, refreshSettings]);

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

  // One effect owns one logical request. It executes locally, retires it, then
  // (only after a classified eligible failure) hands the same request to the
  // server. No second effect or key-based fallback flag can resurrect an older
  // execution path after navigation.
  useEffect(() => {
    if (
      !game ||
      disabled ||
      !visible ||
      !needed ||
      settingsPending ||
      !settingsConfirmed ||
      storedAdvisorComplete
    )
      return;
    let alive = true;
    let running = false;
    let failures = 0;
    const controller = new AbortController();
    activeController.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sequence = settingsSequence.current;
    const current = () =>
      alive && !controller.signal.aborted && settingsSequence.current === sequence;
    const baseReport = stored ?? undefined;
    const stateFor = (
      status: AnalysisRequestState['status'],
      patch: Partial<AnalysisRequestState> = {},
    ): AnalysisRequestState => ({
      key,
      status,
      report: baseReport,
      ...patch,
    });

    const pollServer = async () => {
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
        if ((report.advisor_revision ?? 0) !== settings.revision) {
          await refreshSettings();
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
        setRequest(
          stateFor(pending ? 'pending' : 'done', {
            status: pending ? 'pending' : 'done',
            report,
            local: localMode,
            fallback: localMode,
          }),
        );
        if (pending) timer = setTimeout(() => void pollServer(), 300);
        else serverFallbackKeys.current.delete(key);
      } catch (error) {
        if (!current()) return;
        const temporary =
          error instanceof ApiError && (error.status === 503 || error.status === 429);
        const retry = temporary && failures < 8;
        setRequest((previous) => ({
          key,
          status: retry ? 'waiting' : 'failed',
          report: previous?.key === key ? previous.report : baseReport,
          local: localMode,
          fallback: localMode,
          errorCategory: retry ? 'server-busy' : 'server-failed',
        }));
        if (retry) {
          const delay = Math.min(30000, Math.max(1000 * 2 ** failures, error.retryAfterMs ?? 0));
          failures += 1;
          timer = setTimeout(() => void pollServer(), delay);
        } else {
          serverFallbackKeys.current.delete(key);
        }
      } finally {
        running = false;
      }
    };

    const run = async () => {
      if (localMode && serverFallbackKeys.current.has(key)) {
        // A server request admitted for this logical request may still be
        // retiring. Observe/join it rather than starting an overlapping local
        // search for the same position.
        setRequest(
          stateFor('waiting', {
            status: 'waiting',
            local: true,
            fallback: true,
            stage: 'fallback',
          }),
        );
        await pollServer();
        return;
      }
      if (localMode) {
        setRequest(stateFor('pending', { status: 'pending', local: true, stage: 'preparing' }));
        try {
          const integration = await loadAnalysisIntegration();
          if (!current()) return;
          const outcome = await integration.runLocalAnalysis({
            accountId,
            game,
            basePath,
            cursor: game.cursor,
            settings,
            policy,
            signal: controller.signal,
            offline: typeof navigator !== 'undefined' && navigator.onLine === false,
            onStage: (nextStage) => {
              if (current()) {
                setRequest((previous) =>
                  previous?.key === key
                    ? { ...previous, status: 'pending', stage: nextStage, local: true }
                    : previous,
                );
              }
            },
            onDownloadProgress: (progress) => {
              if (current()) {
                setRequest((previous) =>
                  previous?.key === key
                    ? {
                        ...previous,
                        download: {
                          transferred: progress.transferredBytes,
                          total: progress.totalBytes,
                        },
                      }
                    : previous,
                );
              }
            },
          });
          if (!current()) return;
          setRequest(
            stateFor('done', {
              status: 'done',
              report: mergeAdvisorReport(baseReport, outcome.report),
              local: true,
              offline: outcome.offline,
              stage: undefined,
            }),
          );
          return;
        } catch (error) {
          if (!current()) return;
          const failure = error as Partial<LocalAnalysisError>;
          if (failure?.category === 'cancelled') return;
          if (failure?.code === 'policy-changed') {
            setRequest(
              stateFor('failed', {
                local: true,
                errorCategory: 'policy-changed',
              }),
            );
            void refreshSettings();
            return;
          }
          if (
            failure?.fallbackEligible === true &&
            policy.fallback &&
            mode === 'browser_preferred' &&
            (typeof navigator === 'undefined' || navigator.onLine !== false)
          ) {
            serverFallbackKeys.current.add(key);
            setRequest(
              stateFor('waiting', {
                status: 'waiting',
                local: true,
                fallback: true,
                stage: 'fallback',
              }),
            );
            await pollServer();
            return;
          }
          setRequest(
            stateFor('failed', {
              local: true,
              errorCategory: failure?.category ?? 'unknown',
            }),
          );
          return;
        }
      }
      setRequest(stateFor('pending', { status: 'pending' }));
      await pollServer();
    };
    void run();
    return () => {
      alive = false;
      activeController.current = null;
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
    storedAdvisorComplete,
    localMode,
    disabled,
    mode,
    policy.fallback,
    policy.offlineAuthorizationSeconds,
    accountId,
    attempt,
    historical,
    onlineEpoch,
  ]);
  const current = request?.key === key ? request : null;
  const report = current?.report ?? stored ?? null;
  const cancel = () => {
    activeController.current?.abort();
    setRequest((previous) =>
      previous?.key === key
        ? { ...previous, status: 'failed', errorCategory: 'cancelled' }
        : previous,
    );
  };
  return {
    report,
    pending:
      needed &&
      !settingsPending &&
      settingsConfirmed &&
      !disabled &&
      (current
        ? current.status === 'pending' || current.status === 'waiting'
        : !storedAdvisorComplete),
    waiting: current?.status === 'waiting',
    failed: current?.status === 'failed',
    retry: () => {
      setAttempt((value) => value + 1);
    },
    cancel,
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
    analysisError: current?.errorCategory,
    downloadProgress: current?.download,
  };
}
