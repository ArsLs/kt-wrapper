import type { CreateCampaignInput } from '../api/campaigns';
import { isIdempotencyKey } from '../api/idempotency';

const storageKey = 'kt-wrapper:create-attempt:v1';

export type CreateAttempt = CreateCampaignInput & {
  key: string;
  offerLabel: string;
};

export function readCreateAttempt(): CreateAttempt | null {
  try {
    const attempt = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
    if (
      attempt &&
      isIdempotencyKey(attempt.key) &&
      typeof attempt.name === 'string' &&
      attempt.name.trim() &&
      typeof attempt.geo === 'string' &&
      attempt.geo &&
      Number.isSafeInteger(attempt.offer_id) &&
      attempt.offer_id > 0 &&
      typeof attempt.offerLabel === 'string'
    ) {
      return attempt;
    }
  } catch {
    /* unavailable */
  }
  return null;
}

export function saveCreateAttempt(attempt: CreateAttempt): void {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(attempt));
  } catch {
    /* The current form can still retry with the same key. */
  }
}

export function clearCreateAttempt(key: string): void {
  try {
    if (readCreateAttempt()?.key === key) sessionStorage.removeItem(storageKey);
  } catch {
    /* unavailable */
  }
}
