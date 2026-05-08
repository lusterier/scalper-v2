// T-412 — shared tile primitive for Section 1 Overview cross-bot dashboard.
// Per WG#2 (plan-reviewer Gate 1): the literal "Loading…" lives in this
// file ONCE. OverviewPage MUST NOT hardcode the same literal in its 5 tile
// invocations — single source of truth prevents drift across tiles.
//
// Placeholder mode (placeholder=true) renders "—" + subtitle "Coming F4+"
// for tiles whose backend data path doesn't yet exist (virtual_balance,
// alert_count). Per OQ-A1 / OQ-B1 — explicit operator-facing message vs.
// silent zero.

import * as React from "react";

import { cn } from "@/lib/utils";

interface OverviewTileProps {
  title: string;
  value?: string | React.ReactNode;
  subtitle?: string;
  loading?: boolean;
  error?: string;
  placeholder?: boolean;
}

export function OverviewTile({
  title,
  value,
  subtitle,
  loading,
  error,
  placeholder,
}: OverviewTileProps): React.JSX.Element {
  const body: React.ReactNode = (() => {
    if (loading) {
      return <span className="text-muted-foreground text-sm">Loading…</span>;
    }
    if (error !== undefined) {
      return <span className="text-red-400 text-sm">{error}</span>;
    }
    if (placeholder) {
      return <span className="text-muted-foreground/30 font-trading text-2xl">—</span>;
    }
    return value;
  })();

  const effectiveSubtitle = placeholder ? (subtitle ?? "Coming F4+") : subtitle;

  return (
    <div
      data-testid={`overview-tile-${title.toLowerCase().replace(/\s+/g, "-")}`}
      className="relative overflow-hidden rounded-xl border border-border bg-card px-4 py-3"
    >
      {/* Top accent line */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-border to-transparent" />

      {/* Title */}
      <div className="mb-2.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
        {title}
      </div>

      {/* Value */}
      <div
        className={cn(
          "font-trading text-2xl font-bold leading-none",
          placeholder ? "text-muted-foreground/30" : "text-foreground",
        )}
      >
        {body}
      </div>

      {/* Subtitle */}
      {effectiveSubtitle !== undefined && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {placeholder && (
            <span className="rounded border border-border bg-secondary px-1.5 py-0.5 text-[9px] font-trading tracking-wider text-muted-foreground">
              F4+
            </span>
          )}
          {placeholder ? "Coming soon" : effectiveSubtitle}
        </div>
      )}
    </div>
  );
}
