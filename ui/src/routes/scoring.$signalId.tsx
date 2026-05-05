// T-418 — Section 7 Scoring inspector drill-down. Per BRIEF §14.3:2066.
// 4 sections: header + signal summary + ScoringBreakdownView (extracted
// from T-414) + FeatureSnapshotTable per evaluation. 404 handling per
// T-414 WG#2 echo + downstream gate per T-414 WG#6.

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { FeatureSnapshotTable } from "@/components/FeatureSnapshotTable";
import { ScoringBreakdownView } from "@/components/ScoringBreakdownView";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import type {
  ScoringEvaluationListResponse,
  SignalDetail,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/scoring/$signalId")({
  component: ScoringDrillDown,
});

const STALE_MS = 30_000;

function ScoringDrillDown(): React.JSX.Element {
  const { signalId } = Route.useParams();

  const signalQuery = useQuery({
    queryKey: ["signal-detail", signalId],
    queryFn: () => apiFetch<SignalDetail>(`/api/signals/${signalId}`),
    staleTime: STALE_MS,
    retry: false,
  });

  // Per WG#6 / T-414 echo — gate scoring fetch on signalQuery RESOLVING
  // successfully (`data !== undefined`), not merely `!isError`. At mount
  // both queries would fire in parallel if gated only on isError, since
  // isError is false until the rejection lands.
  const scoringQuery = useQuery({
    queryKey: ["scoring-by-signal", signalId],
    queryFn: () =>
      apiFetch<ScoringEvaluationListResponse>(`/api/scoring/by-signal/${signalId}`),
    enabled: signalQuery.data !== undefined && !signalQuery.isError,
    staleTime: STALE_MS,
    retry: false,
  });

  // Per WG#5 — 404 handling for /api/signals/{id} (entity not found).
  if (signalQuery.isError) {
    const is404 = signalQuery.error.message.includes("404");
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="signal-not-found">
          {is404 ? `Signal #${signalId} not found` : "Failed to load signal"}
        </div>
        <Link to="/scoring" className="text-sm text-primary underline">
          Back to Scoring
        </Link>
      </div>
    );
  }

  const signal = signalQuery.data;
  const evaluations = scoringQuery.data?.evaluations ?? [];

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="flex items-center gap-3">
          <div className="text-lg font-semibold" data-testid="scoring-header">
            Signal #{signalId}
          </div>
          {signal !== undefined && (
            <StatusBadge kind="signal" status={signal.ingestion_status} />
          )}
        </div>
        <Link to="/scoring" className="text-sm text-primary underline">
          Back to Scoring
        </Link>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Signal summary</CardTitle>
        </CardHeader>
        <CardContent>
          {signalQuery.isLoading ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : signal !== undefined ? (
            <dl
              data-testid="signal-summary"
              className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3"
            >
              <Row label="Signal ID" value={String(signal.id)} />
              <Row label="Source" value={signal.source} />
              <Row label="Symbol" value={signal.symbol} />
              <Row label="Action" value={signal.action} />
              <Row label="Received at" value={formatUtcDateTime(signal.received_at)} />
              <Row label="Schema" value={signal.schema_version} />
              <Row
                label="Idempotency key"
                value={<span className="font-mono text-xs">{signal.idempotency_key}</span>}
              />
              <Row
                label="Correlation"
                value={<CorrelationIdChip correlationId={signal.correlation_id} />}
              />
            </dl>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Scoring breakdown</CardTitle>
        </CardHeader>
        <CardContent>
          {scoringQuery.isLoading ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : scoringQuery.isError ? (
            <span className="text-red-400 text-sm">Failed to load scoring</span>
          ) : evaluations.length === 0 ? (
            <div data-testid="no-scoring-evaluations" className="text-sm text-muted-foreground">
              No scoring evaluations for this signal
            </div>
          ) : (
            <ScoringBreakdownView evaluations={evaluations} />
          )}
        </CardContent>
      </Card>

      {evaluations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Feature snapshots</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {evaluations.map((ev) => (
              <div
                key={`snapshot-${String(ev.id)}`}
                data-testid="feature-snapshot-block"
                className="space-y-1"
              >
                <div className="text-xs text-muted-foreground">
                  Bot {ev.bot_id} · {formatUtcDateTime(ev.evaluated_at)}
                </div>
                <FeatureSnapshotTable snapshot={ev.feature_snapshot} />
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
