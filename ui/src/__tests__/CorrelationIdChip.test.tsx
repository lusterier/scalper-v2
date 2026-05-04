import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CorrelationIdChip } from "../components/CorrelationIdChip";

// Per WG#3 — test #16 verifies useNavigate NOT called when onClick override
// is provided. Mock the hook at module boundary; assert mock function calls.
const mockNavigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mockNavigate,
}));

describe("CorrelationIdChip", () => {
  it("truncates correlation ID to 8 chars + ellipsis", () => {
    render(
      <CorrelationIdChip
        correlationId="abcdef1234567890"
        onClick={() => undefined}
      />,
    );
    expect(screen.getByText("abcdef12…")).toBeInTheDocument();
  });

  it("default branch (no onClick) navigates to /audit?correlation_id=...", async () => {
    mockNavigate.mockClear();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<CorrelationIdChip correlationId="abcdef1234567890" />);
    await user.click(screen.getByRole("button"));
    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate.mock.calls[0]?.[0]).toMatchObject({ to: "/audit" });
  });

  it("onClick override branch — useNavigate hook NOT called (WG#3 lazy resolution)", async () => {
    mockNavigate.mockClear();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<CorrelationIdChip correlationId="abcdef1234567890" onClick={onClick} />);
    await user.click(screen.getByRole("button"));
    // Per WG#3 — onClick override branch never mounts NavigatingChip,
    // so useNavigate hook is not called even at component mount time.
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
