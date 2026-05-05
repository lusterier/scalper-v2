import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "../components/StatusBadge";

describe("StatusBadge", () => {
  it("renders bot active with green tone class", () => {
    render(<StatusBadge kind="bot" status="active" />);
    const badge = screen.getByText("active");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("text-green-400");
  });

  it("renders unknown (kind, status) combo without throwing (gray tone fallback)", () => {
    render(<StatusBadge kind="bot" status="unknown_status" />);
    const badge = screen.getByText("unknown_status");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("text-muted-foreground");
  });

  it("kind=backtest renders correct tone for queued/running/completed/failed (T-415 WG#4)", () => {
    const cases: Array<{ status: string; tone: string }> = [
      { status: "queued", tone: "text-yellow-400" },
      { status: "running", tone: "text-blue-400" },
      { status: "completed", tone: "text-green-400" },
      { status: "failed", tone: "text-red-400" },
    ];
    for (const c of cases) {
      const { unmount } = render(<StatusBadge kind="backtest" status={c.status} />);
      const badge = screen.getByText(c.status);
      expect(badge.className).toContain(c.tone);
      unmount();
    }
  });
});
