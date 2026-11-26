import { isIdempotencyKey } from '../api/idempotency';
import type {
  OfferSnapshot,
  PreviewSnapshot,
  SnapshotIn,
  StreamSnapshot,
} from '../api/streams';

const prefix = 'kt-wrapper:draft:v1:';
const campaignPrefix = (campaignId: number) => `${prefix}${campaignId}:`;
const key = (campaignId: number, streamId: number) =>
  `${campaignPrefix(campaignId)}${streamId}`;

export type StoredDraft = {
  snapshot: PreviewSnapshot;
  base: string | null | undefined;
  pushKey?: string;
};

export function draftBase(stream: StreamSnapshot): string {
  const offers = [...stream.offers]
    .sort((a, b) => a.offer_id - b.offer_id)
    .map(({ offer_id, status, position, weight, pinned }) => [
      offer_id,
      status,
      position,
      weight,
      pinned,
    ]);
  return JSON.stringify([stream.status, stream.has_offers, offers]);
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function readDraft(value: string | null): StoredDraft | null {
  try {
    if (!value) return null;
    const draft: unknown = JSON.parse(value);
    if (
      typeof draft !== 'object' ||
      draft === null ||
      !('offers' in draft) ||
      !Array.isArray(draft.offers) ||
      !('unpin_offer_ids' in draft) ||
      !Array.isArray(draft.unpin_offer_ids)
    )
      return null;
    const data = draft as PreviewSnapshot;
    if (
      !data.offers.every(
        (item) =>
          item &&
          Number.isSafeInteger(item.offer_id) &&
          item.offer_id > 0 &&
          typeof item.offer_name === 'string' &&
          Number.isSafeInteger(item.position) &&
          ['active', 'added', 'removed'].includes(item.status) &&
          Number.isInteger(item.weight) &&
          item.weight >= 0 &&
          item.weight <= 100 &&
          typeof item.pinned === 'boolean' &&
          typeof item.can_pin === 'boolean' &&
          typeof item.can_remove === 'boolean',
      ) ||
      !data.unpin_offer_ids.every((id) => Number.isSafeInteger(id) && id > 0) ||
      new Set(data.offers.map((item) => item.offer_id)).size !==
        data.offers.length
    )
      return null;
    const base = 'base' in draft ? draft.base : undefined;
    return {
      snapshot: { offers: data.offers, unpin_offer_ids: data.unpin_offer_ids },
      base: typeof base === 'string' || base === undefined ? base : null,
      pushKey:
        'pushKey' in draft && isIdempotencyKey(draft.pushKey)
          ? draft.pushKey
          : undefined,
    };
  } catch {
    return null;
  }
}

export function readCampaignDrafts(
  campaignId: number,
): Record<number, StoredDraft> {
  const drafts: Record<number, StoredDraft> = {};
  const match = campaignPrefix(campaignId);
  try {
    const store = storage();
    if (!store) return drafts;
    for (let i = 0; i < store.length; i++) {
      const storedKey = store.key(i);
      if (!storedKey?.startsWith(match)) continue;
      const streamId = Number(storedKey.slice(match.length));
      if (
        !Number.isSafeInteger(streamId) ||
        streamId < 1 ||
        key(campaignId, streamId) !== storedKey
      )
        continue;
      const draft = readDraft(store.getItem(storedKey));
      if (draft) drafts[streamId] = draft;
    }
  } catch {
    /* unavailable */
  }
  return drafts;
}

export function writeDraft(
  campaignId: number,
  streamId: number,
  draft: StoredDraft,
): boolean {
  try {
    const store = storage();
    if (!store) return false;
    store.setItem(
      key(campaignId, streamId),
      JSON.stringify({
        ...draft.snapshot,
        base: draft.base,
        pushKey: draft.pushKey,
      }),
    );
    return true;
  } catch {
    return false;
  }
}
export function clearDraft(campaignId: number, streamId: number): void {
  try {
    storage()?.removeItem(key(campaignId, streamId));
  } catch {
    /* unavailable */
  }
}
export function hasCampaignDrafts(campaignId: number): boolean {
  try {
    const store = storage();
    if (!store) return false;
    const match = campaignPrefix(campaignId);
    for (let i = 0; i < store.length; i++)
      if (store.key(i)?.startsWith(match)) return true;
  } catch {
    /* unavailable */
  }
  return false;
}
export function clearCampaignDrafts(campaignId: number): void {
  try {
    const store = storage();
    if (!store) return;
    const keys = Array.from({ length: store.length }, (_, i) =>
      store.key(i),
    ).filter((item) => item?.startsWith(campaignPrefix(campaignId)));
    keys.forEach((item) => store.removeItem(item!));
  } catch {
    /* unavailable */
  }
}
export function toInput(
  offers: OfferSnapshot[],
  unpinOfferIds: number[],
): SnapshotIn {
  return {
    offers: offers.map(({ offer_id, status }) => ({ offer_id, status })),
    unpin_offer_ids: [...new Set(unpinOfferIds)],
  };
}
export function transition(
  stream: StreamSnapshot,
  draft: PreviewSnapshot | null,
  offerId: number,
  action: 'add' | 'remove' | 'restore',
): SnapshotIn {
  const offers = (draft?.offers ?? stream.offers).map(
    ({ offer_id, status }) => ({ offer_id, status }),
  );
  const current = offers.find((item) => item.offer_id === offerId);
  const published = stream.offers.find((item) => item.offer_id === offerId);
  if (action === 'add') {
    if (!current) offers.push({ offer_id: offerId, status: 'added' });
  } else if (action === 'remove') {
    if (current?.status === 'added' && !published)
      offers.splice(offers.indexOf(current), 1);
    else if (current) current.status = 'removed';
  } else if (current) current.status = 'active';
  const unpin = new Set(draft?.unpin_offer_ids ?? []);
  if (action === 'restore' && published?.pinned) unpin.add(offerId);
  return { offers, unpin_offer_ids: [...unpin] };
}
