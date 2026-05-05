import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SignalFeed } from "../components/SignalFeed";

// Mock useNavigate so CorrelationIdChip default-branch (no onClick)
// renders without RouterProvider.
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

describe("SignalFeed", () => {
  it("renders signals in newest-first order with cross-bot banner", () => {
    const signals = [
      {
        id: 3,
        received_at: "2026-05-05T12:00:00Z",
        symbol: "BTCUSDT",
        action: "long_open",
        ingestion_status: "validated" as const,
        correlation_id: "abc12345",
      },
      {
        id: 2,
        received_at: "2026-05-05T11:00:00Z",
        symbol: "ETHUSDT",
        action: "short_open",
        ingestion_status: "duplicate" as const,
        correlation_id: "def67890",
      },
    ];
    render(<SignalFeed signals={signals} />);
    // Per WG#4 — visible runtime banner re cross-bot scope.
    expect(screen.getByTestId("signal-feed-banner")).toHaveTextContent(
      "All signals (cross-bot per BRIEF §7.2)",
    );
    const rows = screen.getAllByTestId("signal-feed-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("BTCUSDT");
    expect(rows[1]).toHaveTextContent("ETHUSDT");
  });

  it("renders 'No signals yet' when signals=[]", () => {
    render(<SignalFeed signals={[]} />);
    expect(screen.getByTestId("signal-feed-empty")).toBeInTheDocument();
    expect(screen.getByText("No signals yet")).toBeInTheDocument();
  });
});
