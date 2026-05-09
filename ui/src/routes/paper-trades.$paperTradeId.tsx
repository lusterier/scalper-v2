// T-516a2 — Paper-trade drill-down route. Mirror trades.$tradeId.tsx
// modulo URL/type swaps. 8 timeline sections; uses shared trade-drill
// module per plan-reviewer Gate 1 WG#6 (TradeSummary + SignalDetailView
// imported from @/components/trade-drill).
//
// Backend endpoint: /api/paper-trades/{id} (T-516a1 shipped).
// Placeholder #4 (Shadow variants) wording per OQ-2 default + WG-aligned:
// "Coming T-516b (... parent_kind=paper)".

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { ScoringBreakdownView } from "@/components/ScoringBreakdownView";
import { ShadowVariantsView } from "@/components/ShadowVariantsView";
import { TimelineSection } from "@/components/TimelineSection";
import { SignalDetailView, TradeSummary } from "@/components/trade-drill";
import { apiFetch } from "@/lib/api-client";
import type {
  PaperTrade,
  ScoringEvaluationListResponse,
  SignalDetail,
} from "@/lib/api-types";

export const Route = createFileRoute("/paper-trades/$paperTradeId")({
  component: PaperTradeDrillDown,
});

const STALE_MS = 30_000;

function PaperTradeDrillDown(): React.JSX.Element {
  const { paperTradeId } = Route.useParams();

  const tradeQuery = useQuery({
    queryKey: ["paper-trade", paperTradeId],
    queryFn: () => apiFetch<PaperTrade>(`/api/paper-trades/${paperTradeId}`),
    staleTime: STALE_MS,
    retry: false,
  });

  const trade = tradeQuery.data;
  const signalId = trade?.signal_id;

  // Per WG#7 — gate downstream queries on tradeQuery success + non-null
  // signal_id. Mirror trades.$tradeId.tsx parity. PaperTradeDrillDown.test
  // #4 verifies signals/scoring NOT called when signal_id is null
  // (L-017 active control: pin both 'called' AND 'not called' sides).
  const signalQuery = useQuery({
    queryKey: ["signal", signalId],
    queryFn: () => apiFetch<SignalDetail>(`/api/signals/${String(signalId)}`),
    enabled:
      !tradeQuery.isError && trade !== undefined && signalId !== null && signalId !== undefined,
    staleTime: STALE_MS,
  });

  const scoringQuery = useQuery({
    queryKey: ["scoring-by-signal", signalId],
    queryFn: () =>
      apiFetch<ScoringEvaluationListResponse>(`/api/scoring/by-signal/${String(signalId)}`),
    enabled:
      !tradeQuery.isError && trade !== undefined && signalId !== null && signalId !== undefined,
    staleTime: STALE_MS,
  });

  // Per WG#4 — 404 fallback "Paper trade #N not found" (NOT "Trade #N").
  // Backend emits lowercase "paper trade {id} not found"; UI title-cases.
  if (tradeQuery.isError) {
    const is404 = tradeQuery.error.message.includes("404");
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="trade-not-found">
          {is404 ? `Paper trade #${paperTradeId} not found` : "Failed to load paper trade"}
        </div>
        <Link to="/paper-trades" className="text-sm text-primary underline">
          Back to Paper trades
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="text-lg font-semibold">Paper trade #{paperTradeId}</div>
        <Link to="/paper-trades" className="text-sm text-primary underline">
          Back to Paper trades
        </Link>
      </header>

      <TimelineSection title="Trade summary" loading={tradeQuery.isLoading}>
        {trade && <TradeSummary trade={trade} />}
      </TimelineSection>

      <TimelineSection title="Signal details" loading={signalQuery.isLoading}>
        {signalId === null || signalId === undefined ? (
          <div className="text-sm text-muted-foreground">
            No signal (manual or reconcile-driven trade)
          </div>
        ) : signalQuery.data !== undefined ? (
          <SignalDetailView signal={signalQuery.data} />
        ) : null}
      </TimelineSection>

      <TimelineSection title="Scoring breakdown" loading={scoringQuery.isLoading}>
        {signalId === null || signalId === undefined ? (
          <div className="text-sm text-muted-foreground">No scoring evaluation</div>
        ) : (
          <ScoringBreakdownView evaluations={scoringQuery.data?.evaluations ?? []} />
        )}
      </TimelineSection>

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
          parentTradeId={paperTradeId}
          parentKind="paper"
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
