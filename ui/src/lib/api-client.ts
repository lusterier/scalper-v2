// T-410 fetch wrapper skeleton. T-411..T-420 will consume via TanStack Query.
//
// Per OQ-6=A — native fetch (no axios). Per WG#12 — error message includes
// the response body so T-412+ debugging is not black-box.
//
// Per L-010 (T-420 brief-reviewer FIX FIRST 2026-05-05): 204 No Content +
// empty-body responses (DELETE / PUT-no-body) MUST short-circuit BEFORE
// res.json() because empty bodies throw SyntaxError on JSON parse.
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
  // Per L-010 — 204 No Content + Content-Length:0 short-circuit. T-420
  // DELETE /api/symbol-map/{id} returns 204 + empty body; res.json() on
  // empty payload throws SyntaxError silently breaking the mutation.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}
