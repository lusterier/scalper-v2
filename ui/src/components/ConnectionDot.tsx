// T-412 — top-bar connection-status indicator. T-412 hardcodes
// status="unknown" (gray) — T-413 wires real EventSource state via
// Zustand store + SSE subscription handler.

import * as React from "react";

import { cn } from "@/lib/utils";

type ConnectionStatus = "connected" | "disconnected" | "unknown";

interface ConnectionDotProps {
  status: ConnectionStatus;
}

const TONE_CLASS: Record<ConnectionStatus, string> = {
  connected: "bg-green-500",
  disconnected: "bg-red-500",
  unknown: "bg-muted-foreground",
};

const TOOLTIP: Record<ConnectionStatus, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  unknown: "SSE not active",
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
