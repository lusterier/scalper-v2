// T-516b — ShadowVariantsView component tests. 6 tests covering live/
// paper parent_kind URL routing, empty state, active vs terminated
// pills, parent prop undefined skeleton + L-017 active control (pin
// BOTH that Live skeleton renders AND that variants useQuery STILL
// fires independent of parent state).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ShadowVariantsView } from "@/components/ShadowVariantsView";
import type { PaperTrade, ShadowVariant, Trade } from "@/lib/api-types";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

beforeEach(() => {
  mockFetch.mockReset();
});

function mountView(props: {
  parentTradeId: string;
  parentKind: "live" | "paper";
  parent: Trade | PaperTrade | undefined;
}): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShadowVariantsView {...props} />
    </QueryClientProvider> as ReactNode,
  );
}

const sampleTrade: Trade = {
  id: 7,
  bot_id: "alpha",
  signal_id: 100,
  open_order_id: 10,
  close_order_id: 11,
  symbol: "BTCUSDT",
  side: "long",
  entry_price: "50000.00",
  exit_price: "51000.00",
  qty: "0.001",
  notional_usd: "50.00",
  realized_pnl: "1.00",
  fees_paid: "0.05",
  close_reason: "tp",
  opened_at: "2026-05-05T10:00:00Z",
  closed_at: "2026-05-05T12:00:00Z",
  status: "closed",
  mfe_pct: 0.025,
  mae_pct: -0.005,
  confidence_score: 0.78,
  meta: {},
};

const samplePaperTrade: PaperTrade = { ...sampleTrade, id: 42 };

const baseVariant: ShadowVariant = {
  id: 1,
  parent_trade_id: 7,
  bot_id: "alpha",
  variant_name: "no_be",
  side: "long",
  entry_price: "50000.00",
  qty: "0.001",
  created_at: "2026-05-05T10:00:00Z",
  terminated_at: "2026-05-05T11:00:00Z",
  terminal_outcome: "tp_full",
  realized_pnl: "5.00",
  mfe_pct: 0.030,
  mae_pct: -0.002,
  meta: {},
  parent_kind: "live",
};

describe("ShadowVariantsView (T-516b)", () => {
  it("parentKind=live fetches /api/trades/{id}/shadow-variants + renders Live row + N variant rows", async () => {
    mockFetch.mockResolvedValue({
      variants: [baseVariant, { ...baseVariant, id: 2, variant_name: "trail_only" }],
    });
    mountView({ parentTradeId: "7", parentKind: "live", parent: sampleTrade });
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-parent-row")).toBeInTheDocument();
    });
    // Verify URL routing.
    expect(mockFetch).toHaveBeenCalledWith("/api/trades/7/shadow-variants");
    // 1 Live row + 2 variant rows.
    const variantRows = screen.getAllByTestId("shadow-variants-variant-row");
    expect(variantRows).toHaveLength(2);
    // Live pill present.
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("parentKind=paper fetches /api/paper-trades/{id}/shadow-variants", async () => {
    mockFetch.mockResolvedValue({
      variants: [{ ...baseVariant, parent_kind: "paper", parent_trade_id: 42 }],
    });
    mountView({ parentTradeId: "42", parentKind: "paper", parent: samplePaperTrade });
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-parent-row")).toBeInTheDocument();
    });
    expect(mockFetch).toHaveBeenCalledWith("/api/paper-trades/42/shadow-variants");
    expect(screen.getAllByTestId("shadow-variants-variant-row")).toHaveLength(1);
  });

  it("renders 'No shadow variants' message when empty + Live row still rendered", async () => {
    mockFetch.mockResolvedValue({ variants: [] });
    mountView({ parentTradeId: "7", parentKind: "live", parent: sampleTrade });
    await waitFor(() => {
      expect(screen.getByText("No shadow variants for this trade")).toBeInTheDocument();
    });
    // Live parent row still renders.
    expect(screen.getByTestId("shadow-variants-parent-row")).toBeInTheDocument();
    // Zero variant rows.
    expect(screen.queryAllByTestId("shadow-variants-variant-row")).toHaveLength(0);
  });

  it("active variant (terminated_at null) renders 'active' pill + — for outcome and pnl", async () => {
    const activeVariant: ShadowVariant = {
      ...baseVariant,
      terminated_at: null,
      terminal_outcome: null,
      realized_pnl: null,
    };
    mockFetch.mockResolvedValue({ variants: [activeVariant] });
    mountView({ parentTradeId: "7", parentKind: "live", parent: sampleTrade });
    await waitFor(() => {
      expect(screen.getByText("active")).toBeInTheDocument();
    });
    // Variant row exists; active pill rendered.
    const variantRow = screen.getByTestId("shadow-variants-variant-row");
    expect(variantRow).toHaveTextContent("active");
    // realized_pnl null → "—" rendered (multiple — in row; just check no PriceDelta).
    expect(variantRow).toHaveTextContent("—");
  });

  it("terminated variant renders terminal_outcome pill + PriceDelta for realized_pnl", async () => {
    mockFetch.mockResolvedValue({
      variants: [
        {
          ...baseVariant,
          terminated_at: "2026-05-05T11:00:00Z",
          terminal_outcome: "tp_full",
          realized_pnl: "5.00",
        },
      ],
    });
    mountView({ parentTradeId: "7", parentKind: "live", parent: sampleTrade });
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-variant-row")).toBeInTheDocument();
    });
    const variantRow = screen.getByTestId("shadow-variants-variant-row");
    expect(variantRow).toHaveTextContent("tp_full");
    expect(variantRow).toHaveTextContent("5.00");
  });

  it("parent prop undefined renders Live skeleton + variants useQuery STILL fires (L-017)", async () => {
    mockFetch.mockResolvedValue({ variants: [baseVariant] });
    mountView({ parentTradeId: "7", parentKind: "live", parent: undefined });
    // L-017 active control — pin BOTH sides:
    // 1. Live skeleton renders (NOT parent-row).
    expect(screen.getByTestId("shadow-variants-parent-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("shadow-variants-parent-row")).not.toBeInTheDocument();
    // 2. variants useQuery STILL fires independent of parent state.
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/trades/7/shadow-variants");
    });
    // Variants render below skeleton.
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-variant-row")).toBeInTheDocument();
    });
  });
});
