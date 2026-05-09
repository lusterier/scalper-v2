// T-414 — Section 3 Trade explorer drill-down. Per BRIEF §14.3:2062.
// 8 timeline sections = 1 trade summary header + 7 BRIEF tiers (2
// supported: signal/scoring; 5 placeholder F4+/F5+: order events,
// fills, SL moves, shadow variants, post-close snapshots).
//
// T-516a2 — TradeSummary + SignalDetailView lifted to
// @/components/trade-drill (shared with paper-trades.$paperTradeId.tsx).

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { ScoringBreakdownView } from "@/components/ScoringBreakdownView";
import { ShadowVariantsView } from "@/components/ShadowVariantsView";
import { TimelineSection } from "@/components/TimelineSection";
import { SignalDetailView, TradeSummary } from "@/components/trade-drill";
import { apiFetch } from "@/lib/api-client";
import type {
  ScoringEvaluationListResponse,
  SignalDetail,
  Trade,
} from "@/lib/api-types";

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
      <TimelineSection title="Shadow variants" loading={false}>
        <ShadowVariantsView
          parentTradeId={tradeId}
          parentKind="live"
          parent={trade}
        />
      </TimelineSection>
      <TimelineSection
        title="Post-close price snapshots"
        placeholder={{ reason: "Coming F4+ (post_close_snapshots table deferred)" }}
      />
    </div>
  );
}
