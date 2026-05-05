// T-413 — last-50 signals feed (newest-first). Compact list, NOT
// DataTable — feed UX wants vertical scroll without filter/pagination
// chrome. Per WG#4: visible runtime banner declares cross-bot scope
// surfaced to operator (BRIEF §7.2 — signals are cross-bot per-symbol;
// no `bot_id` column on signals table).

import * as React from "react";

import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { StatusBadge } from "@/components/StatusBadge";
import type { Signal } from "@/lib/api-types";
import { formatUtcTimeOnly } from "@/lib/format-time";

interface SignalFeedProps {
  signals: Signal[];
}

export function SignalFeed({ signals }: SignalFeedProps): React.JSX.Element {
  return (
    <div data-testid="signal-feed" className="space-y-2">
      <div className="text-xs text-muted-foreground" data-testid="signal-feed-banner">
        All signals (cross-bot per BRIEF §7.2)
      </div>
      {signals.length === 0 ? (
        <div
          data-testid="signal-feed-empty"
          className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
        >
          No signals yet
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {signals.map((s) => (
            <li
              key={s.id}
              data-testid="signal-feed-row"
              className="flex items-center gap-3 px-3 py-2 text-sm"
            >
              <span className="font-mono text-xs text-muted-foreground">
                {formatUtcTimeOnly(s.received_at)}
              </span>
              <span className="font-medium">{s.symbol}</span>
              <span className="text-muted-foreground">{s.action}</span>
              <StatusBadge kind="signal" status={s.ingestion_status} />
              <span className="ml-auto">
                <CorrelationIdChip correlationId={s.correlation_id} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
