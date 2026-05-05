import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSSEStream } from "../lib/hooks/useSSEStream";
import { useSSEStore } from "../store/sse";

interface MockEventSource {
  url: string;
  close: ReturnType<typeof vi.fn>;
  onopen: (() => void) | null;
  onmessage: ((msg: { data: string }) => void) | null;
  onerror: (() => void) | null;
}

let lastInstance: MockEventSource | null = null;
let constructCount = 0;

class FakeEventSource implements MockEventSource {
  url: string;
  close = vi.fn(() => undefined);
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const self: MockEventSource = this;
    lastInstance = self;
    constructCount += 1;
  }
}

beforeEach(() => {
  lastInstance = null;
  constructCount = 0;
  useSSEStore.setState({ status: "unknown", lastEventAt: null, subscriberCount: 0 });
  // @ts-expect-error — replace global EventSource for the test scope
  globalThis.EventSource = FakeEventSource;
});

afterEach(() => {
  // @ts-expect-error — restore
  delete globalThis.EventSource;
});

describe("useSSEStream", () => {
  it("mount with first subscriber opens EventSource", () => {
    const handler = vi.fn();
    renderHook(() => useSSEStream(["positions"], handler));
    expect(constructCount).toBe(1);
    expect(lastInstance?.url).toBe("/events/stream?types=positions");
  });

  it("unmount with last subscriber closes EventSource", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useSSEStream(["positions"], handler));
    unmount();
    expect(lastInstance?.close).toHaveBeenCalledTimes(1);
  });

  it("2 parallel mounts share 1 EventSource (refcount keeps it open)", () => {
    const h1 = vi.fn();
    const h2 = vi.fn();
    const r1 = renderHook(() => useSSEStream(["positions"], h1));
    const r2 = renderHook(() => useSSEStream(["positions"], h2));
    expect(constructCount).toBe(1);
    expect(useSSEStore.getState().subscriberCount).toBe(2);
    r1.unmount();
    expect(lastInstance?.close).not.toHaveBeenCalled();
    r2.unmount();
    expect(lastInstance?.close).toHaveBeenCalledTimes(1);
  });

  it("handler invoked when EventSource onmessage fires", () => {
    const handler = vi.fn();
    renderHook(() => useSSEStream(["signals"], handler));
    act(() => {
      lastInstance?.onmessage?.({
        data: JSON.stringify({
          type: "signals",
          payload: { id: 1 },
          correlation_id: "abc",
          published_at: "2026-05-05T00:00:00+00:00",
        }),
      });
    });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0]?.[0]).toMatchObject({
      type: "signals",
      payload: { id: 1 },
    });
  });
});
