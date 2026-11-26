import { apiRequest } from './client';

export type Geo = { code: string; name: string };

export const getGeo = (): Promise<Geo[]> => apiRequest<Geo[]>('/geo');
