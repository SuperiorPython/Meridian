const BASE = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const KEY_STORAGE = "meridian.key";

export const UNAUTHORIZED_EVENT = "meridian:unauthorized";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// The key lives in sessionStorage: it is gone when the tab closes.
export function getKey(): string | null {
  try {
    return sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setKey(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable: the key just won't survive a reload */
  }
}

function detailOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d as { msg?: string }).msg ?? "").filter(Boolean).join("; ") || fallback;
  }
  return fallback;
}

export async function api<T>(
  path: string,
  init: RequestInit & { key?: string | null } = {},
): Promise<T> {
  const { key = getKey(), ...rest } = init;
  const headers = new Headers(rest.headers);
  if (key) headers.set("Authorization", `Bearer ${key}`);
  if (rest.body) headers.set("Content-Type", "application/json");

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...rest, headers });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, `Can't reach the Meridian server at ${BASE}. Is it running?`);
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(res.status, detailOf(body, res.statusText || `Request failed (${res.status})`));
  }
  return (await res.json()) as T;
}
