/**
 * Session-scoped retirement records for admitted server fallback work.
 *
 * The records deliberately live outside the React component: navigation,
 * unmount/remount, account transitions and route changes must not forget that a
 * server request may still be admitted. A record is removed only on explicit
 * status evidence (retired/expired or a server-epoch change). Elapsed time and
 * registry pressure are never evidence that work finished.
 */

import { api } from './api';

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
}

export interface AnalysisStatus {
  schema: number;
  active: boolean;
  game_id: string;
  cursor: number;
  version: number;
  position_key: string;
  advisor_revision: number;
  receipt?: string;
  receipt_state?: string;
  receipt_epoch?: string;
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
    !Number.isInteger(value.sequence) ||
    typeof value.reconciled_before !== 'number' ||
    !Number.isFinite(value.reconciled_before) ||
    typeof value.reconciled_before_sequence !== 'number' ||
    !Number.isInteger(value.reconciled_before_sequence) ||
    value.game_id !== expected.game_id ||
    value.version !== expected.version ||
    value.cursor !== expected.cursor ||
    value.advisor_revision !== expected.advisor_revision
  ) {
    return null;
  }
  return value as AnalysisReceipt;
}

export function validStatus(
  raw: unknown,
  expected: {
    game_id: string;
    version: number;
    cursor: number;
    advisor_revision: number;
    receipt: string;
  },
): AnalysisStatus | null {
  const value = raw as Partial<AnalysisStatus> | null;
  if (
    !value ||
    value.schema !== 1 ||
    typeof value.active !== 'boolean' ||
    value.game_id !== expected.game_id ||
    value.version !== expected.version ||
    value.cursor !== expected.cursor ||
    value.advisor_revision !== expected.advisor_revision ||
    value.receipt !== expected.receipt ||
    typeof value.position_key !== 'string' ||
    !value.position_key ||
    typeof value.receipt_state !== 'string' ||
    typeof value.receipt_epoch !== 'string'
  ) {
    return null;
  }
  return value as AnalysisStatus;
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
    !Number.isInteger(value.reconciled_before_sequence)
  ) {
    return 'invalid';
  }
  const terminalStates = new Set(['completed', 'failed', 'expired']);
  if (value.terminal !== terminalStates.has(value.state)) return 'invalid';
  if (value.terminal) return 'terminal';
  if (value.state === 'unknown') {
    if (value.epoch !== record.epoch) return 'terminal';
    if (record.sequence <= value.reconciled_before_sequence) return 'terminal';
    return 'unknown';
  }
  return 'active';
}

/**
 * Reconcile every record for one account without revisiting their games.
 * Records are removed only with explicit terminal evidence or the server's
 * reconciled-before watermark/epoch change.
 */
const reconciliation = new Map<string, Promise<number>>();

async function runReconciliation(accountId: string | null, signal?: AbortSignal): Promise<number> {
  let removed = 0;
  const deadline = Date.now() + 5_000;
  for (const [key, record] of [...records]) {
    if (record.account !== accountId) continue;
    if (signal?.aborted || Date.now() >= deadline) break;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(1, deadline - Date.now()));
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    try {
      const response = await fetch(`/api/analysis/receipts/${encodeURIComponent(record.receipt)}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) continue;
      const outcome = receiptOutcome(await response.json(), record);
      if (outcome === 'terminal' && settleRetirement(key, record.receipt)) removed += 1;
    } catch {
      // Unknown transport state: keep the record for a later reconciliation.
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    }
  }
  return removed;
}

export function reconcileRetirements(
  accountId: string | null,
  signal?: AbortSignal,
): Promise<number> {
  const owner = accountId ?? '';
  const existing = reconciliation.get(owner);
  if (existing) return existing;
  const pass = runReconciliation(accountId, signal).finally(() => {
    if (reconciliation.get(owner) === pass) reconciliation.delete(owner);
  });
  reconciliation.set(owner, pass);
  return pass;
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

/** Create and reserve a bounded receipt before submitting fallback work. */
export async function createReceipt(
  request: ReceiptRequest,
  signal: AbortSignal,
): Promise<RetirementRecord | null> {
  const raw = await api<unknown>(
    `${request.basePath}/${request.gameId}/analysis-receipt`,
    { version: request.version, cursor: request.cursor, revision: request.revision },
    'POST',
    signal,
  );
  const created = validReceipt(raw, {
    game_id: request.gameId,
    version: request.version,
    cursor: request.cursor,
    advisor_revision: request.revision,
  });
  if (!created) return null;
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
  };
  if (addRetirement(request.key, record)) return record;
  // Free terminal evidence from this account before refusing a new fallback.
  await reconcileRetirements(request.account, signal);
  return addRetirement(request.key, record) ? record : null;
}

export interface ReceiptResult {
  schema: number;
  receipt: string;
  receipt_state: string;
  receipt_epoch: string;
  status: string;
  terminal: boolean;
  reconciled_before?: number;
  reconciled_before_sequence?: number;
  report?: unknown;
}

export async function fetchReceiptResult(
  record: RetirementRecord,
  signal: AbortSignal,
): Promise<ReceiptResult | null> {
  const raw = await api<unknown>(
    `/analysis/receipts/${encodeURIComponent(record.receipt)}/result`,
    undefined,
    undefined,
    signal,
  );
  const value = raw as Partial<ReceiptResult> | null;
  if (
    !value ||
    value.schema !== 1 ||
    value.receipt !== record.receipt ||
    typeof value.receipt_state !== 'string' ||
    typeof value.receipt_epoch !== 'string' ||
    typeof value.status !== 'string' ||
    typeof value.terminal !== 'boolean'
  ) {
    return null;
  }
  return value as ReceiptResult;
}

export type FallbackOutcome =
  | { kind: 'report'; report: Record<string, unknown> }
  | { kind: 'revision-stale' }
  | { kind: 'stale-game' }
  | { kind: 'failed'; category: string; retryable: boolean };

export interface FallbackRequest extends ReceiptRequest {
  background: boolean;
  historical: boolean;
}

interface FallbackHooks {
  signal: AbortSignal;
  onStage: (stage: 'fallback' | 'searching') => void;
  onWaiting: () => void;
}

/**
 * Submit one logical fallback and retrieve its result through read-only receipt
 * evidence. Retryable admission rejection retires the failed receipt before a
 * new receipt is requested; terminal receipts never submit another computation.
 */
export async function runServerFallback(
  request: FallbackRequest,
  hooks: FallbackHooks,
): Promise<FallbackOutcome> {
  const deadline = Date.now() + 120_000;
  let failures = 0;
  let record = retirementFor(request.key) ?? null;
  const observed = async (
    candidate: RetirementRecord,
  ): Promise<{
    report?: Record<string, unknown>;
    pending?: boolean;
    prepared?: boolean;
    failed?: string;
  }> => {
    const result = await fetchReceiptResult(candidate, hooks.signal);
    if (!result) return { failed: 'server-failed' };
    if (result.report) {
      settleRetirement(request.key, candidate.receipt);
      return { report: result.report as Record<string, unknown> };
    }
    if (result.status === 'pending') {
      return {
        pending: true,
        prepared: result.receipt_state === 'prepared',
      };
    }
    if (result.terminal) {
      settleRetirement(request.key, candidate.receipt);
      return {
        failed: result.status === 'expired' ? 'server-busy' : 'server-failed',
      };
    }
    return { failed: 'server-failed' };
  };
  const poll = async (candidate: RetirementRecord): Promise<FallbackOutcome> => {
    while (!hooks.signal.aborted && Date.now() < deadline) {
      const result = await observed(candidate);
      if (result.report) return { kind: 'report', report: result.report };
      if (result.failed) {
        return { kind: 'failed', category: result.failed, retryable: false };
      }
      if (result.prepared) return { kind: 'failed', category: 'server-busy', retryable: true };
      hooks.onStage('searching');
      await new Promise<void>((resolve) => setTimeout(resolve, 300));
    }
    return { kind: 'failed', category: 'server-busy', retryable: false };
  };

  while (!hooks.signal.aborted && Date.now() < deadline) {
    if (!record) {
      record = await createReceipt(request, hooks.signal);
      if (!record) return { kind: 'failed', category: 'server-busy', retryable: false };
    }
    hooks.onStage('fallback');
    try {
      const report = await api<Record<string, unknown>>(
        `${request.basePath}/${request.gameId}/analysis`,
        {
          version: request.version,
          cursor: request.cursor,
          background: request.background,
          historical: request.historical,
        },
        undefined,
        hooks.signal,
        { 'X-Analysis-Fallback': '1', 'X-Analysis-Receipt': record.receipt },
      );
      if (report.status !== 'pending') {
        settleRetirement(request.key, record.receipt);
        return { kind: 'report', report };
      }
      const outcome = await poll(record);
      if (outcome.kind === 'report') return outcome;
      if (outcome.kind !== 'failed' || !outcome.retryable) return outcome;
      record = null;
      continue;
    } catch (error) {
      if (hooks.signal.aborted) break;
      const code = (error as { code?: string })?.code;
      const status = (error as { status?: number })?.status;
      if (code === 'analysis_revision_stale') return { kind: 'revision-stale' };
      if (code === 'analysis_context_stale') return { kind: 'stale-game' };
      const seen = record ? await observed(record) : { failed: 'server-failed' };
      if (seen.report) return { kind: 'report', report: seen.report };
      if (seen.pending && !seen.prepared) {
        const outcome = await poll(record as RetirementRecord);
        if (outcome.kind === 'report') return outcome;
        if (outcome.kind !== 'failed' || !outcome.retryable) return outcome;
        record = null;
        continue;
      }
      if (seen.failed && code === 'analysis_receipt_invalid') {
        return { kind: 'failed', category: 'server-failed', retryable: false };
      }
      const temporary = status === 503 || status === 429;
      if (!temporary || failures >= 8) {
        return {
          kind: 'failed',
          category: temporary ? 'server-busy' : 'server-failed',
          retryable: false,
        };
      }
      hooks.onWaiting();
      const delay = Math.min(
        30000,
        Math.max(1000 * 2 ** failures, (error as { retryAfterMs?: number }).retryAfterMs ?? 0),
      );
      failures += 1;
      await new Promise<void>((resolve) =>
        setTimeout(resolve, Math.min(delay, Math.max(0, deadline - Date.now()))),
      );
      if (seen.failed) {
        if (retirementFor(request.key)) {
          return { kind: 'failed', category: 'server-failed', retryable: false };
        }
        record = null;
      }
    }
  }
  return { kind: 'failed', category: 'server-busy', retryable: false };
}
export async function waitForReceiptResult(
  record: RetirementRecord,
  signal: AbortSignal,
): Promise<ReceiptResult | null> {
  const deadline = Date.now() + 30_000;
  while (!signal.aborted && Date.now() < deadline) {
    try {
      const raw = await api<unknown>(
        `/analysis/receipts/${encodeURIComponent(record.receipt)}/result`,
        undefined,
        undefined,
        signal,
      );
      const value = raw as Partial<ReceiptResult> | null;
      if (
        !value ||
        value.schema !== 1 ||
        value.receipt !== record.receipt ||
        typeof value.receipt_state !== 'string' ||
        typeof value.receipt_epoch !== 'string' ||
        typeof value.status !== 'string' ||
        typeof value.terminal !== 'boolean'
      ) {
        return null;
      }
      const outcome = receiptOutcome(
        {
          schema: 1,
          receipt: record.receipt,
          epoch: String(value.receipt_epoch),
          state: value.receipt_state,
          terminal: value.terminal,
          reconciled_before: Number(value.reconciled_before ?? 0),
          reconciled_before_sequence: Number(value.reconciled_before_sequence ?? 0),
        },
        record,
      );
      if (outcome === 'invalid') return null;
      if (outcome === 'terminal' || value.terminal) {
        return {
          ...(value as ReceiptResult),
          terminal: true,
          status: value.status === 'unknown' ? 'unknown' : value.status,
        };
      }
    } catch {
      return null;
    }
    await new Promise<void>((resolve) => setTimeout(resolve, 300));
  }
  return null;
}

/** Poll authoritative receipt evidence. Time alone never retires the record. */
export async function waitForReceipt(
  record: RetirementRecord,
  signal: AbortSignal,
): Promise<boolean> {
  const key = retirementKey(
    record.account,
    record.basePath,
    record.gameId,
    record.version,
    record.cursor,
    record.revision,
  );
  const deadline = Date.now() + 30_000;
  while (!signal.aborted && Date.now() < deadline) {
    try {
      const raw = await api<unknown>(
        `/analysis/receipts/${encodeURIComponent(record.receipt)}`,
        undefined,
        undefined,
        signal,
      );
      const outcome = receiptOutcome(raw, record);
      if (outcome === 'terminal') {
        settleRetirement(key, record.receipt);
        return true;
      }
      if (outcome !== 'active' && outcome !== 'unknown') return false;
    } catch {
      return false;
    }
    await new Promise<void>((resolve) => setTimeout(resolve, 300));
  }
  return false;
}
