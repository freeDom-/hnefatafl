/** Guest-only transport. No API helper, auth, account IDs or online adapters. */
export class GuestEngine {
  private worker: Worker | null = null;
  notices = '';
  private abort = new AbortController();
  private sequence = 0;
  private pending: {
    id: number;
    resolve: (value: unknown) => void;
    reject: (error: Error) => void;
    timeout: ReturnType<typeof setTimeout>;
  } | null = null;
  get ready() {
    return this.worker !== null && !this.abort.signal.aborted;
  }
  async start() {
    const timer = setTimeout(() => this.abort.abort(), 15000);
    try {
      const response = await fetch('/assets/guest-manifest.json', {
        credentials: 'omit',
        signal: this.abort.signal,
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('Guest runtime unavailable.');
      const manifest = await response.json();
      if (
        this.abort.signal.aborted ||
        manifest.schema !== 1 ||
        !/^\/assets\/guest-[a-f0-9]{64}\/$/.test(manifest.base)
      )
        throw new Error('Guest runtime unavailable.');
      this.notices = manifest.base + 'licenses/NOTICE.md';
      this.worker = new Worker(manifest.base + 'worker.js');
      this.worker.onmessage = ({ data }) => {
        const pending = this.pending;
        if (!pending || pending.id !== data.id) return;
        clearTimeout(pending.timeout);
        this.pending = null;
        if (data.error) pending.reject(new Error(data.error));
        else pending.resolve(data.value);
      };
      this.worker.onerror = () => this.dispose('Guest engine failed. Retry loading it.');
    } finally {
      clearTimeout(timer);
    }
  }
  request<T>(request: Record<string, unknown>, timeout = 15000): Promise<T> {
    if (!this.worker || this.abort.signal.aborted)
      return Promise.reject(new Error('Guest engine unavailable. Retry loading it.'));
    if (this.pending) return Promise.reject(new Error('Guest engine is busy.'));
    return new Promise<T>((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(
        () => this.dispose('Guest computation timed out. Retry loading the engine.'),
        timeout,
      );
      this.pending = { id, resolve: (value) => resolve(value as T), reject, timeout: timer };
      this.worker!.postMessage({ id, request });
    });
  }
  dispose(message = 'Guest computation cancelled.') {
    this.abort.abort();
    this.worker?.terminate();
    this.worker = null;
    if (this.pending) {
      clearTimeout(this.pending.timeout);
      this.pending.reject(new Error(message));
      this.pending = null;
    }
  }
}
