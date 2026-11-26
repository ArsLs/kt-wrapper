/** Thin fetch wrapper for the wrapper's own backend (`/api/*`). */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

type ErrorBody = { detail?: unknown; code?: unknown };

function messageFrom(body: ErrorBody, fallback: string): string {
  return typeof body.detail === 'string' ? body.detail : fallback;
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  onResponse?: (headers: Headers) => void,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorBody;
    const code = typeof body.code === 'string' ? body.code : null;
    throw new ApiError(response.status, messageFrom(body, response.statusText), code);
  }
  onResponse?.(response.headers);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
