import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OverviewTile } from "../components/OverviewTile";

describe("OverviewTile", () => {
  it("renders value when not loading or error", () => {
    render(<OverviewTile title="Test" value="42" />);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Test")).toBeInTheDocument();
  });

  it("renders 'Loading…' when loading=true (literal owned by OverviewTile per WG#2)", () => {
    render(<OverviewTile title="Test" loading value="ignored" />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    // Value is suppressed during loading.
    expect(screen.queryByText("ignored")).not.toBeInTheDocument();
  });

  it("renders subtitle 'Coming F4+' by default when placeholder=true", () => {
    render(<OverviewTile title="Virtual balance" placeholder />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Coming F4+")).toBeInTheDocument();
  });
});
