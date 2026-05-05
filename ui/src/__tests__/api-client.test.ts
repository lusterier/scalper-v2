// T-420 — api-client tests added per L-010 (brief-reviewer FIX FIRST
// 2026-05-05): apiFetch must short-circuit 204 No Content empty bodies
// because res.json() on empty payload throws SyntaxError. Tests exercise
// the REAL fetch path (mocking global fetch, NOT mocking apiFetch
// itself) so a future regression is caught.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/api-client";

const realFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn() as typeof globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

function mockResponse(init: ResponseInit, body: BodyInit | null = null): Response {
  return new Response(body, init);
}

describe("apiFetch (T-410 + T-420 L-010 fix)", () => {
  it("returns parsed JSON for 200 OK with JSON body", async () => {
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(
      mockResponse({ status: 200, headers: { "content-type": "application/json" } }, '{"ok":true}'),
    );
    const result = await apiFetch<{ ok: boolean }>("/api/test");
    expect(result).toEqual({ ok: true });
  });

  it("204 No Content returns undefined without calling res.json() (per L-010)", async () => {
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    // Empty 204 body — would throw SyntaxError if res.json() called.
    mockFetch.mockResolvedValueOnce(mockResponse({ status: 204 }));
    const result = await apiFetch<void>("/api/symbol-map/BTCUSDT.P", { method: "DELETE" });
    expect(result).toBeUndefined();
  });

  it("Content-Length: 0 returns undefined without parse error (per L-010)", async () => {
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(
      mockResponse({ status: 200, headers: { "content-length": "0" } }),
    );
    const result = await apiFetch<void>("/api/empty");
    expect(result).toBeUndefined();
  });

  it("throws on !res.ok with status code + body in error message", async () => {
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(mockResponse({ status: 404 }, "not found"));
    await expect(apiFetch<unknown>("/api/missing")).rejects.toThrow(
      /404 not found/,
    );
  });
});
