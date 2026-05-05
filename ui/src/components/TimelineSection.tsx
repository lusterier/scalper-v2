// T-414 — Card-section primitive for trade drill-down timeline. Mirrors
// OverviewTile (T-412) loading/error/placeholder semantics but uses
// full-card layout (not single-value tile). 8 instances per drill-down
// route (1 trade summary header + 7 BRIEF tiers).

import * as React from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface TimelineSectionProps {
  title: string;
  loading?: boolean;
  error?: string;
  placeholder?: { reason: string };
  children?: React.ReactNode;
}

export function TimelineSection({
  title,
  loading,
  error,
  placeholder,
  children,
}: TimelineSectionProps): React.JSX.Element {
  const body: React.ReactNode = (() => {
    if (loading) {
      return <span className="text-muted-foreground">Loading…</span>;
    }
    if (error !== undefined) {
      return <span className="text-red-400 text-sm">{error}</span>;
    }
    if (placeholder !== undefined) {
      return (
        <div data-testid="timeline-placeholder" className="text-sm text-muted-foreground">
          {placeholder.reason}
        </div>
      );
    }
    return children;
  })();

  return (
    <Card data-testid={`timeline-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
