import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_GLOBAL_RETIREMENTS,
  MAX_RETIREMENTS,
  __resetRetirements,
  addRetirement,
  reconcileRetirements,
  retirementCount,
  retirementFor,
  retirementKey,
  runServerFallback,
  settleRetirement,
} from './analysisRetirements';
import { setCSRF } from './api';

function record(receipt: string) {
  return {
    receipt,
    epoch: 'epoch',
    createdAt: 1000,
    sequence: 1,
    account: 'account-1',
    gameId: 'g1',
    basePath: '/games',
    version: 0,
    cursor: 0,
    revision: 1,
    positionKey: 'canonical-position',
    ply: 0,
    turn: 'attackers' as const,
  };
}

beforeEach(() => setCSRF('session-1', 'account-1'));
afterEach(() => {
  setCSRF('');
  __resetRetirements();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('analysis retirement records', () => {
  it('are bounded without evicting older potentially active work', () => {
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      expect(
        addRetirement(retirementKey('a', '/games', `g${index}`, 0, 0, 1), record(`r${index}`)),
      ).toBe(true);
    }
    expect(
      addRetirement(retirementKey('a', '/games', 'overflow', 0, 0, 1), record('overflow')),
    ).toBe(false);
    expect(retirementCount()).toBe(MAX_RETIREMENTS);
    expect(retirementFor(retirementKey('a', '/games', 'g0', 0, 0, 1))?.receipt).toBe('r0');
  });

  it('are removed only by the exact receipt with explicit evidence', () => {
    const key = retirementKey('a', '/games', 'g1', 0, 0, 1);
    addRetirement(key, record('receipt'));
    expect(settleRetirement(key, 'other')).toBe(false);
    expect(retirementCount()).toBe(1);
    expect(settleRetirement(key, 'receipt')).toBe(true);
    expect(retirementCount()).toBe(0);
  });

  it('keeps records independent per account and position identity', () => {
    const first = retirementKey('a', '/games', 'g1', 0, 0, 1);
    const second = retirementKey('b', '/games', 'g1', 0, 0, 1);
    expect(addRetirement(first, record('first'))).toBe(true);
    expect(addRetirement(second, record('second'))).toBe(true);
    expect(retirementFor(first)?.receipt).toBe('first');
    expect(retirementFor(second)?.receipt).toBe('second');
  });

  it('reconciles terminal, expired-floor and newer-epoch receipts without revisiting games', async () => {
    const terminalKey = retirementKey('account-1', '/games', 'g1', 0, 0, 1);
    const floorKey = retirementKey('account-1', '/games', 'g2', 0, 0, 1);
    const epochKey = retirementKey('account-1', '/games', 'g3', 0, 0, 1);
    const activeKey = retirementKey('account-1', '/games', 'g4', 0, 0, 1);
    addRetirement(terminalKey, record('terminal'));
    addRetirement(floorKey, { ...record('floor'), createdAt: 500, sequence: 2 });
    addRetirement(epochKey, { ...record('epoch'), epoch: 'old-epoch' });
    addRetirement(activeKey, record('active'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        const statuses: Record<string, Record<string, unknown>> = {
          terminal: { state: 'completed', terminal: true },
          floor: { state: 'unknown', terminal: false },
          epoch: { state: 'unknown', terminal: false },
          active: { state: 'active', terminal: false },
        };
        return {
          ok: true,
          json: async () => ({
            schema: 1,
            receipt: id,
            epoch: id === 'epoch' ? 'new-epoch' : 'epoch',
            reconciled_before: id === 'floor' ? 600 : 0,
            reconciled_before_sequence: id === 'floor' ? 2 : 0,
            ...statuses[id],
          }),
        } as Response;
      }),
    );
    expect(await reconcileRetirements('account-1')).toBe(3);
    expect(retirementFor(terminalKey)).toBeUndefined();
    expect(retirementFor(floorKey)).toBeUndefined();
    expect(retirementFor(epochKey)).toBeUndefined();
    expect(retirementFor(activeKey)).toBeDefined();
    vi.unstubAllGlobals();
  });

  it('does not treat malformed status or another account as terminal evidence', async () => {
    const key = retirementKey('account-1', '/games', 'g1', 0, 0, 1);
    const other = retirementKey('account-2', '/games', 'g2', 0, 0, 1);
    addRetirement(key, record('receipt'));
    addRetirement(other, { ...record('foreign'), account: 'account-2' });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({ active: false }),
          }) as Response,
      ),
    );
    expect(await reconcileRetirements('account-1')).toBe(0);
    expect(retirementFor(key)).toBeDefined();
    expect(retirementFor(other)).toBeDefined();
    vi.unstubAllGlobals();
  });

  it('does not let a previous account consume the current account capacity', () => {
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      expect(
        addRetirement(retirementKey('previous', '/games', `g${index}`, 0, 0, 1), {
          ...record(`previous-${index}`),
          account: 'previous',
        }),
      ).toBe(true);
    }
    expect(
      addRetirement(retirementKey('current', '/games', 'new', 0, 0, 1), {
        ...record('current'),
        account: 'current',
      }),
    ).toBe(true);
  });

  it('recovers bounded capacity after repeated legitimate completed fallbacks', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        return {
          ok: true,
          json: async () => ({
            schema: 1,
            receipt: id,
            epoch: 'epoch',
            reconciled_before: 0,
            reconciled_before_sequence: 0,
            state: 'completed',
            terminal: true,
          }),
        } as Response;
      }),
    );
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      addRetirement(
        retirementKey('account-1', '/games', `g${index}`, 0, 0, 1),
        record(`r${index}`),
      );
    }
    expect(retirementCount()).toBe(MAX_RETIREMENTS);
    await reconcileRetirements('account-1');
    expect(retirementCount()).toBe(0);
    expect(addRetirement(retirementKey('account-1', '/games', 'new', 0, 0, 1), record('new'))).toBe(
      true,
    );
    vi.unstubAllGlobals();
  });
});

const key = retirementKey('account-1', '/games', 'g1', 0, 0, 1);
const request = {
  key,
  account: 'account-1',
  basePath: '/games',
  gameId: 'g1',
  version: 0,
  cursor: 0,
  revision: 1,
  background: true,
  historical: true,
};
const evidence = {
  schema: 1,
  receipt: 'receipt',
  epoch: 'epoch',
  reconciled_before: 0,
  reconciled_before_sequence: 0,
};
const position = {
  game_id: 'g1',
  version: 0,
  cursor: 0,
  ply: 0,
  side_to_move: 'attackers',
  position_key: 'canonical-position',
  advisor_revision: 1,
};
const created = { ...evidence, ...position, sequence: 1, created_at: 1000 };
const completed = {
  schema: 1,
  receipt: 'receipt',
  receipt_epoch: 'epoch',
  receipt_state: 'completed',
  reconciled_before: 0,
  reconciled_before_sequence: 0,
  status: 'complete',
  terminal: true,
  report: {
    ...position,
    status: 'complete',
    evaluations: [{ agent_id: 'advisor', source: 'heuristic' }],
    hint: null,
  },
};

function fallback(controller = new AbortController()) {
  const hooks = { signal: controller.signal, onStage: vi.fn(), onWaiting: vi.fn() };
  return { task: runServerFallback(request, hooks), controller, hooks };
}

function overloaded(status = 503, retryAfter = '1') {
  return Response.json(
    { detail: 'Busy', code: 'analysis_overloaded' },
    {
      status,
      headers: { 'Retry-After': retryAfter },
    },
  );
}

describe('bounded fallback recovery', () => {
  it.each([
    ['analysis_revision_stale', 'revision-stale'],
    ['analysis_context_stale', 'stale-game'],
    [undefined, 'cancelled'],
  ])('preserves supersession reason %s after authoritative retirement', async (code, kind) => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(Response.json({ status: 'pending' }))
      .mockResolvedValueOnce(
        Response.json({
          ...completed,
          report: undefined,
          receipt_state: 'failed',
          status: 'superseded',
          code,
        }),
      );
    vi.stubGlobal('fetch', fetcher);
    expect(await fallback().task).toEqual({ kind });
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis')).toHaveLength(1);
    expect(retirementCount()).toBe(0);
  });

  it('leaves genuine server failures classified as failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(Response.json(created))
        .mockResolvedValueOnce(Response.json({ status: 'pending' }))
        .mockResolvedValueOnce(
          Response.json({
            ...completed,
            report: undefined,
            receipt_state: 'failed',
            status: 'failed',
          }),
        ),
    );
    expect(await fallback().task).toEqual({ kind: 'failed', category: 'server-failed' });
    expect(retirementCount()).toBe(0);
  });

  it('ignores supersession evidence read by an obsolete session', async () => {
    let deliver!: (response: Response) => void;
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(Response.json(created))
        .mockResolvedValueOnce(Response.json({ status: 'pending' }))
        .mockImplementationOnce(
          () =>
            new Promise<Response>((resolve) => {
              deliver = resolve;
            }),
        ),
    );
    const task = fallback().task;
    await vi.waitFor(() => expect(deliver).toBeDefined());
    setCSRF('new-token', 'other-account');
    deliver(
      Response.json({
        ...completed,
        report: undefined,
        receipt_state: 'failed',
        status: 'superseded',
        code: 'analysis_revision_stale',
      }),
    );
    expect(await task).toMatchObject({ kind: 'failed' });
    expect(retirementFor(key)?.receipt).toBe(created.receipt);
  });

  it.each([503, 429])('retries a temporary %i when creating the receipt', async (status) => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(overloaded(status, '2'))
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(Response.json(completed.report))
      .mockResolvedValueOnce(Response.json(completed));
    vi.stubGlobal('fetch', fetcher);
    const { task } = fallback();
    const outcome = task.catch((error) => error);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await outcome).toMatchObject({ kind: 'report' });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/games/g1/analysis-receipt',
      '/api/games/g1/analysis-receipt',
      '/api/games/g1/analysis',
      '/api/analysis/receipts/receipt/result',
    ]);
    expect(retirementCount()).toBe(0);
  });

  it('retries consecutive read failures without sending another computation', async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(Response.json({ status: 'pending' }))
      .mockResolvedValueOnce(overloaded())
      .mockRejectedValueOnce(new TypeError('network unreachable'))
      .mockResolvedValueOnce(overloaded(429))
      .mockResolvedValueOnce(Response.json(completed));
    vi.stubGlobal('fetch', fetcher);
    const outcome = fallback().task.catch((error) => error);
    await vi.runAllTimersAsync();
    expect(await outcome).toMatchObject({ kind: 'report' });
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis')).toHaveLength(1);
    expect(retirementCount()).toBe(0);
  });

  it('reuses a prepared receipt only after a structured rejection before admission', async () => {
    vi.useFakeTimers();
    const rejected = Response.json(
      { detail: 'Busy', code: 'analysis_rate_limited' },
      { status: 429 },
    );
    const prepared = {
      ...completed,
      report: undefined,
      receipt_state: 'prepared',
      status: 'pending',
      terminal: false,
    };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(rejected)
      .mockResolvedValueOnce(Response.json(prepared))
      .mockResolvedValueOnce(Response.json({ status: 'pending' }))
      .mockResolvedValueOnce(Response.json(completed));
    vi.stubGlobal('fetch', fetcher);
    const { task } = fallback();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(await task).toMatchObject({ kind: 'report' });
    const submissions = fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis');
    expect(submissions).toHaveLength(2);
    expect(submissions.map(([, options]) => options.headers['X-Analysis-Receipt'])).toEqual([
      'receipt',
      'receipt',
    ]);
    expect(retirementCount()).toBe(0);
  });

  it('does not submit a receipt that expires during Retry-After', async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(
        Response.json(
          { code: 'analysis_rate_limited' },
          {
            status: 429,
            headers: { 'Retry-After': '2' },
          },
        ),
      )
      .mockResolvedValueOnce(
        Response.json({
          ...completed,
          report: undefined,
          receipt_state: 'expired',
          status: 'expired',
        }),
      );
    vi.stubGlobal('fetch', fetcher);
    const { task } = fallback();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await task).toMatchObject({ kind: 'failed', category: 'server-busy' });
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis')).toHaveLength(1);
    expect(retirementCount()).toBe(0);
  });

  it('observes uncertain submission and unknown status without re-admitting', async () => {
    vi.useFakeTimers();
    const nonTerminal = { ...completed, report: undefined, status: 'pending', terminal: false };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockRejectedValueOnce(new TypeError('submission connection interrupted'))
      .mockResolvedValueOnce(Response.json({ ...nonTerminal, receipt_state: 'prepared' }))
      .mockResolvedValueOnce(
        Response.json({ ...nonTerminal, receipt_state: 'unknown', status: 'unknown' }),
      )
      .mockResolvedValueOnce(Response.json({ ...nonTerminal, receipt_state: 'active' }))
      .mockResolvedValueOnce(Response.json(completed));
    vi.stubGlobal('fetch', fetcher);
    const { task } = fallback();
    await vi.advanceTimersByTimeAsync(600);
    expect(retirementFor(key)?.receipt).toBe('receipt');
    await vi.advanceTimersByTimeAsync(300);
    expect(await task).toMatchObject({ kind: 'report' });
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis')).toHaveLength(1);
    expect(retirementCount()).toBe(0);
  });

  it.each([0, 1, 2])(
    'aborts stalled HTTP at the overall deadline during stage %i',
    async (stall) => {
      vi.useFakeTimers();
      let stalledSignal: AbortSignal | undefined;
      const replies = [created, { status: 'pending' }, completed];
      let index = 0;
      const fetcher = vi.fn(async (_url, options: RequestInit) => {
        const step = index++;
        if (step !== stall) return Response.json(replies[step]);
        stalledSignal = options.signal as AbortSignal;
        return new Promise<Response>((_resolve, reject) => {
          stalledSignal?.addEventListener('abort', () => reject(stalledSignal?.reason));
        });
      });
      vi.stubGlobal('fetch', fetcher);
      const { task, controller } = fallback();
      const outcome = task.catch((error) => error);
      await vi.advanceTimersByTimeAsync(120_000);
      expect(stalledSignal?.aborted).toBe(true);
      expect(controller.signal.aborted).toBe(false);
      expect(await outcome).toMatchObject({ kind: 'failed', category: 'server-busy' });
      expect(retirementCount()).toBe(stall === 0 ? 0 : 1);
    },
  );

  it.each([
    { terminal: false },
    { receipt_epoch: 'wrong-epoch' },
    { report: { ...completed.report, position_key: 'wrong-position' } },
    { report: { ...completed.report, advisor_revision: 2 } },
  ])('keeps retirement evidence when the result contradicts the receipt (%j)', async (patch) => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(created))
      .mockResolvedValueOnce(Response.json(completed.report))
      .mockResolvedValueOnce(Response.json({ ...completed, ...patch }));
    vi.stubGlobal('fetch', fetcher);
    expect(await fallback().task).toMatchObject({ kind: 'failed' });
    expect(retirementFor(key)?.receipt).toBe('receipt');
  });

  it.each(['policy', 'account'])('ends a retry delay when its %s changes', async (cause) => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValueOnce(overloaded(429, '30'));
    vi.stubGlobal('fetch', fetcher);
    const { task, controller, hooks } = fallback();
    const outcome = task.catch((error) => error);
    await vi.advanceTimersByTimeAsync(1);
    expect(hooks.onWaiting).toHaveBeenCalledTimes(1);
    if (cause === 'policy') controller.abort();
    else setCSRF('another-session', 'another-account');
    await vi.advanceTimersByTimeAsync(0);
    expect(await outcome).toMatchObject({ kind: 'failed' });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('reconciliation ownership', () => {
  it.each(['', 'another-session'])(
    'ignores late evidence after session changes to %s',
    async (token) => {
      addRetirement(key, record('receipt'));
      let deliver!: (data: Response) => void;
      const fetcher = vi.fn(
        (_url, _options: RequestInit) =>
          new Promise<Response>((resolve) => {
            deliver = resolve;
          }),
      );
      vi.stubGlobal('fetch', fetcher);
      const task = reconcileRetirements('account-1');
      setCSRF(token, token ? 'account-2' : null);
      expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(true);
      deliver(Response.json({ ...evidence, state: 'completed', terminal: true }));
      expect(await task).toBe(0);
      expect(retirementFor(key)).toBeDefined();
    },
  );

  it('coalesces consumers and cancels only the caller who leaves', async () => {
    addRetirement(key, record('receipt'));
    let deliver!: (data: Response) => void;
    const fetcher = vi.fn(
      (_url, _options: RequestInit) =>
        new Promise<Response>((resolve) => {
          deliver = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    const first = reconcileRetirements('account-1', controller.signal);
    const second = reconcileRetirements('account-1');
    controller.abort();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(false);
    deliver(Response.json({ ...evidence, state: 'completed', terminal: true }));
    expect(await first).toBe(0);
    expect(await second).toBe(1);
    expect(retirementCount()).toBe(0);
  });

  it('reconciles the current account before creating a receipt at its capacity', async () => {
    for (let index = 0; index < MAX_RETIREMENTS; index += 1) {
      addRetirement(
        retirementKey('account-1', '/games', `old-game-${index}`, 0, 0, 1),
        record(`old-${index}`),
      );
    }
    const fetcher = vi.fn(async (url: string) => {
      if (url.endsWith('/analysis-receipt')) return Response.json(created);
      if (url.endsWith('/analysis')) return Response.json({ status: 'pending' });
      if (url.endsWith('/result')) return Response.json(completed);
      return Response.json({
        ...evidence,
        receipt: url.split('/').at(-1),
        state: 'completed',
        terminal: true,
      });
    });
    vi.stubGlobal('fetch', fetcher);
    expect(await fallback().task).toMatchObject({ kind: 'report' });
    expect(fetcher.mock.calls[0][0]).toBe('/api/analysis/receipts/old-0');
    expect(retirementCount()).toBe(0);
  });

  it('recovers global capacity only with authoritative retirement evidence', async () => {
    for (let index = 0; index < MAX_GLOBAL_RETIREMENTS; index += 1) {
      const account = `previous-${Math.floor(index / MAX_RETIREMENTS)}`;
      expect(
        addRetirement(retirementKey(account, '/games', `g${index}`, 0, 0, 1), {
          ...record(`old-${index}`),
          account,
          sequence: index + 1,
        }),
      ).toBe(true);
    }
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ ...created, sequence: 257 }))
      .mockResolvedValueOnce(
        Response.json({ ...created, sequence: 258, reconciled_before_sequence: 1 }),
      )
      .mockResolvedValueOnce(Response.json({ status: 'pending' }))
      .mockResolvedValueOnce(Response.json(completed));
    vi.stubGlobal('fetch', fetcher);
    expect(await fallback().task).toMatchObject({ kind: 'failed' });
    expect(retirementCount()).toBe(MAX_GLOBAL_RETIREMENTS);
    expect(await fallback().task).toMatchObject({ kind: 'report' });
    expect(retirementCount()).toBe(MAX_GLOBAL_RETIREMENTS - 1);
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/games/g1/analysis')).toHaveLength(1);
  });
});
