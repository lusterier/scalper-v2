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

const sampleConfig = {
  id: 7,
  bot_id: "alpha",
  version: 3,
  applied_at: "2026-05-05T10:00:00Z",
  applied_by: "luster",
  config_yaml: "bot_id: alpha\nexchange:\n  mode: paper\n",
  config_hash: "deadbeef",
  notes: "v3 baseline",
};

const sampleVersions = {
  versions: [
    sampleConfig,
    {
      ...sampleConfig,
      id: 6,
      version: 2,
      applied_at: "2026-05-04T10:00:00Z",
      config_yaml: "bot_id: alpha\nexchange:\n  mode: testnet\n",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};

describe("Strategy editor route (T-416)", () => {
  it("renders editor with current config_yaml from /api/configs/<bot_id>", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      // Validate POST is fired when editor seeds.
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: true,
          bot_id: "alpha",
          parsed_version: 3,
          errors: [],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      const editor = screen.getByTestId("yaml-editor") as HTMLTextAreaElement;
      expect(editor.value).toContain("bot_id: alpha");
    });
  });

  it("404 fresh-bot placeholder — 'No active config' message + empty textarea (per WG#9)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") {
        return Promise.reject(
          new Error("API /api/configs/alpha failed: 404 no bot_config for 'alpha'"),
        );
      }
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve({ versions: [], total: 0, limit: 50, offset: 0 });
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      expect(screen.getByTestId("fresh-bot-message")).toHaveTextContent(
        /No active config/,
      );
    });
    expect(screen.getByTestId("diff-current")).toHaveTextContent(/no active config/i);
  });

  it("diff side-by-side renders both current + new <pre> blocks (per OQ-4=C)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: true,
          bot_id: "alpha",
          parsed_version: 3,
          errors: [],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(
      () => {
        expect(screen.getByTestId("diff-current").textContent).toContain("bot_id: alpha");
      },
      { timeout: 3000 },
    );
    expect(screen.getByTestId("diff-new").textContent).toContain("bot_id: alpha");
  });

  it("validation panel renders red errors when /api/configs/validate returns valid=false", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: false,
          bot_id: "alpha",
          parsed_version: null,
          errors: ["yaml.scanner.ScannerError: mapping values are not allowed here"],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(
      () => {
        const errors = screen.getByTestId("validation-errors");
        expect(errors.textContent).toMatch(/scanner|mapping values/i);
      },
      { timeout: 3000 },
    );
  });

  it("Apply button disabled when validation invalid (per WG#7 compound condition)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: false,
          bot_id: "alpha",
          parsed_version: null,
          errors: ["broken yaml"],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      expect(screen.getByTestId("validation-errors")).toBeInTheDocument();
    });
    expect(screen.getByTestId("apply-button")).toBeDisabled();
  });

  it("versions DataTable populated from /api/configs/<bot_id>/versions", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: true,
          bot_id: "alpha",
          parsed_version: 3,
          errors: [],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      // Multiple version rows / cells share text values — assert ≥1 occurrence
      // for each. T-411 DataTable renders the row once but column-cell label
      // text may appear multiple times in the rendered DOM.
      expect(screen.getAllByText("luster").length).toBeGreaterThan(0);
      expect(screen.getAllByText("v3 baseline").length).toBeGreaterThan(0);
    });
  });

  it("Apply button opens Apply modal with applied_by + notes form (per OQ-5=A)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: true,
          bot_id: "alpha",
          parsed_version: 3,
          errors: [],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      expect(screen.getByTestId("apply-button")).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("apply-button"));
    await waitFor(() => {
      expect(screen.getByTestId("applied-by-input")).toBeInTheDocument();
      expect(screen.getByTestId("apply-notes")).toBeInTheDocument();
    });
  });

  it("validation panel shows green 'valid (parsed v3)' when valid=true", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
      if (url === "/api/configs/alpha/versions?limit=50")
        return Promise.resolve(sampleVersions);
      if (url === "/api/configs/validate") {
        return Promise.resolve({
          valid: true,
          bot_id: "alpha",
          parsed_version: 3,
          errors: [],
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/strategy/alpha");
    await waitFor(() => {
      const valid = screen.getByTestId("validation-valid");
      expect(valid.textContent).toMatch(/valid \(parsed v3\)/);
    });
  });
});
