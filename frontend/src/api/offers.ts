import { apiRequest } from './client';

export type Offer = { id: number; name: string; status: string | null };

export const searchOffers = (
  query: string,
  streamId?: number,
): Promise<Offer[]> => {
  const params = new URLSearchParams({ q: query });
  if (streamId !== undefined) params.set('stream_id', String(streamId));
  return apiRequest<Offer[]>(`/offers/search?${params}`);
};
