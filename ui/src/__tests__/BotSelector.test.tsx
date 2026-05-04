import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { BotSelector } from "../components/BotSelector";

// Mock api-client at module boundary so useQuery resolves deterministically.
const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

function renderWithQuery(ui: ReactNode): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("BotSelector", () => {
  it("renders disabled with Loading placeholder while query pending", () => {
    mockFetch.mockReturnValueOnce(new Promise(() => undefined)); // never resolves
    renderWithQuery(<BotSelector value="" onChange={vi.fn()} />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("renders bots when query succeeds", async () => {
    mockFetch.mockResolvedValueOnce({
      bots: [
        {
          bot_id: "alpha",
          display_name: "Alpha Bot",
          created_at: "2026-05-04T00:00:00Z",
          status: "active",
          exchange_mode: "paper",
          config_hash: "x",
          config_applied_at: "2026-05-04T00:00:00Z",
          meta: {},
        },
      ],
    });
    renderWithQuery(<BotSelector value="" onChange={vi.fn()} />);
    expect(await screen.findByText("Select bot(s)")).toBeInTheDocument();
  });

  it("renders disabled empty placeholder when bots[] is empty", async () => {
    mockFetch.mockResolvedValueOnce({ bots: [] });
    renderWithQuery(<BotSelector value="" onChange={vi.fn()} />);
    expect(await screen.findByText("no bots configured")).toBeInTheDocument();
  });
});
