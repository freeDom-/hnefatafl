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
  account: string | null;
  gameId: string;
  basePath: string;
  version: number;
  cursor: number;
  revision: number;
}

export const MAX_RETIREMENTS = 64;
const records = new Map<string, RetirementRecord>();

export interface AnalysisReceipt {
  schema: number;
  receipt: string;
  epoch: string;
  created_at: number;
  reconciled_before: number;
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
    typeof value.reconciled_before !== 'number' ||
    !Number.isFinite(value.reconciled_before) ||
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
  if (records.size >= MAX_RETIREMENTS) return false;
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
    !Number.isFinite(value.reconciled_before)
  ) {
    return 'invalid';
  }
  if (value.terminal) return 'terminal';
  if (value.state === 'unknown') {
    if (value.epoch !== record.epoch) return 'terminal';
    if (record.createdAt <= value.reconciled_before) return 'terminal';
    return 'unknown';
  }
  return 'active';
}

/**
 * Reconcile every record for one account without revisiting their games.
 * Records are removed only with explicit terminal evidence or the server's
 * reconciled-before watermark/epoch change.
 */
export async function reconcileRetirements(accountId: string | null): Promise<number> {
  let removed = 0;
  for (const [key, record] of [...records]) {
    if (record.account !== accountId) continue;
    try {
      const response = await fetch(`/api/analysis/receipts/${encodeURIComponent(record.receipt)}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { accept: 'application/json' },
      });
      if (!response.ok) continue;
      const outcome = receiptOutcome(await response.json(), record);
      if (outcome === 'terminal' && settleRetirement(key, record.receipt)) removed += 1;
    } catch {
      // Unknown transport state: keep the record for a later reconciliation.
    }
  }
  return removed;
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
    account: request.account,
    gameId: request.gameId,
    basePath: request.basePath,
    version: request.version,
    cursor: request.cursor,
    revision: request.revision,
  };
  return addRetirement(request.key, record) ? record : null;
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
