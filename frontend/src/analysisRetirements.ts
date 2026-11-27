/**
 * Session-scoped retirement records for admitted server fallback work.
 *
 * The records deliberately live outside the React component: navigation,
 * unmount/remount, account transitions and route changes must not forget that a
 * server request may still be admitted. A record is removed only on explicit
 * status evidence (retired/expired or a server-epoch change). Elapsed time and
 * registry pressure are never evidence that work finished.
 */

export interface RetirementRecord {
  receipt: string;
  epoch: string;
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
  expected: Omit<AnalysisReceipt, 'schema' | 'receipt' | 'epoch'>,
): AnalysisReceipt | null {
  const value = raw as Partial<AnalysisReceipt> | null;
  if (
    !value ||
    value.schema !== 1 ||
    typeof value.receipt !== 'string' ||
    !value.receipt ||
    typeof value.epoch !== 'string' ||
    !value.epoch ||
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
