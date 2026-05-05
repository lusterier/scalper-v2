// T-411 — colored status badge for bot/signal/trade lifecycle states.
// Per BRIEF §14.4:2077 + OQ-4=A (inline cva variants per kind, no
// centralized STATUS_COLORS map — premature abstraction at single consumer).

import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const statusBadgeVariants = cva(
  "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      kind: {
        bot: "",
        signal: "",
        trade: "",
        backtest: "",
      },
      tone: {
        green: "bg-green-500/15 text-green-400 ring-1 ring-green-500/30",
        yellow: "bg-yellow-500/15 text-yellow-400 ring-1 ring-yellow-500/30",
        red: "bg-red-500/15 text-red-400 ring-1 ring-red-500/30",
        blue: "bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/30",
        gray: "bg-muted text-muted-foreground ring-1 ring-border",
      },
    },
    defaultVariants: { kind: "bot", tone: "gray" },
  },
);

export type BadgeKind = "bot" | "signal" | "trade" | "backtest";

interface StatusBadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof statusBadgeVariants> {
  kind: BadgeKind;
  status: string;
}

// (kind, status) → tone mapping. Unknown combos render in muted gray
// (no throw per Edge case #10).
function toneFor(kind: BadgeKind, status: string): VariantProps<typeof statusBadgeVariants>["tone"] {
  if (kind === "bot") {
    if (status === "active") return "green";
    if (status === "paused") return "yellow";
    if (status === "archived") return "gray";
  }
  if (kind === "signal") {
    if (status === "validated") return "green";
    if (status === "duplicate") return "yellow";
    if (status === "invalid") return "red";
  }
  if (kind === "trade") {
    if (status === "open") return "blue";
    if (status === "closed") return "gray";
    if (status === "error") return "red";
  }
  if (kind === "backtest") {
    if (status === "queued") return "yellow";
    if (status === "running") return "blue";
    if (status === "completed") return "green";
    if (status === "failed") return "red";
  }
  return "gray";
}

export function StatusBadge({
  kind,
  status,
  className,
  ...rest
}: StatusBadgeProps): React.JSX.Element {
  const tone = toneFor(kind, status);
  return (
    <span
      className={cn(statusBadgeVariants({ kind, tone }), className)}
      data-kind={kind}
      data-status={status}
      {...rest}
    >
      {status}
    </span>
  );
}
