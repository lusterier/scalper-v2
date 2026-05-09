// T-516b — Shadow variants drill-down section. Replaces placeholder #4
// in BOTH trades.$tradeId.tsx (live) + paper-trades.$paperTradeId.tsx
// (paper) routes. Renders all variants alongside live outcome per
// BRIEF §13.6:2038 ("per-trade drill-down shows all 5 variants
// alongside the live outcome").
//
// 7-col compact table per OQ-2: type pill / variant_name / side /
// entry_price / qty / terminal_outcome (or active pill) / realized_pnl
// (PriceDelta) / MFE-MAE. Live parent row at top per OQ-3.
//
// Per Write-time guidance #1: root container uses
// data-testid="shadow-variants-view"; loading skeletons use
// data-testid="shadow-variants-loading"; component MUST NOT render
// any data-testid="timeline-placeholder" (placeholder count assertion
// in TradeDrillDown.test.tsx + PaperTradeDrillDown.test.tsx
// transitions 5→4 cleanly).

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { PriceDelta } from "@/components/PriceDelta";
import { apiFetch } from "@/lib/api-client";
import type {
  PaperTrade,
  ShadowVariant,
  ShadowVariantListResponse,
  Trade,
} from "@/lib/api-types";

const STALE_MS = 30_000;

export function ShadowVariantsView({
  parentTradeId,
  parentKind,
  parent,
}: {
  parentTradeId: string;
  parentKind: "live" | "paper";
  parent: Trade | PaperTrade | undefined;
}): React.JSX.Element {
  const url =
    parentKind === "live"
      ? `/api/trades/${parentTradeId}/shadow-variants`
      : `/api/paper-trades/${parentTradeId}/shadow-variants`;

  const variantsQuery = useQuery({
    queryKey: ["shadow-variants", parentKind, parentTradeId],
    queryFn: () => apiFetch<ShadowVariantListResponse>(url),
    staleTime: STALE_MS,
    retry: false,
  });

  const variants = variantsQuery.data?.variants ?? [];

  return (
    <div data-testid="shadow-variants-view" className="space-y-2">
      <table className="w-full text-sm">
        <thead className="text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="text-left font-medium pb-2">Type</th>
            <th className="text-left font-medium pb-2">Name</th>
            <th className="text-left font-medium pb-2">Side</th>
            <th className="text-left font-medium pb-2">Entry</th>
            <th className="text-left font-medium pb-2">Qty</th>
            <th className="text-left font-medium pb-2">Outcome</th>
            <th className="text-left font-medium pb-2">P&amp;L</th>
            <th className="text-left font-medium pb-2">MFE / MAE</th>
          </tr>
        </thead>
        <tbody>
          <ParentRow parent={parent} />
          {variantsQuery.isLoading ? (
            <tr data-testid="shadow-variants-loading">
              <td colSpan={8} className="py-2 text-muted-foreground">
                Loading variants…
              </td>
            </tr>
          ) : variantsQuery.isError ? (
            <tr>
              <td colSpan={8} className="py-2 text-red-400">
                Failed to load shadow variants
              </td>
            </tr>
          ) : variants.length === 0 ? (
            <tr>
              <td colSpan={8} className="py-2 text-muted-foreground">
                No shadow variants for this trade
              </td>
            </tr>
          ) : (
            variants.map((v) => <VariantRow key={v.id} variant={v} />)
          )}
        </tbody>
      </table>
    </div>
  );
}

function ParentRow({
  parent,
}: {
  parent: Trade | PaperTrade | undefined;
}): React.JSX.Element {
  if (parent === undefined) {
    return (
      <tr data-testid="shadow-variants-parent-skeleton">
        <td colSpan={8} className="py-2 text-muted-foreground">
          Loading live outcome…
        </td>
      </tr>
    );
  }
  return (
    <tr data-testid="shadow-variants-parent-row" className="border-t border-border">
      <td className="py-1.5">
        <Pill kind="live">Live</Pill>
      </td>
      <td className="py-1.5 text-xs text-muted-foreground">live</td>
      <td className="py-1.5">{parent.side}</td>
      <td className="py-1.5 font-mono text-xs">{parent.entry_price}</td>
      <td className="py-1.5 font-mono text-xs">{parent.qty}</td>
      <td className="py-1.5">
        <Pill kind="status">{parent.status}</Pill>
      </td>
      <td className="py-1.5">
        {parent.realized_pnl !== null ? (
          <PriceDelta value={parent.realized_pnl} />
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-1.5 text-xs text-muted-foreground">
        {formatPctPair(parent.mfe_pct, parent.mae_pct)}
      </td>
    </tr>
  );
}

function VariantRow({ variant }: { variant: ShadowVariant }): React.JSX.Element {
  const isActive = variant.terminated_at === null;
  return (
    <tr data-testid="shadow-variants-variant-row" className="border-t border-border">
      <td className="py-1.5">
        <Pill kind="variant">Variant</Pill>
      </td>
      <td className="py-1.5 text-xs">{variant.variant_name}</td>
      <td className="py-1.5">{variant.side}</td>
      <td className="py-1.5 font-mono text-xs">{variant.entry_price}</td>
      <td className="py-1.5 font-mono text-xs">{variant.qty}</td>
      <td className="py-1.5">
        {isActive ? (
          <Pill kind="active">active</Pill>
        ) : (
          <Pill kind="outcome">{variant.terminal_outcome ?? "—"}</Pill>
        )}
      </td>
      <td className="py-1.5">
        {variant.realized_pnl !== null ? (
          <PriceDelta value={variant.realized_pnl} />
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-1.5 text-xs text-muted-foreground">
        {formatPctPair(variant.mfe_pct, variant.mae_pct)}
      </td>
    </tr>
  );
}

function Pill({
  kind,
  children,
}: {
  kind: "live" | "variant" | "active" | "outcome" | "status";
  children: React.ReactNode;
}): React.JSX.Element {
  const palette: Record<typeof kind, string> = {
    live: "bg-primary/15 text-primary",
    variant: "bg-secondary/30 text-secondary-foreground",
    active: "bg-yellow-500/15 text-yellow-400",
    outcome: "bg-muted text-foreground",
    status: "bg-muted text-foreground",
  };
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${palette[kind]}`}
    >
      {children}
    </span>
  );
}

function formatPctPair(mfe: number | null, mae: number | null): string {
  const fmt = (n: number | null): string =>
    n === null ? "—" : `${(n * 100).toFixed(2)}%`;
  return `${fmt(mfe)} / ${fmt(mae)}`;
}
