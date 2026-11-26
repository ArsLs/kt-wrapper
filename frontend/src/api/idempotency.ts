import { ApiError } from './client';

export const isIdempotencyKey = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );

export function newIdempotencyKey(): string {
  // getRandomValues also works when the wrapper is served over plain HTTP.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-');
}

export function canRetryWithSameKey(error: unknown): boolean {
  return (
    !(error instanceof ApiError) ||
    error.code === 'campaign_create_uncertain' ||
    error.status >= 500 ||
    error.status === 408 ||
    error.status === 425 ||
    error.status === 429
  );
}
