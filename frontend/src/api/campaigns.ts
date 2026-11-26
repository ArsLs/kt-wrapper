import { apiRequest } from './client';

export type CreateCampaignInput = {
  name: string;
  geo: string;
  offer_id: number;
};
export type CreatedCampaign = {
  id: number;
  keitaro_id: number;
  name: string;
  alias: string;
  geo: string;
};

export const createCampaign = (
  input: CreateCampaignInput,
  idempotencyKey: string,
): Promise<CreatedCampaign> =>
  apiRequest<CreatedCampaign>('/campaigns', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(input),
  });

export type CampaignSummary = CreatedCampaign & {
  status: 'active' | 'removed';
};
export const getCampaigns = (): Promise<CampaignSummary[]> =>
  apiRequest('/campaigns');
export const deleteCampaign = (id: number): Promise<void> =>
  apiRequest<void>(`/campaigns/${id}`, { method: 'DELETE' });
