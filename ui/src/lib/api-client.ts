// T-410 fetch wrapper skeleton. T-411..T-420 will consume via TanStack Query.
//
// Per OQ-6=A — native fetch (no axios). Per WG#12 — error message includes
// the response body so T-412+ debugging is not black-box.
//
// Vite dev server proxies /api/* to http://127.0.0.1:8000 (analytics-api).
// In production (F5+), nginx routes /api/* to the backend service.

type FetchOptions = Omit<RequestInit, "body" | "signal"> & {
  body?: unknown;
  signal?: AbortSignal;
};

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options;
  const res = await fetch(path, {
    ...rest,
    headers: { "content-type": "application/json", ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const responseText = await res.text();
    throw new Error(`API ${path} failed: ${res.status} ${responseText}`);
  }
  return res.json() as Promise<T>;
}
