// T-412 — Section 1 Overview cross-bot dashboard route. Per BRIEF
// §14.3:2060 — tiles: total open positions, aggregate virtual balance,
// 24h P&L, signals received/accepted/rejected, alert count.
//
// Layout per BRIEF §14.2:2052 — top bar (BotSelector multi + TimeRange
// Picker + ConnectionDot) + tile grid below. All queries auto-refetch
// every 30 s (matches global staleTime per L-001 active control).

import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { ConnectionDot } from "@/components/ConnectionDot";
import { OverviewTile } from "@/components/OverviewTile";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { apiFetch } from "@/lib/api-client";
import type {
  OpenPositionListResponse,
  PnlSeriesResponse,
  SignalListResponse,
} from "@/lib/api-types";
import { useNavStore } from "@/store/nav";
import { useSSEStore } from "@/store/sse";

export const Route = createFileRoute("/")({
  component: OverviewPage,
});

const REFETCH_MS = 30_000;
const WINDOW_24H_MS = 24 * 60 * 60 * 1000;

function buildPositionsUrl(selectedBots: string[]): string {
  // Per WG (plan §Behavioural contract): exactly-one bot selected → use
  // ?bot_id=<id>; zero or multiple → fetch cross-bot and filter client-
  // side. MVP scale (<10 bots × <50 positions) makes the cross-bot fetch
  // cheaper than N parallel requests.
  if (selectedBots.length === 1) {
    return `/api/positions/?bot_id=${encodeURIComponent(selectedBots[0]!)}`;
  }
  return "/api/positions/";
}

function buildPnlUrl(selectedBots: string[], fromIso: string, toIso: string): string {
  const base = `/api/analytics/pnl-series?bucket=hour&from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`;
  if (selectedBots.length === 1) {
    return `${base}&bot_id=${encodeURIComponent(selectedBots[0]!)}`;
  }
  return base;
}

function buildSignalsUrl(
  fromIso: string,
  toIso: string,
  ingestionStatus?: "validated" | "invalid",
): string {
  let url = `/api/signals/?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}&limit=1`;
  if (ingestionStatus !== undefined) {
    url += `&ingestion_status=${ingestionStatus}`;
  }
  return url;
}

function OverviewPage(): React.JSX.Element {
  // Per OQ-D=B: TimeRangePicker rendered with all 5 presets but Overview-
  // tile queries are 24h-fixed regardless of picker selection. BRIEF
  // §14.3:2060 hardcodes 24h for Overview tiles; TimeRangePicker is in
  // the shared top-bar (§14.2:2052) for visual consistency with T-413+
  // sections that respect picker arbitrary windows.
  const [pickerRange, setPickerRange] = React.useState<TimeRange>(() => {
    const now = new Date();
    return { from: new Date(now.getTime() - WINDOW_24H_MS), to: now, preset: "24h" };
  });
  const [selectedBots, setSelectedBots] = React.useState<string[]>([]);
  const setLastSelectedBotId = useNavStore((s) => s.setLastSelectedBotId);

  // Query window pinned to last 24 h — recomputed once per OverviewPage
  // mount + each refetch tick (memoized so URL strings stay stable across
  // identical renders, otherwise TanStack Query keys would re-thrash).
  const { fromIso, toIso } = React.useMemo(() => {
    const now = new Date();
    const from = new Date(now.getTime() - WINDOW_24H_MS);
    return { fromIso: from.toISOString(), toIso: now.toISOString() };
  }, []);

  const positionsQuery = useQuery({
    queryKey: ["overview-positions", selectedBots],
    queryFn: () => apiFetch<OpenPositionListResponse>(buildPositionsUrl(selectedBots)),
    refetchInterval: REFETCH_MS,
  });

  const pnlQuery = useQuery({
    queryKey: ["overview-pnl-24h", selectedBots, fromIso, toIso],
    queryFn: () => apiFetch<PnlSeriesResponse>(buildPnlUrl(selectedBots, fromIso, toIso)),
    refetchInterval: REFETCH_MS,
  });

  const signalsReceivedQuery = useQuery({
    queryKey: ["overview-signals-received", fromIso, toIso],
    queryFn: () => apiFetch<SignalListResponse>(buildSignalsUrl(fromIso, toIso)),
    refetchInterval: REFETCH_MS,
  });

  const signalsAcceptedQuery = useQuery({
    queryKey: ["overview-signals-accepted", fromIso, toIso],
    queryFn: () =>
      apiFetch<SignalListResponse>(buildSignalsUrl(fromIso, toIso, "validated")),
    refetchInterval: REFETCH_MS,
  });

  // Per OQ-C1: rejected = ingestion_status=invalid only. "duplicate" is
  // neither accepted nor rejected (idempotency-key collision); visible
  // via T-419 audit log. received != accepted + rejected by design.
  const signalsRejectedQuery = useQuery({
    queryKey: ["overview-signals-rejected", fromIso, toIso],
    queryFn: () =>
      apiFetch<SignalListResponse>(buildSignalsUrl(fromIso, toIso, "invalid")),
    refetchInterval: REFETCH_MS,
  });

  const positionsCount = (() => {
    const data = positionsQuery.data;
    if (data === undefined) return undefined;
    if (selectedBots.length <= 1) return data.positions.length;
    return data.positions.filter((p) => selectedBots.includes(p.bot_id)).length;
  })();

  const pnl24h = (() => {
    const data = pnlQuery.data;
    if (data === undefined || data.points.length === 0) return undefined;
    return data.points[data.points.length - 1]!.cumulative_pnl;
  })();

  return (
    <div className="space-y-0">
      <header className="flex items-center justify-between gap-4 border-b border-border bg-card px-5 py-3">
        <div className="flex-shrink-0">
          <BotSelector
            multi
            value={selectedBots}
            onChange={(v) => {
              const next = Array.isArray(v) ? v : [v];
              setSelectedBots(next);
              if (next.length > 0) setLastSelectedBotId(next[0]!);
            }}
            placeholder="All bots"
          />
        </div>
        <div className="flex flex-1 justify-center">
          <TimeRangePicker value={pickerRange} onChange={setPickerRange} />
        </div>
        <OverviewConnectionIndicator />

      </header>

      <div className="grid grid-cols-5 gap-4 p-6">
        <OverviewTile
          title="Open positions"
          loading={positionsQuery.isLoading}
          error={positionsQuery.isError ? "Failed to load" : undefined}
          value={positionsCount !== undefined ? String(positionsCount) : undefined}
          subtitle={
            selectedBots.length === 0
              ? "All bots"
              : `${String(selectedBots.length)} bot${selectedBots.length === 1 ? "" : "s"}`
          }
        />
        <OverviewTile title="Virtual balance" placeholder />
        <OverviewTile
          title="24h P&L"
          loading={pnlQuery.isLoading}
          error={pnlQuery.isError ? "Failed to load" : undefined}
          value={pnl24h !== undefined ? <span data-value={pnl24h}>{pnl24h}</span> : "—"}
          subtitle="Cumulative, last 24h"
        />
        <OverviewTile
          title="Signals (24h)"
          loading={
            signalsReceivedQuery.isLoading ||
            signalsAcceptedQuery.isLoading ||
            signalsRejectedQuery.isLoading
          }
          error={
            signalsReceivedQuery.isError ||
            signalsAcceptedQuery.isError ||
            signalsRejectedQuery.isError
              ? "Failed to load"
              : undefined
          }
          value={
            <SignalCounts
              received={signalsReceivedQuery.data?.total}
              accepted={signalsAcceptedQuery.data?.total}
              rejected={signalsRejectedQuery.data?.total}
            />
          }
          subtitle="received / accepted / rejected"
        />
        <OverviewTile title="Alerts (24h)" placeholder />
      </div>
    </div>
  );
}

// T-413 per WG#5 / OQ-5=A — Overview ConnectionDot now reads
// useSSEStore status (was hardcoded "unknown" in T-412 placeholder).
// Indicator surfaces real EventSource lifecycle when /bot/$botId is
// mounted; on Overview alone the status stays "unknown" because no
// route here calls useSSEStream.
function OverviewConnectionIndicator(): React.JSX.Element {
  const status = useSSEStore((s) => s.status);
  return (
    <div className="flex flex-shrink-0 items-center gap-2 text-xs text-muted-foreground">
      <ConnectionDot status={status} />
      <span>SSE</span>
    </div>
  );
}

interface SignalCountsProps {
  received: number | undefined;
  accepted: number | undefined;
  rejected: number | undefined;
}

function SignalCounts({ received, accepted, rejected }: SignalCountsProps): React.JSX.Element {
  const fmt = (n: number | undefined): string => (n === undefined ? "—" : String(n));
  return (
    <span className="font-trading flex items-baseline gap-1">
      <span data-testid="signals-received" className="text-2xl font-bold text-foreground">
        {fmt(received)}
      </span>
      <span className="text-muted-foreground/50 text-lg">/</span>
      <span data-testid="signals-accepted" className="text-2xl font-bold text-primary">
        {fmt(accepted)}
      </span>
      <span className="text-muted-foreground/50 text-lg">/</span>
      <span data-testid="signals-rejected" className="text-2xl font-bold text-destructive">
        {fmt(rejected)}
      </span>
    </span>
  );
}
