import { apiRequest } from './client';

export type OfferSnapshot = {
  offer_id: number;
  offer_name: string;
  position: number;
  status: 'active' | 'added' | 'removed';
  weight: number;
  pinned: boolean;
  can_pin: boolean;
  can_remove: boolean;
};
export type StreamSnapshot = {
  id: number;
  keitaro_id: number;
  name: string;
  position: number;
  has_offers: boolean;
  status: 'published' | 'removed';
  offers: OfferSnapshot[];
};
export type SnapshotIn = {
  offers: { offer_id: number; status: OfferSnapshot['status'] }[];
  unpin_offer_ids: number[];
};
export type PreviewSnapshot = {
  offers: OfferSnapshot[];
  unpin_offer_ids: number[];
};

export const getStreams = (campaignId: number): Promise<StreamSnapshot[]> =>
  apiRequest(`/campaigns/${campaignId}/streams`);
export const fetchStreams = (campaignId: number): Promise<StreamSnapshot[]> =>
  apiRequest(`/campaigns/${campaignId}/fetch`, { method: 'POST' });
export const previewStream = (
  streamId: number,
  snapshot: SnapshotIn,
): Promise<PreviewSnapshot> =>
  apiRequest(`/streams/${streamId}/preview`, {
    method: 'POST',
    body: JSON.stringify(snapshot),
  });
export async function pushStream(
  streamId: number,
  snapshot: SnapshotIn,
  idempotencyKey: string,
  replayOnly: boolean,
): Promise<{ stream: StreamSnapshot; replayed: boolean }> {
  let replayed = false;
  const headers = new Headers({ 'Idempotency-Key': idempotencyKey });
  if (replayOnly) headers.set('Idempotency-Replay-Only', 'true');
  const stream = await apiRequest<StreamSnapshot>(
    `/streams/${streamId}/push`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify(snapshot),
    },
    (response) => {
      replayed = response.get('Idempotency-Replayed') === 'true';
    },
  );
  return { stream, replayed };
}
export const setOfferPin = (
  streamId: number,
  offerId: number,
  pinned: boolean,
): Promise<StreamSnapshot> =>
  apiRequest(`/streams/${streamId}/offers/${offerId}/pin`, {
    method: 'PUT',
    headers: { 'X-Stream-Dirty': 'false' },
    body: JSON.stringify({ pinned }),
  });
