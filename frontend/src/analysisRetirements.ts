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
