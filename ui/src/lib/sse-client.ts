// T-410 EventSource wrapper skeleton. T-413 (Per-bot live view) will
// consume `/events/stream?types=positions,signals,trades`.
//
// Per OQ-7=A — native EventSource (no @microsoft/fetch-event-source dep).
//
// TODO (per WG#13 — T-413 owner): T-408 SSE endpoint emits typed events
// (server uses `data: {"type": "positions", ...}` JSON envelope per
// _envelope_to_sse_event in services/analytics_api/app/sse.py). Current
// skeleton only handles anonymous `onmessage` events. T-413 may extend
// with per-type handler-map via `source.addEventListener("positions",
// handler)` if the backend adds named SSE event lines (currently does
// not — all events arrive as anonymous `data:` lines).

export type SSEEvent = {
  type: string;
  payload: unknown;
  correlation_id: string | null;
  published_at: string;
};

export type SSEHandler = (event: SSEEvent) => void;

export function subscribeSSE(
  url: string,
  handler: SSEHandler,
  onError?: (e: Event) => void,
): () => void {
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    try {
      handler(JSON.parse(msg.data) as SSEEvent);
    } catch (e) {
      console.error("SSE parse failed", e);
    }
  };
  if (onError) {
    source.onerror = onError;
  }
  return () => {
    source.close();
  };
}
