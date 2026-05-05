// T-417 — Freshness indicator dot for Feature inspector. 12px circle:
// green (fresh) if Date.now() - computedAt <= thresholdMs, red (stale)
// otherwise. Tooltip shows minutes-ago for operator inspection.
//
// Per WG#1 — STALENESS_MS = 5 * 60_000 — pragmatic UX threshold for
// fresh/stale dot; NOT business-logic gating (BRIEF §10.3:1727 backend
// uses per-feature 2 × interval_sec). Per-feature threshold deferred
// to F5+ when feature config metadata is exposed via API; per L-001
// named constant; per §N9 candidate for VITE_ env exposure if operator
// wants it tweakable.

import * as React from "react";

import { cn } from "@/lib/utils";

export const STALENESS_MS = 5 * 60_000;

interface StalenessDotProps {
  computedAt: string;
  thresholdMs?: number;
}

export function StalenessDot({
  computedAt,
  thresholdMs = STALENESS_MS,
}: StalenessDotProps): React.JSX.Element {
  const ageMs = Date.now() - new Date(computedAt).getTime();
  const stale = ageMs > thresholdMs;
  const ageMin = Math.max(0, Math.floor(ageMs / 60_000));
  return (
    <span
      data-testid="staleness-dot"
      data-status={stale ? "stale" : "fresh"}
      title={`computed ${String(ageMin)} min ago`}
      className={cn(
        "inline-block h-3 w-3 rounded-full",
        stale ? "bg-red-500" : "bg-green-500",
      )}
    />
  );
}
