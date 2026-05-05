import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TimelineSection } from "../components/TimelineSection";

describe("TimelineSection", () => {
  it("renders children when not loading/error/placeholder", () => {
    render(
      <TimelineSection title="Test">
        <div data-testid="child">child content</div>
      </TimelineSection>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
    expect(screen.getByText("Test")).toBeInTheDocument();
  });

  it("renders Loading… when loading=true", () => {
    render(<TimelineSection title="X" loading />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders placeholder.reason when placeholder is set", () => {
    render(<TimelineSection title="X" placeholder={{ reason: "Coming F4+ test" }} />);
    expect(screen.getByTestId("timeline-placeholder")).toHaveTextContent("Coming F4+ test");
  });
});
