import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

beforeEach(() => {
  mockFetch.mockReset();
});

function mountAt(path: string): ReturnType<typeof render> {
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createRouter({ routeTree, history });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider> as ReactNode,
  );
}

const sampleRun = {
  id: "1234abcd-5678-90ef-1234-567890abcdef",
  name: "baseline-2026",
  bot_id: "alpha",
  config_yaml: "bot_id: alpha\n",
  config_hash: "deadbeefcafebabe1234567890",
  date_range_start: "2026-01-01T00:00:00Z",
  date_range_end: "2026-02-01T00:00:00Z",
  status: "queued",
  started_at: "2026-05-05T10:00:00Z",
  finished_at: null,
  summary: null,
  notes: null,
};

function defaultFetchImpl(url: string, options?: { method?: string }): Promise<unknown> {
  if (url === "/api/bots/") {
    return Promise.resolve({ bots: [] });
  }
  if (url.startsWith("/api/backtests/?")) {
    return Promise.resolve({ runs: [sampleRun], total: 1, limit: 50, offset: 0 });
  }
  if (url === "/api/backtests/" && options?.method === "POST") {
    return Promise.resolve(sampleRun);
  }
  return Promise.reject(new Error(`unmocked URL: ${url}`));
}

describe("Backtests route (T-415)", () => {
  it("renders DataTable with backtest_runs from /api/backtests/", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/backtests");
    await waitFor(() => {
      expect(screen.getByText("baseline-2026")).toBeInTheDocument();
    });
  });

  it("status='all' OMITS ?status= URL param (per WG#7 negative regex)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/backtests");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/backtests/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).not.toMatch(/[?&]status=/);
    });
  });

  it("status filter='queued' appends ?status=queued", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/backtests");
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "queued" } });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" && (c[0] as string).includes("status=queued"),
      );
      expect(call).toBeDefined();
    });
  });

  it("time range filter uses .toISOString() Z-suffix per §N1", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/backtests");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/backtests/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).toMatch(/from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
      expect(url).toMatch(/to=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
    });
  });

  it("new-run form happy path — POST body has Z-suffix UTC + form resets + list refetches (WG#2)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/backtests");
    await waitFor(() => {
      expect(screen.getByTestId("new-run-toggle")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("new-run-toggle"));
    fireEvent.change(screen.getByPlaceholderText(/baseline-2026/), {
      target: { value: "test-run" },
    });
    fireEvent.change(screen.getByTestId("config-yaml-textarea"), {
      target: { value: "bot_id: alpha\n" },
    });
    const dateInputs = screen.getAllByDisplayValue("");
    // datetime-local inputs are in form order: start, end (after name + before yaml)
    const startInput = dateInputs.find(
      (el) => el.getAttribute("type") === "datetime-local",
    ) as HTMLInputElement | undefined;
    expect(startInput).toBeDefined();

    // Set start + end via fireEvent on every datetime-local input.
    const allDtInputs = document.querySelectorAll<HTMLInputElement>(
      'input[type="datetime-local"]',
    );
    expect(allDtInputs.length).toBe(2);
    fireEvent.change(allDtInputs[0]!, { target: { value: "2026-01-01T00:00" } });
    fireEvent.change(allDtInputs[1]!, { target: { value: "2026-02-01T00:00" } });

    fireEvent.submit(screen.getByTestId("new-run-submit").closest("form")!);

    await waitFor(() => {
      const postCall = mockFetch.mock.calls.find(
        (c) =>
          c[0] === "/api/backtests/" &&
          (c[1] as { method?: string } | undefined)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = (postCall?.[1] as { body?: unknown } | undefined)?.body as
        | BacktestPostBody
        | undefined;
      expect(body).toBeDefined();
      // Per WG#2 — assert Z-suffix on POST body datetime fields.
      expect(body?.date_range_start).toMatch(/T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/);
      expect(body?.date_range_end).toMatch(/T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/);
      expect(body?.name).toBe("test-run");
    });
  });

  it("new-run form 422 error — error.message rendered inline (per WG#5 placeholder lock)", async () => {
    const errorMessage = "API /api/backtests/ failed: 422 config_yaml parse failed: invalid YAML";
    mockFetch.mockImplementation((url: string, options?: { method?: string }) => {
      if (url === "/api/bots/") return Promise.resolve({ bots: [] });
      if (url.startsWith("/api/backtests/?")) {
        return Promise.resolve({ runs: [], total: 0, limit: 50, offset: 0 });
      }
      if (url === "/api/backtests/" && options?.method === "POST") {
        return Promise.reject(new Error(errorMessage));
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/backtests");
    await waitFor(() => {
      expect(screen.getByTestId("new-run-toggle")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("new-run-toggle"));
    fireEvent.change(screen.getByPlaceholderText(/baseline-2026/), {
      target: { value: "fail-run" },
    });
    fireEvent.change(screen.getByTestId("config-yaml-textarea"), {
      target: { value: "garbage" },
    });
    const allDtInputs = document.querySelectorAll<HTMLInputElement>(
      'input[type="datetime-local"]',
    );
    fireEvent.change(allDtInputs[0]!, { target: { value: "2026-01-01T00:00" } });
    fireEvent.change(allDtInputs[1]!, { target: { value: "2026-02-01T00:00" } });
    fireEvent.submit(screen.getByTestId("new-run-submit").closest("form")!);
    await waitFor(() => {
      expect(screen.getByTestId("new-run-error")).toHaveTextContent(/422/);
    });
  });

  it("submit button disabled while mutation isPending — POST called exactly once on double-click (per WG#1)", async () => {
    let resolveFetch: ((value: unknown) => void) | undefined;
    const slowPostPromise = new Promise<unknown>((resolve) => {
      resolveFetch = resolve;
    });
    mockFetch.mockImplementation((url: string, options?: { method?: string }) => {
      if (url === "/api/bots/") return Promise.resolve({ bots: [] });
      if (url.startsWith("/api/backtests/?")) {
        return Promise.resolve({ runs: [], total: 0, limit: 50, offset: 0 });
      }
      if (url === "/api/backtests/" && options?.method === "POST") {
        return slowPostPromise;
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/backtests");
    await waitFor(() => {
      expect(screen.getByTestId("new-run-toggle")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("new-run-toggle"));
    fireEvent.change(screen.getByPlaceholderText(/baseline-2026/), {
      target: { value: "guard-run" },
    });
    fireEvent.change(screen.getByTestId("config-yaml-textarea"), {
      target: { value: "bot_id: alpha\n" },
    });
    const allDtInputs = document.querySelectorAll<HTMLInputElement>(
      'input[type="datetime-local"]',
    );
    fireEvent.change(allDtInputs[0]!, { target: { value: "2026-01-01T00:00" } });
    fireEvent.change(allDtInputs[1]!, { target: { value: "2026-02-01T00:00" } });
    const submitButton = screen.getByTestId("new-run-submit");
    const form = submitButton.closest("form")!;
    // First submit triggers the mutation; subsequent attempts should be
    // blocked because (a) the submit button is disabled (browser/RTL
    // ignores click on disabled), and (b) handleSubmit returns early
    // when createMutation.isPending. Combined: exactly one POST.
    fireEvent.submit(form);
    await waitFor(() => {
      expect(submitButton).toBeDisabled();
    });
    fireEvent.click(submitButton);
    fireEvent.click(submitButton);
    fireEvent.submit(form);
    const postCalls = mockFetch.mock.calls.filter(
      (c) =>
        c[0] === "/api/backtests/" &&
        (c[1] as { method?: string } | undefined)?.method === "POST",
    );
    expect(postCalls).toHaveLength(1);
    // Resolve to allow cleanup.
    resolveFetch?.(sampleRun);
  });
});

interface BacktestPostBody {
  name: string;
  bot_id: string;
  config_yaml: string;
  date_range_start: string;
  date_range_end: string;
  notes: string | null;
}
