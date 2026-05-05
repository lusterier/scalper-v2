import { renderHook, act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebouncedValidation } from "../lib/hooks/useDebouncedValidation";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDebouncedValidation", () => {
  it("debounces 500ms before firing POST (per OQ-3=A)", async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValue({
      valid: true,
      bot_id: "alpha",
      parsed_version: 3,
      errors: [],
    });
    const { rerender } = renderHook(
      ({ text }: { text: string }) => useDebouncedValidation(text, "alpha", 500),
      { initialProps: { text: "abc" } },
    );
    // Before 500ms: no fetch.
    expect(mockFetch).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    // After 500ms: 1 fetch.
    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    rerender({ text: "abc" });
  });

  it("empty yamlText returns synchronous invalid result without POST (per WG#4)", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDebouncedValidation("", "alpha", 500));
    expect(result.current.valid).toBe(false);
    expect(result.current.errors).toEqual(["yaml_text empty"]);
    expect(result.current.isPending).toBe(false);
    // Advance way past debounce — still no fetch.
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("AbortController cancels prior fetch when yamlText changes mid-fetch (per WG#3)", async () => {
    vi.useFakeTimers();
    let signalAborted = false;
    mockFetch.mockImplementation(
      (_path: string, options: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            signalAborted = true;
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    const { rerender, unmount } = renderHook(
      ({ text }: { text: string }) => useDebouncedValidation(text, "alpha", 500),
      { initialProps: { text: "first" } },
    );
    act(() => {
      vi.advanceTimersByTime(501);
    });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(signalAborted).toBe(false);
    rerender({ text: "second" });
    expect(signalAborted).toBe(true);
    unmount();
  });

  it("result state machine — non-empty text → isPending=true → backend response sets valid", async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValue({
      valid: true,
      bot_id: "alpha",
      parsed_version: 7,
      errors: [],
    });
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useDebouncedValidation(text, "alpha", 500),
      { initialProps: { text: "valid yaml" } },
    );
    // After empty initial — typed text triggers isPending.
    expect(result.current.isPending).toBe(true);
    await act(async () => {
      vi.advanceTimersByTime(501);
      vi.useRealTimers();
      // Wait for the resolved promise to flush.
      await new Promise((r) => setTimeout(r, 10));
    });
    await waitFor(() => {
      expect(result.current.valid).toBe(true);
      expect(result.current.parsedVersion).toBe(7);
      expect(result.current.isPending).toBe(false);
    });
    rerender({ text: "valid yaml" });
  });
});
