// T-412 — top-bar connection-status indicator. T-412 hardcodes
// status="unknown" (gray) — T-413 wires real EventSource state via
// Zustand store + SSE subscription handler.

import * as React from "react";

import { cn } from "@/lib/utils";

// T-413 added "connecting" — EventSource lifecycle has a transient
// state between mount and onopen.
type ConnectionStatus = "connected" | "disconnected" | "unknown" | "connecting";

interface ConnectionDotProps {
  status: ConnectionStatus;
}

const TONE_CLASS: Record<ConnectionStatus, string> = {
  connected: "bg-green-500",
  disconnected: "bg-red-500",
  unknown: "bg-muted-foreground",
  connecting: "bg-yellow-500",
};

const TOOLTIP: Record<ConnectionStatus, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  unknown: "SSE not active",
  connecting: "Connecting…",
};

export function ConnectionDot({ status }: ConnectionDotProps): React.JSX.Element {
  return (
    <span
      title={TOOLTIP[status]}
      data-testid="connection-dot"
      data-status={status}
      className={cn("inline-block h-3 w-3 rounded-full", TONE_CLASS[status])}
    />
  );
}
