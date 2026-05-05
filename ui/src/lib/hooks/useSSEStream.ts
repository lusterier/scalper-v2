// T-413 — useSSEStream: shared EventSource lifecycle hook.
//
// Per WG#6: handler list lives in a module-scoped `Set<SSEEventHandler>`
// OUTSIDE Zustand state, so per-event dispatch does NOT trigger
// component re-renders. Browser is single-threaded JS event loop — no
// concurrency concern. Set is cleared on ref-count 0 (when the last
// subscriber unmounts) so a stale handler from a prior connection
// cannot fire on a new event source.

import * as React from "react";

import { buildSSEUrl, type SSEEvent } from "@/lib/sse-client";
import { useSSEStore } from "@/store/sse";

// T-413 consumers ("positions" / "signals" / "trades"); backend
// supports 2 additional types ("scoring" / "alerts" per models/events.py
// EventType enum) that no T-413 consumer subscribes to. Keeping this
// union narrow (per §0.8 anti-hypothetical) — T-417 / T-419 may extend
// when their consumers land.
export type SSEEventType = "positions" | "signals" | "trades";

export type SSEEventHandler = (event: SSEEvent) => void;

const handlers = new Set<SSEEventHandler>();
let activeSource: EventSource | null = null;

function dispatch(event: SSEEvent): void {
  for (const handler of handlers) {
    try {
      handler(event);
    } catch (err) {
      console.error("SSE handler raised", err);
    }
  }
}

function openConnection(types: ReadonlyArray<SSEEventType>): EventSource {
  const url = buildSSEUrl(types);
  const source = new EventSource(url);
  const store = useSSEStore.getState();
  store.setStatus("connecting");
  source.onopen = () => {
    useSSEStore.getState().setStatus("connected");
  };
  source.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data) as SSEEvent;
      useSSEStore.getState().recordEvent();
      dispatch(event);
    } catch (err) {
      console.error("SSE parse failed", err);
    }
  };
  source.onerror = () => {
    useSSEStore.getState().setStatus("disconnected");
  };
  return source;
}

function closeConnection(): void {
  if (activeSource !== null) {
    activeSource.close();
    activeSource = null;
  }
  handlers.clear();
  useSSEStore.getState().setStatus("disconnected");
}

export function useSSEStream(
  types: ReadonlyArray<SSEEventType>,
  onEvent: SSEEventHandler,
): { status: ReturnType<typeof useSSEStore.getState>["status"] } {
  const status = useSSEStore((s) => s.status);
  const incrementSubscribers = useSSEStore((s) => s.incrementSubscribers);
  const decrementSubscribers = useSSEStore((s) => s.decrementSubscribers);

  // Stable string key derived from types array contents — re-renders
  // pass new array references but identical content, so the effect
  // dep should compare by value not reference. Strings are compared by
  // value in React's dep array (unlike arrays).
  const typesKey = [...types].sort().join(",");

  React.useEffect(() => {
    handlers.add(onEvent);
    incrementSubscribers();
    if (activeSource === null) {
      activeSource = openConnection(typesKey.split(",") as SSEEventType[]);
    }
    return () => {
      handlers.delete(onEvent);
      decrementSubscribers();
      if (useSSEStore.getState().subscriberCount === 0) {
        closeConnection();
      }
    };
  }, [onEvent, typesKey, incrementSubscribers, decrementSubscribers]);

  return { status };
}
