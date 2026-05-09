// T-516a2 — shared signal-detail card. Renders 9-row dl grid.
// Lifted verbatim from ui/src/routes/trades.$tradeId.tsx:177-197 (T-414).
// SignalDetail is shared model (signals are bot-level, not paper/live-mode);
// no union needed.

import * as React from "react";

import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { StatusBadge } from "@/components/StatusBadge";
import type { SignalDetail } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

import { Row } from "./Row";

export function SignalDetailView({
  signal,
}: {
  signal: SignalDetail;
}): React.JSX.Element {
  return (
    <dl
      data-testid="signal-detail"
      className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3"
    >
      <Row label="Signal ID" value={String(signal.id)} />
      <Row label="Source" value={signal.source} />
      <Row label="Symbol" value={signal.symbol} />
      <Row label="Action" value={signal.action} />
      <Row
        label="Status"
        value={<StatusBadge kind="signal" status={signal.ingestion_status} />}
      />
      <Row label="Received at" value={formatUtcDateTime(signal.received_at)} />
      <Row label="Schema" value={signal.schema_version} />
      <Row label="Idempotency key" value={<span className="font-mono text-xs">{signal.idempotency_key}</span>} />
      <Row label="Correlation" value={<CorrelationIdChip correlationId={signal.correlation_id} />} />
    </dl>
  );
}
