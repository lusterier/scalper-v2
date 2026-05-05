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

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
      return <span className="text-muted-foreground">Loading…</span>;
    }
    if (error !== undefined) {
      return <span className="text-red-400 text-sm">{error}</span>;
    }
    if (placeholder) {
      return <span className="text-muted-foreground">—</span>;
    }
    return value;
  })();

  const effectiveSubtitle = placeholder ? (subtitle ?? "Coming F4+") : subtitle;

  return (
    <Card data-testid={`overview-tile-${title.toLowerCase().replace(/\s+/g, "-")}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={cn("text-2xl font-semibold", placeholder && "text-muted-foreground")}>
          {body}
        </div>
        {effectiveSubtitle !== undefined && (
          <div className="mt-1 text-xs text-muted-foreground">{effectiveSubtitle}</div>
        )}
      </CardContent>
    </Card>
  );
}
