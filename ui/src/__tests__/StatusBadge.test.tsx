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
});
