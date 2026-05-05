// T-413 — Section 2 Per-bot live view. Per BRIEF §14.3:2061 — for the
// selected bot: open positions table + live signals feed (last 50) +
// P&L chart. Live updates via T-408 SSE (`/events/stream?types=...`).
//
// Per WG#3: refetch intervals are NAMED CONSTANTS with rationale
// comments (UI cache TTL aligned with global staleTime + SSE-
// invalidation fallback, per L-001 they are NOT new business-logic
// timing knobs). Per WG#1: feedRing local state + queryClient
// invalidation declared in component body. Per WG#9: unrecognized
// botId param renders not-found placeholder, not crashed render.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { ConnectionDot } from "@/components/ConnectionDot";
import { DataTable } from "@/components/DataTable";
import { PnlChart } from "@/components/PnlChart";
import { PriceDelta } from "@/components/PriceDelta";
import { SignalFeed } from "@/components/SignalFeed";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import type {
  BotListResponse,
  OpenPosition,
  OpenPositionListResponse,
  PaginatedSignalListResponse,
  PnlSeriesResponse,
  Signal,
} from "@/lib/api-types";
import { useSSEStream } from "@/lib/hooks/useSSEStream";
import { useNavStore } from "@/store/nav";
import { useSSEStore } from "@/store/sse";

// Per WG#3 — named constants with rationale comments. NOT business-
// logic timing knobs (per L-001 these are UI cache TTL aligned with
// global staleTime); inline rationale prevents re-derivation at
// brief-reviewer stage.
//
// 5s = SSE-invalidation fallback ceiling; UI cache TTL aligned with
// global staleTime; if SSE drops, user sees fresh data within 5s.
const POSITIONS_REFETCH_MS = 5_000;
// 30s = matches global staleTime; SSE prepends update feed between
// refetches.
const SIGNALS_REFETCH_MS = 30_000;
// 30s = matches global staleTime; pnl-series has no SSE emission
// (analytics aggregate, not lifecycle event).
const PNL_REFETCH_MS = 30_000;

const SIGNALS_RING_MAX = 50;
const SIGNALS_INITIAL_LIMIT = 50;
const WINDOW_24H_MS = 24 * 60 * 60 * 1000;

// Hoist to module scope so the array reference is stable across renders
// (useEffect in useSSEStream compares deps by content via string key,
// but a stable reference avoids any future caller-side regression).
const SSE_TYPES = ["positions", "signals", "trades"] as const;

export const Route = createFileRoute("/bot/$botId")({
  component: PerBotPage,
});

function PerBotPage(): React.JSX.Element {
  const { botId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setLastSelectedBotId = useNavStore((s) => s.setLastSelectedBotId);
  const sseStatus = useSSEStore((s) => s.status);

  React.useEffect(() => {
    setLastSelectedBotId(botId);
  }, [botId, setLastSelectedBotId]);

  const [pickerRange, setPickerRange] = React.useState<TimeRange>(() => {
    const now = new Date();
    return { from: new Date(now.getTime() - WINDOW_24H_MS), to: now, preset: "24h" };
  });

  // Per WG#1 — explicit feedRing local state owned by route component.
  const [feedRing, setFeedRing] = React.useState<Signal[]>([]);

  // Per WG#9 — guard against unrecognized botId before mounting panels.
  const botsQuery = useQuery({
    queryKey: ["bots-for-validation"],
    queryFn: () => apiFetch<BotListResponse>("/api/bots/"),
  });

  const positionsQuery = useQuery({
    queryKey: ["per-bot-positions", botId],
    queryFn: () =>
      apiFetch<OpenPositionListResponse>(`/api/positions/?bot_id=${encodeURIComponent(botId)}`),
    refetchInterval: POSITIONS_REFETCH_MS,
  });

  const signalsQuery = useQuery({
    queryKey: ["per-bot-signals-initial", botId],
    queryFn: () =>
      apiFetch<PaginatedSignalListResponse>(
        `/api/signals/?limit=${String(SIGNALS_INITIAL_LIMIT)}`,
      ),
    refetchInterval: SIGNALS_REFETCH_MS,
  });

  React.useEffect(() => {
    if (signalsQuery.data !== undefined) {
      setFeedRing(signalsQuery.data.signals.slice(0, SIGNALS_RING_MAX));
    }
  }, [signalsQuery.data]);

  const fromIso = React.useMemo(() => pickerRange.from.toISOString(), [pickerRange.from]);
  const toIso = React.useMemo(() => pickerRange.to.toISOString(), [pickerRange.to]);

  const pnlQuery = useQuery({
    queryKey: ["per-bot-pnl", botId, fromIso, toIso],
    queryFn: () =>
      apiFetch<PnlSeriesResponse>(
        `/api/analytics/pnl-series?bucket=hour&bot_id=${encodeURIComponent(botId)}` +
          `&from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`,
      ),
    refetchInterval: PNL_REFETCH_MS,
  });

  const onSSEEvent = React.useCallback(
    (event: { type: string; payload: unknown }) => {
      if (event.type === "positions" || event.type === "trades") {
        void queryClient.invalidateQueries({ queryKey: ["per-bot-positions", botId] });
      } else if (event.type === "signals") {
        const payload = event.payload as Signal | undefined;
        if (payload === undefined) return;
        setFeedRing((prev) => [payload, ...prev].slice(0, SIGNALS_RING_MAX));
      }
    },
    [botId, queryClient],
  );

  useSSEStream(SSE_TYPES, onSSEEvent);

  // Per WG#9 — botId not in the bots list (operator typed `/bot/garbage`).
  const knownBotIds = botsQuery.data?.bots.map((b) => b.bot_id) ?? [];
  const botExists = !botsQuery.isLoading && knownBotIds.includes(botId);

  if (!botsQuery.isLoading && !botExists) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="bot-not-found">
          Bot &quot;{botId}&quot; not found
        </div>
        <Link to="/" className="text-sm text-primary underline">
          Back to Overview
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between gap-4 border-b border-border p-4">
        <div className="flex-shrink-0">
          <BotSelector
            value={botId}
            onChange={(v) => {
              if (typeof v === "string") {
                void navigate({ to: "/bot/$botId", params: { botId: v } });
              }
            }}
          />
        </div>
        <div className="flex flex-1 justify-center">
          <TimeRangePicker value={pickerRange} onChange={setPickerRange} />
        </div>
        <div className="flex flex-shrink-0 items-center gap-2 text-xs text-muted-foreground">
          <ConnectionDot status={sseStatus} />
          <span>SSE</span>
        </div>
      </header>

      <div className="grid grid-cols-3 gap-4 px-6">
        <Card className="col-span-2">
          <CardHeader>
            <CardTitle className="text-sm">Open positions</CardTitle>
          </CardHeader>
          <CardContent>
            {positionsQuery.isLoading ? (
              <span className="text-muted-foreground">Loading…</span>
            ) : positionsQuery.isError ? (
              <span className="text-red-400 text-sm">Failed to load</span>
            ) : (
              <DataTable
                columns={POSITION_COLUMNS}
                data={positionsQuery.data?.positions ?? []}
                enableColumnVisibility={false}
                enableFiltering={false}
                emptyMessage="No open positions"
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Live signals</CardTitle>
          </CardHeader>
          <CardContent>
            <SignalFeed signals={feedRing} />
          </CardContent>
        </Card>
      </div>

      <Card className="mx-6">
        <CardHeader>
          <CardTitle className="text-sm">Cumulative P&L (24h)</CardTitle>
        </CardHeader>
        <CardContent>
          {pnlQuery.isLoading ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : pnlQuery.isError ? (
            <span className="text-red-400 text-sm">Failed to load</span>
          ) : (
            <PnlChart data={pnlQuery.data?.points ?? []} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

const POSITION_COLUMNS: ColumnDef<OpenPosition, unknown>[] = [
  { accessorKey: "symbol", header: "Symbol" },
  { accessorKey: "side", header: "Side" },
  {
    accessorKey: "entry_price",
    header: "Entry",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.entry_price}</span>
    ),
  },
  {
    accessorKey: "qty",
    header: "Qty",
    cell: ({ row }) => <span className="font-mono text-xs">{row.original.qty}</span>,
  },
  {
    accessorKey: "running_pnl",
    header: "Unrealized P&L",
    cell: ({ row }) => <PriceDelta value={row.original.running_pnl} />,
  },
  {
    accessorKey: "sl_price",
    header: "SL",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.sl_price ?? "—"}</span>
    ),
  },
  {
    accessorKey: "tp_price",
    header: "TP",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.tp_price ?? "—"}</span>
    ),
  },
  {
    accessorKey: "mfe_price",
    header: "MFE",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.mfe_price ?? "—"}</span>
    ),
  },
  {
    accessorKey: "mae_price",
    header: "MAE",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.mae_price ?? "—"}</span>
    ),
  },
];
