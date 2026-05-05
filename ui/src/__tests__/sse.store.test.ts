import { beforeEach, describe, expect, it } from "vitest";

import { useSSEStore } from "../store/sse";

function reset(): void {
  useSSEStore.setState({ status: "unknown", lastEventAt: null, subscriberCount: 0 });
}

describe("useSSEStore", () => {
  beforeEach(reset);

  it("initial state — status=unknown, subscriberCount=0, lastEventAt=null", () => {
    const s = useSSEStore.getState();
    expect(s.status).toBe("unknown");
    expect(s.subscriberCount).toBe(0);
    expect(s.lastEventAt).toBeNull();
  });

  it("setStatus transitions across all 4 states", () => {
    const { setStatus } = useSSEStore.getState();
    setStatus("connecting");
    expect(useSSEStore.getState().status).toBe("connecting");
    setStatus("connected");
    expect(useSSEStore.getState().status).toBe("connected");
    setStatus("disconnected");
    expect(useSSEStore.getState().status).toBe("disconnected");
    setStatus("unknown");
    expect(useSSEStore.getState().status).toBe("unknown");
  });

  it("incrementSubscribers + decrementSubscribers refcount", () => {
    const { incrementSubscribers, decrementSubscribers } = useSSEStore.getState();
    incrementSubscribers();
    incrementSubscribers();
    expect(useSSEStore.getState().subscriberCount).toBe(2);
    decrementSubscribers();
    expect(useSSEStore.getState().subscriberCount).toBe(1);
    decrementSubscribers();
    decrementSubscribers();
    expect(useSSEStore.getState().subscriberCount).toBe(0);
  });

  it("recordEvent updates lastEventAt to a positive monotonic ms", () => {
    const before = Date.now();
    useSSEStore.getState().recordEvent();
    const after = Date.now();
    const lastEventAt = useSSEStore.getState().lastEventAt;
    expect(lastEventAt).not.toBeNull();
    expect(lastEventAt!).toBeGreaterThanOrEqual(before);
    expect(lastEventAt!).toBeLessThanOrEqual(after);
  });
});
