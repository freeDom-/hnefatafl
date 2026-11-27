/**
 * Session-scoped retirement records for admitted server fallback work.
 *
 * The records deliberately live outside the React component: navigation,
 * unmount/remount, account transitions and route changes must not forget that a
 * server request may still be admitted. A record is removed only on explicit
 * status evidence (retired/expired or a server-epoch change). Elapsed time and
 * registry pressure are never evidence that work finished.
 */

import { api, requestSession } from './api';
import type { PositionAnalysis } from './analysisTypes';

export interface RetirementRecord {
  receipt: string;
  epoch: string;
  createdAt: number;
  sequence: number;
  account: string | null;
  gameId: string;
  basePath: string;
  version: number;
  cursor: number;
  revision: number;
  positionKey: string;
  ply: number;
  turn: PositionAnalysis['side_to_move'];
}

export const MAX_RETIREMENTS = 64;
export const MAX_GLOBAL_RETIREMENTS = 256;
const records = new Map<string, RetirementRecord>();

export interface AnalysisReceipt {
  schema: number;
  receipt: string;
  epoch: string;
  created_at: number;
  sequence: number;
  reconciled_before: number;
  reconciled_before_sequence: number;
  game_id: string;
  version: number;
  cursor: number;
  advisor_revision: number;
  position_key: string;
  ply: number;
  side_to_move: PositionAnalysis['side_to_move'];
}

export function validReceipt(
  raw: unknown,
  expected: Pick<AnalysisReceipt, 'game_id' | 'version' | 'cursor' | 'advisor_revision'>,
): AnalysisReceipt | null {
  const value = raw as Partial<AnalysisReceipt> | null;
  if (
    !value ||
    value.schema !== 1 ||
    typeof value.receipt !== 'string' ||
    !value.receipt ||
    typeof value.epoch !== 'string' ||
    !value.epoch ||
    typeof value.created_at !== 'number' ||
    !Number.isFinite(value.created_at) ||
    typeof value.sequence !== 'number' ||
    !Number.isSafeInteger(value.sequence) ||
    value.sequence <= 0 ||
    typeof value.reconciled_before !== 'number' ||
    !Number.isFinite(value.reconciled_before) ||
    typeof value.reconciled_before_sequence !== 'number' ||
    !Number.isSafeInteger(value.reconciled_before_sequence) ||
    value.reconciled_before_sequence < 0 ||
    value.reconciled_before_sequence >= value.sequence ||
    value.game_id !== expected.game_id ||
    value.version !== expected.version ||
    value.cursor !== expected.cursor ||
    value.advisor_revision !== expected.advisor_revision ||
    typeof value.position_key !== 'string' ||
    !value.position_key ||
    value.ply !== expected.cursor ||
    (value.side_to_move !== 'attackers' && value.side_to_move !== 'defenders')
  ) {
    return null;
  }
  return value as AnalysisReceipt;
}

export function retirementKey(
  account: string | null,
  basePath: string,
  gameId: string,
  version: number,
  cursor: number,
  revision: number,
): string {
  return `${account ?? ''}:${basePath}:${gameId}:${version}:${cursor}:${revision}`;
}

export function retirementFor(key: string): RetirementRecord | undefined {
  return records.get(key);
}

/** Reserve bounded ownership before any fallback request is submitted. */
export function addRetirement(key: string, record: RetirementRecord): boolean {
  const existing = records.get(key);
  if (existing) return existing.receipt === record.receipt;
  if (records.size >= MAX_GLOBAL_RETIREMENTS) return false;
  let owned = 0;
  for (const item of records.values()) {
    if (item.account === record.account) owned += 1;
  }
  if (owned >= MAX_RETIREMENTS) return false;
  records.set(key, record);
  return true;
}

/** Remove only the exact receipt that explicit evidence retired. */
export function settleRetirement(key: string, receipt: string): boolean {
  const existing = records.get(key);
  if (!existing || existing.receipt !== receipt) return false;
  records.delete(key);
  return true;
}

export function retirementCount(): number {
  return records.size;
}

/** Test-only/reset hook; production callers never clear potentially active work. */
export function __resetRetirements(): void {
  records.clear();
}

export interface ReceiptStatus {
  schema: number;
  receipt: string;
  epoch: string;
  state: string;
  terminal: boolean;
  reconciled_before: number;
  reconciled_before_sequence: number;
  created_at?: number;
}

export type ReceiptOutcome = 'terminal' | 'active' | 'unknown' | 'invalid';

const terminalStates = new Set(['completed', 'failed', 'expired']);

export function receiptOutcome(raw: unknown, record: RetirementRecord): ReceiptOutcome {
  const value = raw as Partial<ReceiptStatus> | null;
  if (
    !value ||
    value.schema !== 1 ||
    value.receipt !== record.receipt ||
    typeof value.epoch !== 'string' ||
    !value.epoch ||
    typeof value.state !== 'string' ||
    typeof value.terminal !== 'boolean' ||
    typeof value.reconciled_before !== 'number' ||
    !Number.isFinite(value.reconciled_before) ||
    typeof value.reconciled_before_sequence !== 'number' ||
    !Number.isSafeInteger(value.reconciled_before_sequence) ||
    value.reconciled_before_sequence < 0 ||
    value.terminal !== terminalStates.has(value.state)
  ) {
    return 'invalid';
  }
  if (value.state === 'unknown') {
    return value.epoch !== record.epoch || record.sequence <= value.reconciled_before_sequence
      ? 'terminal'
      : 'unknown';
  }
  if (value.epoch !== record.epoch) return 'invalid';
  if (value.terminal) return 'terminal';
  return ['prepared', 'submitted', 'active'].includes(value.state) &&
    record.sequence > value.reconciled_before_sequence
    ? 'active'
    : 'invalid';
}

async function withDeadline<T>(
  owner: AbortSignal,
  ms: number,
  run: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await run(AbortSignal.any([owner, controller.signal]));
  } finally {
    clearTimeout(timer);
  }
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });
}

function receiptPath(receipt: string): string {
  return `/analysis/receipts/${encodeURIComponent(receipt)}`;
}

async function runReconciliation(account: string, signal: AbortSignal): Promise<number> {
  let removed = 0;
  for (const [key, record] of [...records]) {
    if (signal.aborted) break;
    if (record.account !== account || records.get(key) !== record) continue;
    try {
      const raw = await api<unknown>(receiptPath(record.receipt), undefined, undefined, signal);
      if (!signal.aborted && receiptOutcome(raw, record) === 'terminal') {
        if (settleRetirement(key, record.receipt)) removed += 1;
      }
    } catch {
      // Another record may still have readable evidence within this pass.
    }
  }
  return removed;
}

interface Reconciliation {
  owner: AbortSignal;
  controller: AbortController;
  task: Promise<number>;
  consumers: number;
}

let reconciliation: Reconciliation | undefined;

/** One bounded reconciliation per authenticated session, with independent callers. */
export function reconcileRetirements(
  account: string | null,
  signal?: AbortSignal,
): Promise<number> {
  const session = requestSession();
  if (!account || account !== session.account || signal?.aborted) return Promise.resolve(0);
  if (reconciliation?.owner !== session.signal || reconciliation.controller.signal.aborted) {
    const controller = new AbortController();
    const owner = AbortSignal.any([session.signal, controller.signal]);
    const task = withDeadline(owner, 5_000, (signal) => runReconciliation(account, signal)).finally(
      () => {
        if (reconciliation?.task === task) reconciliation = undefined;
      },
    );
    reconciliation = { owner: session.signal, controller, task, consumers: 0 };
  }
  const pass = reconciliation;
  pass.consumers += 1;
  return new Promise((resolve) => {
    const caller = signal ? AbortSignal.any([session.signal, signal]) : session.signal;
    const finish = (removed: number) => {
      caller.removeEventListener('abort', cancel);
      pass.consumers -= 1;
      resolve(removed);
    };
    const cancel = () => {
      finish(0);
      if (!pass.consumers) pass.controller.abort();
    };
    caller.addEventListener('abort', cancel, { once: true });
    void pass.task.then((removed) => {
      if (!caller.aborted) finish(removed);
    });
  });
}

export interface ReceiptRequest {
  key: string;
  account: string | null;
  basePath: string;
  gameId: string;
  version: number;
  cursor: number;
  revision: number;
}

async function createReceipt(
  request: ReceiptRequest,
  signal: AbortSignal,
): Promise<RetirementRecord | null> {
  // Check account capacity before creating server-side prepared work. At global
  // capacity a single creation can also supply a fresh registry watermark.
  const owned = () =>
    [...records.values()].filter((record) => record.account === request.account).length;
  if (owned() >= MAX_RETIREMENTS) {
    await reconcileRetirements(request.account, signal);
    if (owned() >= MAX_RETIREMENTS) return null;
  }
  signal.throwIfAborted();
  const previous = records.size >= MAX_GLOBAL_RETIREMENTS ? [...records] : [];
  const raw = await api<unknown>(
    `${request.basePath}/${request.gameId}/analysis-receipt`,
    { version: request.version, cursor: request.cursor, revision: request.revision },
    'POST',
    signal,
  );
  signal.throwIfAborted();
  const created = validReceipt(raw, {
    game_id: request.gameId,
    version: request.version,
    cursor: request.cursor,
    advisor_revision: request.revision,
  });
  if (!created) return null;
  for (const [key, old] of previous) {
    // This response describes the shared registry. Only consider receipts
    // known before the request, so a late response cannot clear a newer epoch.
    if (old.epoch !== created.epoch || old.sequence <= created.reconciled_before_sequence) {
      settleRetirement(key, old.receipt);
    }
  }
  const record: RetirementRecord = {
    receipt: created.receipt,
    epoch: created.epoch,
    createdAt: created.created_at,
    sequence: created.sequence,
    account: request.account,
    gameId: request.gameId,
    basePath: request.basePath,
    version: request.version,
    cursor: request.cursor,
    revision: request.revision,
    positionKey: created.position_key,
    ply: created.ply,
    turn: created.side_to_move,
  };
  return addRetirement(request.key, record) ? record : null;
}

type ResultStatus =
  | 'pending'
  | 'unknown'
  | 'expired'
  | 'complete'
  | 'partial'
  | 'unavailable'
  | 'failed'
  | 'superseded';

interface ReceiptResult {
  status: ResultStatus;
  receipt_state: string;
  retired: boolean;
  retry_admission?: boolean;
  report?: PositionAnalysis;
}

const resultStates: Record<string, readonly ResultStatus[]> = {
  prepared: ['pending'],
  submitted: ['pending'],
  active: ['pending'],
  unknown: ['unknown'],
  expired: ['expired'],
  completed: ['complete', 'partial', 'unavailable', 'superseded'],
  failed: ['failed', 'unavailable', 'superseded'],
};

function consistentReport(result: ReceiptResult, record: RetirementRecord): boolean {
  const { status, report } = result;
  const success = status === 'complete' || status === 'partial';
  if (!report) return !success && report === undefined;
  if (!result.retired || (!success && status !== 'unavailable' && status !== 'failed'))
    return false;
  if (
    report.status !== (success ? status : 'unavailable') ||
    report.cursor !== record.cursor ||
    report.ply !== record.ply ||
    report.side_to_move !== record.turn ||
    report.position_key !== record.positionKey ||
    report.advisor_revision !== record.revision ||
    !Array.isArray(report.evaluations) ||
    report.evaluations.some((entry) => !entry || typeof entry.source !== 'string')
  )
    return false;
  const usable = report.evaluations.some(
    (entry) =>
      entry.agent_id === 'advisor' && entry.source !== 'unavailable' && !entry.unavailable_reason,
  );
  return (
    success === usable && (report.hint === null || (success && report.hint?.agent_id === 'advisor'))
  );
}

async function fetchReceiptResult(
  record: RetirementRecord,
  signal: AbortSignal,
): Promise<ReceiptResult | null> {
  const raw = await api<unknown>(
    `${receiptPath(record.receipt)}/result`,
    undefined,
    undefined,
    signal,
  );
  signal.throwIfAborted();
  const value = raw as
    | (Partial<ReceiptResult> & Partial<ReceiptStatus> & { receipt_epoch?: string })
    | null;
  if (!value || !value.receipt_state || !value.status) return null;
  const outcome = receiptOutcome(
    { ...value, epoch: value.receipt_epoch, state: value.receipt_state },
    record,
  );
  if (outcome === 'invalid' || !resultStates[value.receipt_state]?.includes(value.status))
    return null;
  const result = { ...value, retired: outcome === 'terminal' } as ReceiptResult;
  if (
    result.retry_admission !== undefined &&
    (result.retry_admission !== true || result.status !== 'failed' || result.report !== undefined)
  )
    return null;
  return consistentReport(result, record) ? result : null;
}

export type FallbackOutcome =
  | { kind: 'report'; report: PositionAnalysis }
  | { kind: 'revision-stale' }
  | { kind: 'stale-game' }
  | { kind: 'failed'; category: string };

export interface FallbackRequest extends ReceiptRequest {
  background: boolean;
  historical: boolean;
}

interface FallbackHooks {
  signal: AbortSignal;
  onStage: (stage: 'fallback' | 'searching') => void;
  onWaiting: () => void;
}

type RequestError = { code?: string; status?: number; retryAfterMs?: number };

function overload(error: RequestError): boolean {
  return error.status === 429 || error.status === 503;
}

function retryWait(signal: AbortSignal, onWaiting?: () => void) {
  let failures = 0;
  return async (error: RequestError): Promise<boolean> => {
    if (signal.aborted || failures >= 8) return false;
    onWaiting?.();
    const backoff = Math.min(30_000, 1000 * 2 ** failures++);
    await delay(Math.min(120_000, Math.max(backoff, error.retryAfterMs ?? 0)), signal);
    return !signal.aborted;
  };
}

async function observeResult(
  record: RetirementRecord,
  signal: AbortSignal,
  retry: (error: RequestError) => Promise<boolean>,
  allowPrepared: boolean,
  onPending?: () => void,
): Promise<ReceiptResult | null> {
  while (!signal.aborted) {
    let result: ReceiptResult | null;
    try {
      result = await fetchReceiptResult(record, signal);
    } catch (failure) {
      const error = failure as RequestError;
      const temporary =
        failure instanceof TypeError ||
        overload(error) ||
        [408, 502, 504].includes(error.status ?? 0);
      if (!temporary || !(await retry(error))) return null;
      continue;
    }
    if (!result || result.retired || (allowPrepared && result.receipt_state === 'prepared'))
      return result;
    onPending?.();
    await delay(300, signal);
  }
  return null;
}

function serverFailure(busy = false): FallbackOutcome {
  return { kind: 'failed', category: busy ? 'server-busy' : 'server-failed' };
}

function observedOutcome(result: ReceiptResult | null, signal: AbortSignal): FallbackOutcome {
  if (result?.report) return { kind: 'report', report: result.report };
  return serverFailure(signal.aborted || result?.status === 'expired' || result?.retry_admission);
}

/** Submit once unless the server explicitly proves an admission rejection. */
export async function runServerFallback(
  request: FallbackRequest,
  hooks: FallbackHooks,
): Promise<FallbackOutcome> {
  const session = requestSession();
  if (!session.account || request.account !== session.account) return serverFailure();
  const owner = AbortSignal.any([session.signal, hooks.signal]);
  return withDeadline(owner, 120_000, async (signal) => {
    const retry = retryWait(signal, hooks.onWaiting);
    const observe = async (record: RetirementRecord, allowPrepared = false) => {
      const result = await observeResult(record, signal, retry, allowPrepared, () =>
        hooks.onStage('searching'),
      );
      if (signal.aborted) return null;
      if (result?.retired) settleRetirement(request.key, record.receipt);
      return result;
    };
    let record = retirementFor(request.key);
    // An earlier submission may still reach the server. Only observe it.
    if (record) return observedOutcome(await observe(record), signal);
    while (!signal.aborted) {
      if (!record) {
        try {
          record = (await createReceipt(request, signal)) ?? undefined;
        } catch (failure) {
          const error = failure as RequestError;
          if (error.code === 'analysis_revision_stale') return { kind: 'revision-stale' };
          if (error.code === 'analysis_context_stale') return { kind: 'stale-game' };
          if (overload(error) && error.code && (await retry(error))) continue;
          return serverFailure(signal.aborted || overload(error));
        }
        if (!record) return serverFailure(true);
      }
      if (signal.aborted) break;
      hooks.onStage('fallback');
      let rejection: RequestError | undefined;
      try {
        await api<unknown>(
          `${request.basePath}/${request.gameId}/analysis`,
          {
            version: request.version,
            cursor: request.cursor,
            background: request.background,
            historical: request.historical,
          },
          undefined,
          signal,
          { 'X-Analysis-Fallback': '1', 'X-Analysis-Receipt': record.receipt },
        );
      } catch (failure) {
        const error = failure as RequestError;
        if (error.code === 'analysis_revision_stale') return { kind: 'revision-stale' };
        if (error.code === 'analysis_context_stale') return { kind: 'stale-game' };
        if (overload(error)) rejection = error;
      }
      const beforeAdmission =
        rejection?.code === 'analysis_rate_limited' || rejection?.code === 'analysis_rate_capacity';
      // Check a prepared receipt after the delay so a receipt that expired
      // during Retry-After will never be submitted as prepared.
      if (rejection && beforeAdmission && !(await retry(rejection))) return serverFailure(true);
      const result = await observe(record, beforeAdmission);
      if (
        !rejection ||
        !result ||
        !(result.retry_admission || result.receipt_state === 'prepared')
      ) {
        return observedOutcome(result, signal);
      }
      if (!beforeAdmission && !(await retry(rejection))) return serverFailure(true);
      if (result.retired) {
        // A concurrent observer may have reserved the replacement while we waited.
        const replacement = retirementFor(request.key);
        if (replacement) return observedOutcome(await observe(replacement), signal);
        record = undefined;
      } else if (retirementFor(request.key) !== record) {
        return serverFailure(true);
      }
    }
    return serverFailure(true);
  });
}

export async function waitForReceiptResult(
  record: RetirementRecord,
  signal: AbortSignal,
): Promise<ReceiptResult | null> {
  const session = requestSession();
  if (!session.account || session.account !== record.account) return null;
  return withDeadline(AbortSignal.any([session.signal, signal]), 30_000, async (owned) => {
    const result = await observeResult(record, owned, retryWait(owned), false);
    return owned.aborted ? null : result;
  });
}
