import { expect, test, type Page } from '@playwright/test';
import type { PreviewSnapshot, SnapshotIn, StreamSnapshot } from '../src/api/streams';

const campaign = {
  id: 1, keitaro_id: 10, name: 'Campaign', alias: 'campaign', geo: 'US', status: 'active',
};
const labels = ['Alpha', 'Bravo', 'Charlie', 'Delta'];
const weightsError = 'Offer weights in Keitaro are invalid. Check them in the tracker and fetch the streams again.';
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const offer = (id: number, weight: number, status: PreviewSnapshot['offers'][number]['status'] = 'active', pinned = false) => ({
  offer_id: id, offer_name: labels[id - 1], position: id, status, weight, pinned, can_pin: true, can_remove: true,
});
const initial: StreamSnapshot = {
  id: 7, keitaro_id: 70, name: 'Main stream', position: 1, has_offers: true,
  status: 'published', offers: [offer(1, 100)],
};

async function mockApi(page: Page) {
  let published = initial;
  let remote = published;
  let badWeights = false;
  let nextFetchFails = false;
  let nextPush: 'drop' | 'temporary' | 'uncommitted' | 'success' = 'success';
  let failCreate = true;
  let loseNextCreate = false;
  let fetchCount = 0;
  let trackerPushCount = 0;
  const pushRequests: { key: string | undefined; replayOnly: boolean; data: SnapshotIn }[] = [];
  const createRequests: { key: string | undefined; data: unknown }[] = [];
  const previews: SnapshotIn[] = [];
  const savedPushes = new Map<string, StreamSnapshot>();

  function preview(input: SnapshotIn): PreviewSnapshot {
    const active = input.offers.filter(({ status }) => status !== 'removed');
    const fixed = active.filter(({ offer_id }) => published.offers.some(
      (item) => item.offer_id === offer_id && item.pinned && !input.unpin_offer_ids.includes(offer_id),
    ));
    const pool = active.filter((item) => !fixed.includes(item));
    const remaining = 100 - fixed.reduce((sum, item) => sum + published.offers.find(
      ({ offer_id }) => offer_id === item.offer_id,
    )!.weight, 0);
    const base = Math.floor(remaining / pool.length);
    return {
      offers: input.offers.map((item) => {
        const old = published.offers.find(({ offer_id }) => offer_id === item.offer_id);
        const pinned = fixed.includes(item);
        const extra = pool.indexOf(item) >= pool.length - (remaining % pool.length);
        const weight = item.status === 'removed' ? 0 : pinned ? old!.weight : base + Number(extra);
        return offer(item.offer_id, weight, item.status, pinned);
      }),
      unpin_offer_ids: input.unpin_offer_ids,
    };
  }

  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const send = (json: unknown, status = 200, headers?: Record<string, string>) =>
      route.fulfill({ status, json, headers });
    const headers = request.headers();
    switch (url.pathname) {
      case '/api/health': return send({ status: 'ok', database: 'ok' });
      case '/api/campaigns':
        if (request.method() === 'GET') return send([campaign]);
        createRequests.push({ key: headers['idempotency-key'], data: request.postDataJSON() });
        if (loseNextCreate) {
          loseNextCreate = false;
          return route.abort('failed');
        }
        if (failCreate) return send({ detail: 'Campaign creation could not be confirmed.', code: 'campaign_create_uncertain' }, 502);
        return send(campaign, 201);
      case '/api/geo': return send([{ code: 'US', name: 'United States' }]);
      case '/api/offers/search': {
        const q = url.searchParams.get('q')?.toLowerCase();
        return send(labels.map((name, index) => ({ id: index + 1, name, status: null })).filter(
          ({ id, name }) => String(id) === q || name.toLowerCase().includes(q ?? ''),
        ));
      }
      case '/api/campaigns/1/streams': return send([published]);
      case '/api/campaigns/1/fetch':
        fetchCount++;
        if (nextFetchFails) {
          nextFetchFails = false;
          return send({ detail: 'Fetch is temporarily unavailable.' }, 503);
        }
        if (badWeights) return send({ code: 'keitaro_invalid_offer_weights', detail: weightsError }, 502);
        published = remote;
        return send([published]);
      case '/api/streams/7/preview': {
        const input: SnapshotIn = request.postDataJSON();
        previews.push(input);
        return send(preview(input));
      }
      case '/api/streams/7/offers/1/pin':
        published = { ...published, offers: published.offers.map((item) => item.offer_id === 1
          ? { ...item, pinned: request.postDataJSON().pinned }
          : item) };
        return send(published);
      case '/api/streams/7/push': {
        const key = headers['idempotency-key'];
        const replayOnly = headers['idempotency-replay-only'] === 'true';
        const data: SnapshotIn = request.postDataJSON();
        pushRequests.push({ key, replayOnly, data });
        const replay = key && savedPushes.get(key);
        if (replay) return send(replay, 200, { 'Idempotency-Replayed': 'true' });
        if (replayOnly) return send({ code: 'idempotency_result_unavailable', detail: 'No saved push result.' }, 409);
        const behavior = nextPush;
        nextPush = 'success';
        if (behavior === 'temporary') return send({ detail: 'Keitaro is temporarily unavailable.' }, 503);
        trackerPushCount++;
        remote = {
          ...published,
          offers: preview(data).offers.map((item) =>
            item.status === 'added' ? { ...item, status: 'active' } : item),
        };
        if (behavior === 'uncommitted') return route.abort('failed');
        published = remote;
        if (key) savedPushes.set(key, published);
        if (behavior === 'drop') return route.abort('failed');
        return send(published);
      }
      default: throw new Error(`Unexpected API request: ${request.method()} ${url.pathname}`);
    }
  });

  return {
    createRequests, pushRequests, previews,
    get fetchCount() { return fetchCount; },
    get trackerPushCount() { return trackerPushCount; },
    allowCreate: () => { failCreate = false; },
    loseNextCreateResponse: () => { loseNextCreate = true; },
    failNextFetch: () => { nextFetchFails = true; },
    failNextPush: () => { nextPush = 'temporary'; },
    failNextPushCommit: () => { nextPush = 'uncommitted'; },
    loseNextPushResponse: () => { nextPush = 'drop'; },
    setRemote: (offers: StreamSnapshot['offers']) => { remote = { ...published, offers }; },
    setBadWeights: (value: boolean) => { badWeights = value; },
  };
}

const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true });
const row = (page: Page, label: string) => page.getByRole('row').filter({ hasText: label });

async function openEditor(page: Page) {
  await page.goto('/campaigns/1');
  await expect(button(page, 'Fetch streams')).toBeEnabled();
}

async function addOffer(page: Page, id: number) {
  await page.getByLabel('Add offer').fill(String(id));
  await page.getByRole('option', { name: `${labels[id - 1]} (#${id})` }).click();
  await button(page, 'Add').click();
  await expect(row(page, labels[id - 1])).toContainText('Added');
}

test('fetches, edits, pins, cancels, archives and restores offers', async ({ page }) => {
  const api = await mockApi(page);
  await openEditor(page);
  expect(api.fetchCount).toBe(1);
  await expect(row(page, 'Alpha')).toContainText('100%');

  for (const id of [2, 3, 4]) await addOffer(page, id);
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  for (const label of labels) await expect(row(page, label)).toContainText('25%');
  await expect(button(page, 'Pin Alpha')).toBeDisabled();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
  expect(api.pushRequests[0].key).toMatch(uuidV4);
  expect(api.pushRequests[0].data.offers).toEqual([
    { offer_id: 1, status: 'active' },
    ...[2, 3, 4].map((offer_id) => ({ offer_id, status: 'added' })),
  ]);

  await button(page, 'Pin Alpha').click();
  await expect(button(page, 'Pin Alpha')).toHaveAttribute('aria-pressed', 'true');
  await button(page, 'Remove Delta').click();
  await expect(row(page, 'Alpha')).toContainText('25%');
  await expect(row(page, 'Bravo')).toContainText('37%');
  await expect(row(page, 'Charlie')).toContainText('38%');
  await expect(row(page, 'Delta')).toContainText('Removed');
  await button(page, 'Cancel').click();
  await expect(row(page, 'Delta')).toContainText('25%');
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
  expect(api.pushRequests).toHaveLength(1);

  await button(page, 'Remove Delta').click();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
  await expect(row(page, 'Delta')).toContainText('Removed');
  expect(api.pushRequests[1].data.offers.at(-1)).toEqual({ offer_id: 4, status: 'removed' });

  await row(page, 'Delta').getByRole('button', { name: 'Bring back' }).click();
  await expect(row(page, 'Delta')).toContainText('25%');
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  expect(api.previews.at(-1)?.offers.at(-1)).toEqual({ offer_id: 4, status: 'active' });
  await button(page, 'Cancel').click();

  api.setRemote([offer(1, 25, 'active', true), offer(2, 37), offer(3, 0, 'removed'), offer(4, 0, 'removed')]);
  await button(page, 'Fetch streams').click();
  await expect(row(page, 'Charlie')).toContainText('Removed');
  await expect(row(page, 'Charlie').getByRole('button', { name: 'Bring back' })).toBeEnabled();
  expect(api.fetchCount).toBe(2);

  await row(page, 'Charlie').getByRole('button', { name: 'Bring back' }).click();
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  api.setBadWeights(true);
  page.once('dialog', (dialog) => dialog.accept());
  await button(page, 'Fetch streams').click();
  await expect(page.getByText(weightsError)).toBeVisible();
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  api.setBadWeights(false);
  page.once('dialog', (dialog) => dialog.accept());
  await button(page, 'Fetch streams').click();
  await expect(page.getByText(weightsError)).toHaveCount(0);
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
});

test('replays Push after reload and displays newer published data', async ({ page }) => {
  const api = await mockApi(page);
  await openEditor(page);
  await addOffer(page, 2);
  api.loseNextPushResponse();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Network request failed. Please try again.')).toBeVisible();
  api.setRemote([offer(1, 70), offer(2, 30)]);
  await page.reload();
  await button(page, 'Retry previous push').click();
  await expect(page.getByText('Draft for Main stream')).toHaveCount(0);
  await expect(row(page, 'Alpha')).toContainText('70%');
  expect(api.pushRequests[1]).toEqual({ ...api.pushRequests[0], replayOnly: true });
});

test('only replays a restored Push key when automatic fetch fails', async ({ page }) => {
  const api = await mockApi(page);
  await openEditor(page);
  await addOffer(page, 2);
  api.failNextPushCommit();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Network request failed. Please try again.')).toBeVisible();
  expect(api.trackerPushCount).toBe(1);

  api.failNextFetch();
  await page.reload();
  await expect(page.getByText('Fetch is temporarily unavailable.')).toBeVisible();
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  await button(page, 'Retry previous push').click();
  await expect(page.getByText('No saved push result.')).toBeVisible();
  expect(api.pushRequests[1]).toEqual({ ...api.pushRequests[0], replayOnly: true });
  expect(api.trackerPushCount).toBe(1);

  await button(page, 'Cancel').click();
  await button(page, 'Fetch streams').click();
  await expect(row(page, 'Bravo')).toContainText('Active');
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
});

test('reuses a transient Push key and clears it on edit and Cancel', async ({ page }) => {
  const api = await mockApi(page);
  await openEditor(page);
  await addOffer(page, 2);
  api.failNextPush();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Keitaro is temporarily unavailable.')).toBeVisible();
  await page.reload();
  api.failNextPush();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Keitaro is temporarily unavailable.')).toBeVisible();
  expect(api.pushRequests[1]).toEqual(api.pushRequests[0]);

  await addOffer(page, 3);
  api.failNextPush();
  await button(page, 'Push to Keitaro').click();
  await expect.poll(() => api.pushRequests.length).toBe(3);
  expect(api.pushRequests[2].key).not.toBe(api.pushRequests[0].key);
  await button(page, 'Cancel').click();
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);

  await addOffer(page, 2);
  await addOffer(page, 3);
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Unsaved changes')).toHaveCount(0);
  expect(api.pushRequests[3].data).toEqual(api.pushRequests[2].data);
  expect(api.pushRequests[3].key).not.toBe(api.pushRequests[2].key);
});

test('loads a legacy session draft and can retry it safely', async ({ page }) => {
  const api = await mockApi(page);
  await openEditor(page);
  await page.evaluate((snapshot) => {
    sessionStorage.setItem('kt-wrapper:draft:v1:1:7', JSON.stringify(snapshot));
  }, { offers: [offer(1, 50), offer(2, 50, 'added')], unpin_offer_ids: [] });
  await page.reload();
  await expect(page.getByText('Unsaved changes')).toBeVisible();
  await expect(row(page, 'Bravo')).toContainText('Added');
  api.failNextPush();
  await button(page, 'Push to Keitaro').click();
  await expect(page.getByText('Keitaro is temporarily unavailable.')).toBeVisible();

  api.setRemote([offer(1, 90)]);
  await page.reload();
  await button(page, 'Retry previous push').click();
  await expect(page.getByText('No saved push result.')).toBeVisible();
  expect(api.pushRequests[0].key).toMatch(uuidV4);
  expect(api.pushRequests[1]).toEqual({ ...api.pushRequests[0], replayOnly: true });
  await expect(row(page, 'Alpha')).toContainText('90%');
  await button(page, 'Cancel').click();
  await page.reload();
  await expect(button(page, 'Fetch streams')).toBeEnabled();
  await expect(page.getByText('Draft for Main stream')).toHaveCount(0);
});

async function fillCampaign(page: Page, name: string) {
  await page.getByLabel('Name').fill(name);
  await page.getByRole('combobox', { name: 'Geo' }).click();
  await page.getByRole('option', { name: 'United States (US)' }).click();
  await page.getByLabel('Offer').fill('Alpha');
  await page.getByRole('option', { name: 'Alpha (#1)' }).click();
}

const uncertainCreate = (page: Page) =>
  page.getByRole('region', { name: 'New campaign' }).getByText('Campaign creation could not be confirmed.');

test('restores an uncertain Create and starts a new request after editing', async ({ page }) => {
  const api = await mockApi(page);
  await page.goto('/');
  await fillCampaign(page, 'New campaign');
  await button(page, 'Create').click();
  await expect(uncertainCreate(page)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Name')).toHaveValue('New campaign');
  await button(page, 'Retry create').click();
  await expect(uncertainCreate(page)).toBeVisible();
  expect(api.createRequests[0].key).toMatch(uuidV4);
  expect(api.createRequests[1]).toEqual(api.createRequests[0]);

  await page.getByLabel('Name').fill('Edited campaign');
  await button(page, 'Create').click();
  await expect(uncertainCreate(page)).toBeVisible();
  expect(api.createRequests[2].data).toEqual({ name: 'Edited campaign', geo: 'US', offer_id: 1 });
  expect(api.createRequests[2].key).not.toBe(api.createRequests[0].key);

  await button(page, 'Reset').click();
  await expect(page.getByLabel('Name')).toBeEmpty();
  await fillCampaign(page, 'Edited campaign');
  api.allowCreate();
  await button(page, 'Create').click();
  await expect(page).toHaveURL(/\/campaigns\/1$/);
  expect(api.createRequests[3].data).toEqual(api.createRequests[2].data);
  expect(api.createRequests[3].key).not.toBe(api.createRequests[2].key);
  await page.goto('/');
  await expect(page.getByLabel('Name')).toBeEmpty();
});

test('keeps each tab’s pending Create after another tab succeeds', async ({ context, page }) => {
  const api = await mockApi(page);
  const secondTab = await context.newPage();
  const secondApi = await mockApi(secondTab);
  await Promise.all([page.goto('/'), secondTab.goto('/')]);
  await fillCampaign(page, 'First campaign');
  await fillCampaign(secondTab, 'Second campaign');

  api.loseNextCreateResponse();
  await button(page, 'Create').click();
  await expect(page.getByRole('region', { name: 'New campaign' }).getByText(
    'Network request failed. Please try again.',
  )).toBeVisible();

  secondApi.allowCreate();
  await button(secondTab, 'Create').click();
  await expect(secondTab).toHaveURL(/\/campaigns\/1$/);
  expect(secondApi.createRequests[0].key).toMatch(uuidV4);
  expect(secondApi.createRequests[0].key).not.toBe(api.createRequests[0].key);

  await page.reload();
  await expect(page.getByLabel('Name')).toHaveValue('First campaign');
  await button(page, 'Retry create').click();
  await expect(uncertainCreate(page)).toBeVisible();
  expect(api.createRequests[0].key).toMatch(uuidV4);
  expect(api.createRequests[1]).toEqual(api.createRequests[0]);
});
