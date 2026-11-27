import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
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
import { browserAnalysisLoaded, loadAnalysisIntegration } from './browserAnalysis/loader';
import type { LocalAnalysisError, LocalAnalysisStage } from './browserAnalysis/integration';
import type { RetirementRecord } from './analysisRetirements';
import type { Game } from './types';

type RequestStatus = 'pending' | 'waiting' | 'done' | 'failed' | 'cancelled';

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
    // The newly calculated advice owns the hint, including an explicit null.
    hint: local.hint,
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
  const refreshSequence = useRef(0);
  const accountRef = useRef(accountId);
  accountRef.current = accountId;
  const inlineSettings = game?.analytics;
  const confirmedSettings = useRef<{ gameId: string; value: AnalyticsSettings } | null>(null);
  useEffect(() => {
    if (!gameId) return;
    let alive = true;
    const sequence = ++settingsSequence.current;
    const requestedAccount = accountRef.current;
    if (inlineSettings) {
      confirmedSettings.current = { gameId, value: inlineSettings };
      setPreferences({ gameId, value: inlineSettings, pending: false, confirmed: true });
      return;
    }
    setPreferences({ gameId, value: defaultAnalyticsSettings, pending: true, confirmed: false });
    void api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`)
      .then((value) => {
        if (
          alive &&
          settingsSequence.current === sequence &&
          accountRef.current === requestedAccount
        ) {
          confirmedSettings.current = { gameId, value };
          setPreferences({ gameId, value, pending: false, confirmed: true });
        }
      })
      .catch((error: Error) => {
        if (
          alive &&
          settingsSequence.current === sequence &&
          accountRef.current === requestedAccount
        )
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
  }, [gameId, basePath, accountId]);
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
      const requestedAccount = accountRef.current;
      const sequence = ++settingsSequence.current;
      setPreferences({ gameId, value: next, pending: true, confirmed: false });
      // Serialize writes so a slow older request cannot undo the latest selection.
      const operation = settingsQueue.current
        .catch(() => undefined)
        .then(() => {
          if (accountRef.current !== requestedAccount || currentGame.current !== gameId) {
            throw new DOMException('Request cancelled', 'AbortError');
          }
          return api<AnalyticsSettings>(
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
          );
        });
      settingsQueue.current = operation;
      try {
        const value = await operation;
        if (
          currentGame.current === gameId &&
          accountRef.current === requestedAccount &&
          settingsSequence.current === sequence
        ) {
          confirmedSettings.current = { gameId, value };
          setPreferences({ gameId, value, pending: false, confirmed: true });
        }
      } catch (error) {
        if (
          currentGame.current !== gameId ||
          accountRef.current !== requestedAccount ||
          settingsSequence.current !== sequence
        )
          return;
        const message = (error as Error).message;
        // A failed response may still have committed. Read the server's effective
        // preferences before showing results or enabling a new analysis request.
        try {
          const value = await api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`);
          if (
            currentGame.current === gameId &&
            accountRef.current === requestedAccount &&
            settingsSequence.current === sequence
          ) {
            confirmedSettings.current = { gameId, value };
            setPreferences({ gameId, value, pending: false, confirmed: true, error: message });
          }
        } catch {
          if (
            currentGame.current === gameId &&
            accountRef.current === requestedAccount &&
            settingsSequence.current === sequence
          ) {
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
  const policyKey = JSON.stringify(settings.execution);
  const workKey = game
    ? `${accountId ?? ''}:${basePath}:${game.id}:${game.version}:${game.cursor}:${settings.revision}`
    : '';
  const key = game
    ? `${workKey}:${policyKey}:${JSON.stringify([settings.advisor, settings.time_limit, settings.show_win_chances])}`
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
  const creditedReplay = useRef('');

  const refreshSettings = useCallback(async (): Promise<boolean> => {
    if (!gameId) return false;
    const requestSequence = ++refreshSequence.current;
    const settingsSequenceAtStart = settingsSequence.current;
    const requestedAccount = accountRef.current;
    try {
      const value = await api<AnalyticsSettings>(`${basePath}/${gameId}/analytics`);
      const currentConfirmed =
        confirmedSettings.current?.gameId === gameId
          ? confirmedSettings.current.value
          : preferences?.value;
      const unchanged =
        currentConfirmed !== undefined &&
        JSON.stringify(currentConfirmed) === JSON.stringify(value);
      if (
        currentGame.current !== gameId ||
        accountRef.current !== requestedAccount ||
        refreshSequence.current !== requestSequence ||
        settingsSequence.current !== settingsSequenceAtStart ||
        unchanged
      ) {
        return false;
      }
      // A genuine settings/policy change is a new ownership generation. An
      // unchanged visibility/online refresh must not cancel running analysis.
      settingsSequence.current += 1;
      confirmedSettings.current = { gameId, value };
      setPreferences({ gameId, value, pending: false, confirmed: true });
      return true;
    } catch {
      // Keep the last confirmed settings; the failed execution remains visible
      // and retry re-reads them.
      return false;
    }
  }, [gameId, basePath, preferences?.value]);

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

  // Bind the managed local runtime and deliver known policy changes even when no
  // further local search runs. Server-only/disabled installations that never
  // loaded the deferred integration still never import it.
  useEffect(() => {
    if (!localMode && !browserAnalysisLoaded()) return;
    let alive = true;
    const requestedAccount = accountRef.current;
    const owned = () => alive && accountRef.current === requestedAccount;
    void loadAnalysisIntegration().then(async (integration) => {
      if (!owned()) return;
      await integration.setAnalysisAccount(requestedAccount);
      if (!owned()) return;
      await integration.setAnalysisPolicy(policy, requestedAccount);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localMode, accountId, policyKey]);

  // Reconcile abandoned retirements in the background without visiting their
  // games. The server binds every lookup to this account and never reveals
  // private game data.
  useEffect(() => {
    if (!localMode) return;
    let alive = true;
    void loadAnalysisIntegration().then(async (integration) => {
      if (!alive) return;
      await integration.reconcileRetirements(accountId);
    });
    return () => {
      alive = false;
    };
  }, [localMode, accountId, onlineEpoch]);

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
    const retirementApi: { current: Awaited<ReturnType<typeof loadAnalysisIntegration>> | null } = {
      current: null,
    };
    const retryDeadline = Date.now() + 120_000;
    const controller = new AbortController();
    activeController.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sequence = settingsSequence.current;
    const current = () =>
      alive && !controller.signal.aborted && settingsSequence.current === sequence;
    // Retirement records are stored outside this component. They never
    // authorize fallback; they only prevent an equivalent local search from
    // overlapping server work that may still be admitted.
    const waitForRetiredServer = async (): Promise<boolean> => {
      const record = retirementApi.current?.retirementFor(workKey);
      if (!record) return true;
      const done = await retirementApi.current?.waitForReceipt(record, controller.signal);
      return done === true && current();
    };
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

    let receipt: RetirementRecord | null | undefined =
      retirementApi.current?.retirementFor(workKey);
    const ensureReceipt = async (): Promise<RetirementRecord | null> => {
      if (receipt) return receipt;
      receipt = await retirementApi.current?.createReceipt(
        {
          key: workKey,
          account: accountId,
          basePath,
          gameId: game.id,
          version: game.version,
          cursor: game.cursor,
          revision: settings.revision,
        },
        controller.signal,
      );
      return receipt ?? null;
    };

    const pollServer = async () => {
      if (!current() || running) return;
      if (Date.now() > retryDeadline) {
        setRequest(
          stateFor('failed', {
            local: localMode,
            fallback: localMode,
            errorCategory: 'server-failed',
          }),
        );
        return;
      }
      running = true;
      try {
        let record: RetirementRecord | null = null;
        if (localMode && policy.fallback) {
          record = await ensureReceipt();
          if (!record) {
            if (current())
              setRequest(
                stateFor('failed', {
                  local: localMode,
                  fallback: localMode,
                  errorCategory: 'server-busy',
                }),
              );
            return;
          }
        }
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
          {
            ...(localMode ? { 'X-Analysis-Fallback': '1' } : {}),
            ...(record ? { 'X-Analysis-Receipt': record.receipt } : {}),
          },
        );
        if (!current()) return;
        if ((report.advisor_revision ?? 0) !== settings.revision || report.cursor !== game.cursor) {
          setRequest(stateFor('failed', { local: localMode, errorCategory: 'revision-stale' }));
          await refreshSettings();
          return;
        }
        const pending = report.status === 'pending';
        if (!pending && record) {
          // An explicit completed analysis response for this exact receipt,
          // cursor and revision is valid retirement evidence. A pending response
          // or an aborted request is not.
          retirementApi.current?.settleRetirement(workKey, record.receipt);
        }
        // Only genuine completion clears the retry budget. A pending response
        // followed by another 503 must not restart the retry allowance.
        if (!pending) failures = 0;
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
      } catch (error) {
        if (!current()) return;
        const code = (error as { code?: string })?.code;
        const status = (error as { status?: number })?.status;
        if (localMode && (status === 401 || status === 403)) {
          void loadAnalysisIntegration().then((integration) =>
            integration.invalidateAnalysisAuthorization(accountId),
          );
        }
        if (code === 'analysis_revision_stale') {
          setRequest(stateFor('failed', { local: localMode, errorCategory: 'revision-stale' }));
          await refreshSettings();
          return;
        }
        if (code === 'analysis_context_stale') {
          setRequest(stateFor('failed', { local: localMode, errorCategory: 'stale-game' }));
          return;
        }
        if (code === 'analysis_receipt_expired' || code === 'analysis_receipt_terminal') {
          // Explicit server evidence that the old request cannot be admitted.
          if (receipt) retirementApi.current?.settleRetirement(workKey, receipt.receipt);
          receipt = undefined;
        } else if (code === 'analysis_receipt_invalid') {
          // Missing evidence is unknown, not terminal. Do not reuse the id, but
          // keep the retirement record for bounded reconciliation.
          receipt = undefined;
        }
        const temporary = status === 503 || status === 429;
        const retry = temporary && failures < 8 && Date.now() + 1000 < retryDeadline;
        if (!retry) {
          setRequest((previous) => ({
            ...stateFor('failed', {
              local: localMode,
              fallback: localMode,
              errorCategory: temporary ? 'server-busy' : 'server-failed',
            }),
            report: previous?.key === key ? previous.report : baseReport,
          }));
          return;
        }
        setRequest((previous) => ({
          ...stateFor('waiting', {
            local: localMode,
            fallback: localMode,
            errorCategory: 'server-busy',
          }),
          report: previous?.key === key ? previous.report : baseReport,
        }));
        const delay = Math.min(
          30000,
          Math.max(1000 * 2 ** failures, (error as { retryAfterMs?: number }).retryAfterMs ?? 0),
        );
        failures += 1;
        timer = setTimeout(() => void pollServer(), Math.min(delay, retryDeadline - Date.now()));
      } finally {
        running = false;
      }
    };

    const run = async () => {
      if (localMode) {
        try {
          retirementApi.current = await loadAnalysisIntegration();
        } catch {
          if (current())
            setRequest(stateFor('failed', { local: true, errorCategory: 'initialization' }));
          return;
        }
        if (!current()) return;
        // A server request admitted for this logical position may still be
        // retiring. Observe its authoritative status without starting server
        // computation; only then start local work. A key alone is never
        // authorization to run a fallback.
        if (!(await waitForRetiredServer())) {
          if (!current()) return;
          setRequest(
            stateFor('failed', {
              local: true,
              errorCategory: 'server-busy',
            }),
          );
          return;
        }
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
                    ? {
                        ...previous,
                        status: 'pending',
                        stage: nextStage,
                        local: true,
                        download: nextStage === 'downloading' ? previous.download : undefined,
                      }
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
          if (failure?.category === 'cancelled') {
            if (current())
              setRequest(stateFor('cancelled', { local: true, errorCategory: 'cancelled' }));
            return;
          }
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
          if (failure?.code === 'analysis_revision_stale') {
            setRequest(
              stateFor('failed', {
                local: true,
                errorCategory: 'revision-stale',
              }),
            );
            void refreshSettings();
            return;
          }
          if (failure?.code === 'analysis_context_stale') {
            setRequest(
              stateFor('failed', {
                local: true,
                errorCategory: 'stale-game',
              }),
            );
            return;
          }
          if (
            failure?.fallbackEligible === true &&
            policy.fallback &&
            mode === 'browser_preferred' &&
            (typeof navigator === 'undefined' || navigator.onLine !== false)
          ) {
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
        ? { ...previous, status: 'cancelled', errorCategory: 'cancelled' }
        : previous,
    );
  };
  return {
    report,
    pending:
      visible &&
      needed &&
      !settingsPending &&
      settingsConfirmed &&
      !disabled &&
      (current
        ? current.status === 'pending' || current.status === 'waiting'
        : !storedAdvisorComplete),
    waiting: current?.status === 'waiting',
    failed: current?.status === 'failed',
    cancelled: current?.status === 'cancelled',
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
