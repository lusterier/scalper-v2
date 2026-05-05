// T-414 — Section 3 Trade explorer drill-down. Per BRIEF §14.3:2062.
// 8 timeline sections = 1 trade summary header + 7 BRIEF tiers (2
// supported: signal/scoring; 5 placeholder F4+/F5+: order events,
// fills, SL moves, shadow variants, post-close snapshots).

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { PriceDelta } from "@/components/PriceDelta";
import { StatusBadge } from "@/components/StatusBadge";
import { TimelineSection } from "@/components/TimelineSection";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import type {
  ScoringEvaluation,
  ScoringEvaluationListResponse,
  ScoringRuleResult,
  SignalDetail,
  Trade,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/trades/$tradeId")({
  component: TradeDrillDown,
});

// Per WG#5 — named constant. 30s = matches global staleTime from
// main.tsx; drill-down is read-only navigation, no live SSE concern.
const STALE_MS = 30_000;

function TradeDrillDown(): React.JSX.Element {
  const { tradeId } = Route.useParams();

  const tradeQuery = useQuery({
    queryKey: ["trade", tradeId],
    queryFn: () => apiFetch<Trade>(`/api/trades/${tradeId}`),
    staleTime: STALE_MS,
    retry: false,
  });

  const trade = tradeQuery.data;
  const signalId = trade?.signal_id;

  // Per WG#6 — gate downstream queries on tradeQuery success + non-null
  // signal_id. If trade fetch 404s, we never call /api/signals/undefined.
  const signalQuery = useQuery({
    queryKey: ["signal", signalId],
    queryFn: () => apiFetch<SignalDetail>(`/api/signals/${String(signalId)}`),
    enabled: !tradeQuery.isError && trade !== undefined && signalId !== null && signalId !== undefined,
    staleTime: STALE_MS,
  });

  const scoringQuery = useQuery({
    queryKey: ["scoring-by-signal", signalId],
    queryFn: () =>
      apiFetch<ScoringEvaluationListResponse>(`/api/scoring/by-signal/${String(signalId)}`),
    enabled: !tradeQuery.isError && trade !== undefined && signalId !== null && signalId !== undefined,
    staleTime: STALE_MS,
  });

  // Per WG#2 — 404 substring detect against apiFetch error format
  // (api-client.ts:20 throws `API ${path} failed: ${res.status} ...`).
  // Test 16 mocks an Error message containing literal "404".
  if (tradeQuery.isError) {
    const is404 = tradeQuery.error.message.includes("404");
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="trade-not-found">
          {is404 ? `Trade #${tradeId} not found` : "Failed to load trade"}
        </div>
        <Link to="/trades" className="text-sm text-primary underline">
          Back to Trades
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="text-lg font-semibold">Trade #{tradeId}</div>
        <Link to="/trades" className="text-sm text-primary underline">
          Back to Trades
        </Link>
      </header>

      {/* Header card — Trade summary (folds in BRIEF "close" tier) */}
      <TimelineSection title="Trade summary" loading={tradeQuery.isLoading}>
        {trade && <TradeSummary trade={trade} />}
      </TimelineSection>

      {/* BRIEF Tier 1 — Signal details */}
      <TimelineSection title="Signal details" loading={signalQuery.isLoading}>
        {signalId === null || signalId === undefined ? (
          <div className="text-sm text-muted-foreground">
            No signal (manual or reconcile-driven trade)
          </div>
        ) : signalQuery.data !== undefined ? (
          <SignalDetailView signal={signalQuery.data} />
        ) : null}
      </TimelineSection>

      {/* BRIEF Tier 2 — Scoring breakdown */}
      <TimelineSection title="Scoring breakdown" loading={scoringQuery.isLoading}>
        {signalId === null || signalId === undefined ? (
          <div className="text-sm text-muted-foreground">No scoring evaluation</div>
        ) : (
          <ScoringBreakdownView evaluations={scoringQuery.data?.evaluations ?? []} />
        )}
      </TimelineSection>

      {/* BRIEF Tier 3-7 — placeholders */}
      <TimelineSection
        title="Order events"
        placeholder={{ reason: "Coming F4+ (trading_events endpoint deferred)" }}
      />
      <TimelineSection
        title="Fills"
        placeholder={{ reason: "Coming F4+ (executions endpoint deferred)" }}
      />
      <TimelineSection
        title="SL moves"
        placeholder={{ reason: "Coming F4+ (derives from trading_events; deferred)" }}
      />
      <TimelineSection
        title="Shadow variants"
        placeholder={{ reason: "Coming F5+ (shadow runtime deferred)" }}
      />
      <TimelineSection
        title="Post-close price snapshots"
        placeholder={{ reason: "Coming F4+ (post_close_snapshots table deferred)" }}
      />
    </div>
  );
}

function TradeSummary({ trade }: { trade: Trade }): React.JSX.Element {
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

function SignalDetailView({ signal }: { signal: SignalDetail }): React.JSX.Element {
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

function ScoringBreakdownView({
  evaluations,
}: {
  evaluations: ScoringEvaluation[];
}): React.JSX.Element {
  if (evaluations.length === 0) {
    return <div className="text-sm text-muted-foreground">No scoring evaluations</div>;
  }
  return (
    <div className="space-y-4" data-testid="scoring-breakdown">
      {evaluations.map((ev) => (
        <Card key={ev.id} className="border-muted">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <span>Bot: {ev.bot_id}</span>
              <span className="text-muted-foreground">·</span>
              <span>Score: {ev.total_score.toFixed(3)}</span>
              <span className="text-muted-foreground">/ {ev.trigger_threshold.toFixed(3)}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {formatUtcDateTime(ev.evaluated_at)}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            <div className="text-muted-foreground">
              decision: <span className="font-mono text-foreground">{ev.decision}</span>
            </div>
            <ul className="divide-y divide-border rounded-md border border-border">
              {ev.rule_results.map((r, idx) => (
                <li
                  key={`${ev.id}-${String(idx)}-${r.name}`}
                  data-testid="scoring-rule-row"
                  className="flex items-center gap-2 px-3 py-1.5"
                >
                  <span className="font-medium">{r.name}</span>
                  <span className="text-muted-foreground">
                    weight {r.weight.toFixed(2)}
                  </span>
                  <span className="text-muted-foreground">
                    applied {r.applied_weight.toFixed(2)}
                  </span>
                  <ResultBadge rule={r} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// Per BLOCKER #1 — `result` is loose string. Map known values to badge
// tones; unknown strings render muted.
function ResultBadge({ rule }: { rule: ScoringRuleResult }): React.JSX.Element {
  const tone = (() => {
    if (rule.result === "True") return "text-green-400";
    if (rule.result === "False") return "text-muted-foreground";
    if (rule.result === "n/a" || rule.result === "skipped") return "text-yellow-400";
    return "text-red-400";
  })();
  const errMsg =
    rule.error !== null && typeof rule.error["error"] === "string" ? rule.error["error"] : null;
  return (
    <span
      className={`ml-auto font-mono text-xs ${tone}`}
      data-result={rule.result}
      title={errMsg ?? undefined}
    >
      {rule.result}
    </span>
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
