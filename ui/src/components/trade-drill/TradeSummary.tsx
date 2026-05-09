// T-516a2 — shared trade summary card. Renders 12-row dl grid.
// Lifted verbatim from ui/src/routes/trades.$tradeId.tsx:137-175 (T-414).
// Accepts Trade | PaperTrade union prop per OQ-1 default — backend §3.1:268
// paper-live symmetry invariant guarantees structurally identical 21 fields.

import * as React from "react";

import { PriceDelta } from "@/components/PriceDelta";
import { StatusBadge } from "@/components/StatusBadge";
import type { PaperTrade, Trade } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

import { Row } from "./Row";

export function TradeSummary({
  trade,
}: {
  trade: Trade | PaperTrade;
}): React.JSX.Element {
  return (
    <dl
      data-testid="trade-summary"
      className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3"
    >
      <Row label="Bot" value={trade.bot_id} />
      <Row label="Symbol" value={trade.symbol} />
      <Row label="Side" value={trade.side} />
      <Row
        label="Status"
        value={<StatusBadge kind="trade" status={trade.status} />}
      />
      <Row label="Entry price" value={<span className="font-mono">{trade.entry_price}</span>} />
      <Row
        label="Exit price"
        value={<span className="font-mono">{trade.exit_price ?? "—"}</span>}
      />
      <Row label="Qty" value={<span className="font-mono">{trade.qty}</span>} />
      <Row label="Notional USD" value={<span className="font-mono">{trade.notional_usd}</span>} />
      <Row
        label="Realized P&L"
        value={
          trade.realized_pnl !== null ? (
            <PriceDelta value={trade.realized_pnl} />
          ) : (
            <span className="text-muted-foreground">—</span>
          )
        }
      />
      <Row label="Close reason" value={trade.close_reason ?? "—"} />
      <Row label="Opened at" value={formatUtcDateTime(trade.opened_at)} />
      <Row
        label="Closed at"
        value={trade.closed_at !== null ? formatUtcDateTime(trade.closed_at) : "—"}
      />
    </dl>
  );
}
