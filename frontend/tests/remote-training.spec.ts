import { openCreation, selectWorkshopJob } from './workshop-helpers';
import { randomUUID } from 'node:crypto';
import { expect, test, type APIRequestContext, type Page } from './auth-fixture';

const api = 'http://127.0.0.1:8001/api';

async function openWorkshop(page: Page) {
  await page.goto('/dashboard?setup=single-player');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
}

async function queue(page: Page, name: string) {
  await openCreation(page);
  await page.getByRole('textbox', { name: 'Model name', exact: true }).fill(name);
  const response = page.waitForResponse(
    (entry) => entry.url().endsWith('/api/training/jobs') && entry.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Queue training', exact: true }).click();
  const created = await response;
  expect(created.ok()).toBe(true);
  return created.json();
}

async function post(request: APIRequestContext, path: string, token: string, data: unknown) {
  const response = await request.post(`${api}/worker${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

test('remote worker setup queues parallel jobs and retains independent reported progress', async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const name = `Cloud raven ${info.project.name}`;
  let workerId: string | undefined;
  await openWorkshop(page);
  await page.getByRole('button', { name: /Remote workers/ }).click();
  await page.getByRole('button', { name: 'Connect a worker', exact: true }).click();
  const connect = page.getByRole('dialog', { name: 'Connect a remote worker', exact: true });
  await connect.getByRole('textbox', { name: 'Worker name', exact: true }).fill(name);
  await connect.getByRole('spinbutton', { name: 'Parallel jobs', exact: true }).fill('2');
  await connect.getByRole('button', { name: 'Create worker', exact: true }).click();
  const credential = connect.getByLabel('Worker token', { exact: true });
  await expect(credential).toBeVisible();
  const token = await credential.inputValue();
  expect(token.length).toBeGreaterThan(20);
  await expect(credential).toHaveAttribute('type', 'password');
  await expect(connect.locator('.worker-command')).toHaveText(
    `read -r -s HNEFATAFL_WORKER_TOKEN\nexport HNEFATAFL_WORKER_TOKEN\n.venv/bin/python -m hnefatafl.cli worker --server ${new URL(page.url()).origin} --work-dir ./data/remote-worker --concurrency 2`,
  );
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(token);
  const workers = await (await request.get(`${api}/training/workers`)).json();
  const worker = workers.find((entry: { name: string }) => entry.name === name);
  workerId = worker.id;
  const currentVersion = worker.compatibility.server_version;
  expect(JSON.stringify(workers)).not.toContain(token);
  try {
    await connect.getByRole('button', { name: 'Close worker setup', exact: true }).click();
    await openCreation(page);
    const reported = await post(request, '/heartbeat', token, {
      capabilities: {
        protocol_version: 1,
        package_version: currentVersion,
        cuda_available: true,
        cuda_devices: ['Test cloud accelerator'],
        device: 'cuda',
      },
    });
    expect(reported.worker.compatibility.eligible).toBe(true);
    await page
      .getByRole('combobox', { name: 'Run training on', exact: true })
      .selectOption(`worker:${workerId}`);
    await page
      .getByRole('combobox', { name: 'Training preset', exact: true })
      .selectOption('smoke');
    await page.getByRole('combobox', { name: 'Battlefield', exact: true }).selectOption('brandubh');
    await page.getByRole('spinbutton', { name: 'Training iterations', exact: true }).fill('3');
    const first = await queue(page, `Cloud Ulf ${info.project.name}`);
    const second = await queue(page, `Cloud Freydis ${info.project.name}`);
    const firstClaim = (await post(request, '/claim', token, { request_id: randomUUID() })).job;
    const secondClaim = (await post(request, '/claim', token, { request_id: randomUUID() })).job;
    expect(new Set([firstClaim.id, secondClaim.id])).toEqual(new Set([first.id, second.id]));
    expect((await post(request, '/claim', token, { request_id: randomUUID() })).job).toBeNull();
    const byId = new Map([firstClaim, secondClaim].map((claim) => [claim.id, claim]));
    await post(request, `/jobs/${first.id}/progress`, token, {
      lease_token: byId.get(first.id).lease_token,
      sequence: 1,
      progress: {
        iteration: 1,
        total_iterations: 3,
        stage: 'iteration_complete',
        history: [{ iteration: 1, policy_loss: 1.5, value_loss: 0.4, evaluation_score: 0.25 }],
      },
    });
    await post(request, `/jobs/${second.id}/progress`, token, {
      lease_token: byId.get(second.id).lease_token,
      sequence: 1,
      progress: {
        iteration: 2,
        total_iterations: 3,
        stage: 'iteration_complete',
        history: [
          { iteration: 1, policy_loss: 2.5, value_loss: 0.8, evaluation_score: 0 },
          { iteration: 2, policy_loss: 2, value_loss: 0.6, evaluation_score: 0.5 },
        ],
      },
    });
    await selectWorkshopJob(page, first.id, `Cloud Ulf ${info.project.name}`);
    await expect(page.locator('.job-detail .job-status')).toHaveText('running');
    await expect(
      page.getByRole('img', { name: 'Loss by iteration', exact: true }).locator('circle'),
    ).toHaveCount(2);
    await selectWorkshopJob(page, second.id, `Cloud Freydis ${info.project.name}`);
    await expect(
      page.getByRole('img', { name: 'Loss by iteration', exact: true }).locator('circle'),
    ).toHaveCount(4);

    const waiting = await queue(page, `Cloud waiting ${info.project.name}`);
    expect(waiting.status).toBe('queued');
    await page.getByRole('button', { name: 'Stop training', exact: true }).click();
    await expect(page.locator('.job-detail .job-status')).toHaveText('cancelled');
    expect((await (await request.get(`${api}/training/jobs/${waiting.id}`)).json()).status).toBe(
      'cancelled',
    );

    await page.reload();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: 'AI workshop', exact: true }).click();
    await expect(page.getByLabel('Worker token', { exact: true })).toHaveCount(0);
    await selectWorkshopJob(page, second.id, `Cloud Freydis ${info.project.name}`);
    await expect(
      page.getByRole('img', { name: 'Loss by iteration', exact: true }).locator('circle'),
    ).toHaveCount(4);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
    expect(errors).toEqual([]);
  } finally {
    if (workerId) await request.delete(`${api}/training/workers/${workerId}`);
  }
});

test('revoking a worker hides it immediately and after reload while authorized machines stay visible', async ({
  page,
  request,
}, info) => {
  const enrolled: { worker: { id: string; name: string }; token: string }[] = [];
  try {
    for (const state of ['Online', 'Disconnected', 'Revoke']) {
      const response = await request.post(`${api}/training/workers`, {
        data: { name: `${state} raven ${info.project.name}`, capacity: 1 },
      });
      expect(response.ok()).toBe(true);
      enrolled.push(await response.json());
    }
    const [online, offline, revoked] = enrolled;
    await post(request, '/heartbeat', online.token, { capabilities: { protocol_version: 1 } });
    await openWorkshop(page);
    await page.getByRole('button', { name: /Remote workers/ }).click();
    const card = (id: string) => page.locator(`[data-worker-id="${id}"]`);
    await expect(card(online.worker.id)).toBeVisible();
    await expect(card(online.worker.id).locator('.worker-status')).toHaveText('online');
    await expect(card(offline.worker.id)).toBeVisible();
    await expect(card(offline.worker.id).locator('.worker-status')).toHaveText('offline');
    await card(revoked.worker.id)
      .getByRole('button', { name: /Revoke worker/ })
      .click();
    const confirm = page.getByRole('dialog', { name: `Revoke worker ${revoked.worker.name}?` });
    await confirm.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(card(revoked.worker.id)).toHaveCount(0);
    await expect(card(online.worker.id)).toBeVisible();
    await expect(card(offline.worker.id)).toBeVisible();
    await openWorkshop(page);
    await page.getByRole('button', { name: /Remote workers/ }).click();
    await expect(card(revoked.worker.id)).toHaveCount(0);
    await expect(card(online.worker.id)).toBeVisible();
    await expect(card(offline.worker.id)).toBeVisible();
    const reconnect = await request.post(`${api}/worker/heartbeat`, {
      headers: { Authorization: `Bearer ${revoked.token}` },
      data: { capabilities: { protocol_version: 1 } },
    });
    expect(reconnect.status()).toBe(403);
  } finally {
    for (const entry of enrolled)
      await request.delete(`${api}/training/workers/${entry.worker.id}`);
  }
});

test('OpenVINO is explicit, hardware aware and stays selected across targets and presets', async ({
  page,
  request,
}, info) => {
  const response = await request.post(`${api}/training/workers`, {
    data: { name: `Intel ${info.project.name}`, capacity: 1 },
  });
  expect(response.ok()).toBe(true);
  const { worker, token } = await response.json();
  try {
    const reported = await post(request, '/heartbeat', token, {
      capabilities: {
        protocol_version: 1,
        package_version: worker.compatibility.server_version,
        inference_backends: ['pytorch', 'openvino'],
        openvino_version: '2026.4.1',
        openvino_devices: [
          { id: 'GPU', name: 'Intel Iris Xe (reported test device)', available: true },
        ],
      },
    });
    expect(reported.worker.compatibility.eligible).toBe(true);
    await openWorkshop(page);
    await openCreation(page);
    await page
      .getByRole('combobox', { name: 'Run training on', exact: true })
      .selectOption(`worker:${worker.id}`);
    await expect(page.getByRole('option', { name: /Intel Iris Xe/ })).toBeEnabled();
    await expect(
      page.getByRole('combobox', { name: 'Neural inference backend', exact: true }),
    ).toHaveValue('pytorch');
    await page.getByRole('combobox', { name: 'Training device', exact: true }).selectOption('cpu');
    await page
      .getByRole('combobox', { name: 'Neural inference backend', exact: true })
      .selectOption('openvino');
    await page
      .getByRole('combobox', { name: 'Training preset', exact: true })
      .selectOption('smoke');
    await expect(
      page.getByRole('combobox', { name: 'Neural inference backend', exact: true }),
    ).toHaveValue('openvino');
    await expect(page.getByRole('combobox', { name: 'Training device', exact: true })).toHaveValue(
      'cpu',
    );
    await page
      .getByRole('combobox', { name: 'Run training on', exact: true })
      .selectOption('local');
    await expect(
      page.getByRole('alert').filter({ hasText: 'no reported OpenVINO GPU' }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start training', exact: true })).toBeDisabled();
    await page
      .getByRole('combobox', { name: 'Run training on', exact: true })
      .selectOption(`worker:${worker.id}`);
    const job = await queue(page, `Intel inference ${info.project.name}`);
    expect(job.config).toMatchObject({
      device: 'cpu',
      inference_backend: 'openvino',
      inference_device: 'GPU',
    });
    await request.post(`${api}/training/jobs/${job.id}/cancel`);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
  } finally {
    await request.delete(`${api}/training/workers/${worker.id}`);
  }
});

test('version mismatch stays online and requires an explicit administrator override', async ({
  page,
  request,
}, info) => {
  const created = await request.post(`${api}/training/workers`, {
    data: { name: `Version raven ${info.project.name}`, capacity: 1 },
  });
  expect(created.ok()).toBe(true);
  const { worker, token } = await created.json();
  try {
    const reported = await post(request, '/heartbeat', token, {
      capabilities: { protocol_version: 1, package_version: '0.25.0' },
    });
    const currentVersion = reported.worker.compatibility.server_version;
    await openWorkshop(page);
    await page.getByRole('button', { name: /Remote workers/ }).click();
    const card = page.locator(`[data-worker-id="${worker.id}"]`);
    await expect(card.locator('.worker-status')).toHaveText('online');
    await expect(card.locator('.worker-compatibility')).toHaveText('Application version mismatch');
    await expect(card).toContainText(`Worker version: 0.25.0 · Server version: ${currentVersion}`);
    await openCreation(page);
    await expect(page.getByRole('option', { name: new RegExp(worker.name) })).toHaveJSProperty(
      'disabled',
      true,
    );
    await card.getByRole('button', { name: /Enable version override for/ }).click();
    const warning = page.getByRole('dialog', { name: 'Enable version override', exact: true });
    await expect(warning).toContainText('invalid benchmark comparisons');
    await warning.getByRole('button', { name: 'Apply', exact: true }).click();
    await expect(card.locator('.worker-compatibility')).toHaveText('Administrator override active');
    await expect(card).toContainText('Unsupported version combination');
    await expect(page.getByRole('option', { name: new RegExp(worker.name) })).toHaveJSProperty(
      'disabled',
      false,
    );
    await page
      .getByRole('combobox', { name: 'Run training on', exact: true })
      .selectOption(`worker:${worker.id}`);
    await card.getByRole('button', { name: /Disable version override for/ }).click();
    await page
      .getByRole('dialog', { name: 'Disable version override', exact: true })
      .getByRole('button', { name: 'Apply', exact: true })
      .click();
    await expect(card.locator('.worker-compatibility')).toHaveText('Application version mismatch');
    await expect(page.getByRole('button', { name: 'Queue training', exact: true })).toBeDisabled();
    await post(request, '/heartbeat', token, {
      capabilities: { protocol_version: 1, package_version: currentVersion },
    });
    await expect(card.locator('.worker-compatibility')).toHaveText('Compatible');
    await expect(page.getByRole('button', { name: 'Queue training', exact: true })).toBeEnabled();
    await page.getByRole('combobox', { name: 'Language', exact: true }).selectOption('de');
    await expect(card).toContainText('Kompatibel');
    await expect(card).toContainText('Versionsausnahme deaktiviert');
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
  } finally {
    await request.delete(`${api}/training/workers/${worker.id}`);
  }
});
