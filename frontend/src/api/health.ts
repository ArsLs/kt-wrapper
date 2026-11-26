import { apiRequest } from './client';

export type Health = { status: 'ok' | 'error'; database: 'ok' | 'unavailable' };

export const getHealth = (): Promise<Health> => apiRequest<Health>('/health');
