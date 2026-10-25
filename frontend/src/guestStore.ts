import { parseGuestRecord, type GuestRecord } from './guestTypes';
export const GUEST_DB = 'hnefatafl-guest-v1';
export class GuestConflict extends Error {}
export type DeviceSlot = { revision: string | null; raw: unknown | null };
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('Browser storage unavailable.'));
      return;
    }
    const request = indexedDB.open(GUEST_DB, 1);
    let settled = false;
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    };
    const timer = setTimeout(() => fail(new Error('Browser storage timed out.')), 5000);
    request.onupgradeneeded = () => {
      request.result.createObjectStore('device');
    };
    request.onerror = () => fail(request.error ?? new Error('Browser storage unavailable.'));
    request.onblocked = () => fail(new Error('Browser storage is blocked.'));
    request.onsuccess = () => {
      clearTimeout(timer);
      if (settled) {
        request.result.close();
        return;
      }
      settled = true;
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
  });
}
/** One atomic slot + an opaque revision; no retained revisions or account IDs. */
export async function readGuestSlot(): Promise<DeviceSlot> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('device', 'readonly');
    const store = tx.objectStore('device');
    const revision = store.get('revision');
    const game = store.get('game');
    tx.oncomplete = () => {
      db.close();
      const r = revision.result ?? null;
      if (r !== null && (typeof r !== 'string' || !/^[0-9a-f-]{36}$/.test(r)))
        reject(new Error('Invalid device storage metadata.'));
      else resolve({ revision: r, raw: game.result ?? null });
    };
    tx.onabort = tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error('Cannot read device storage.'));
    };
  });
}
export async function writeGuestSlot(
  expected: string | null,
  record: GuestRecord,
): Promise<string> {
  const raw = JSON.stringify(record);
  parseGuestRecord(raw);
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('device', 'readwrite');
    const store = tx.objectStore('device');
    let conflict = false;
    const next = crypto.randomUUID();
    const request = store.get('revision');
    request.onsuccess = () => {
      const actual = request.result ?? null;
      if (actual !== expected) {
        conflict = true;
        tx.abort();
        return;
      }
      try {
        store.put(next, 'revision');
        store.put(raw, 'game');
      } catch {
        tx.abort();
      }
    };
    tx.oncomplete = () => {
      db.close();
      resolve(next);
    };
    tx.onabort = () => {
      db.close();
      reject(
        conflict
          ? new GuestConflict('Another tab changed the device game. Reload it before continuing.')
          : (tx.error ?? new Error('Cannot save device game.')),
      );
    };
    tx.onerror = () => {}; // onabort owns rejection, preserving the previous committed slot.
  });
}

/** Explicit device clear also repairs damaged metadata. New tokens invalidate stale tabs. */
export async function clearGuestSlot(): Promise<string> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('device', 'readwrite');
    const next = crypto.randomUUID();
    try {
      const store = tx.objectStore('device');
      store.put(next, 'revision');
      store.delete('game');
    } catch {
      tx.abort();
    }
    tx.oncomplete = () => {
      db.close();
      resolve(next);
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error ?? new Error('Cannot clear device game.'));
    };
    tx.onerror = () => {};
  });
}
