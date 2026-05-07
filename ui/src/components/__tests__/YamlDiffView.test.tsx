import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { YamlDiffView } from "@/components/YamlDiffView";

const HIGHLIGHT_CLASSES = ["bg-green-50", "bg-red-50", "bg-yellow-50"] as const;

function getDiffLines(testid: "diff-current" | "diff-new"): HTMLElement[] {
  const wrapper = screen.getByTestId(testid);
  // Each line is a direct child div. Use .children iteration (bypass jsdom
  // nwsapi bug with :scope > div on Tailwind-classed elements).
  return Array.from(wrapper.children).filter(
    (el): el is HTMLElement => el instanceof HTMLElement && el.tagName === "DIV"
  );
}

describe("YamlDiffView", () => {
  it("identical input renders no highlight classes (§A)", () => {
    render(<YamlDiffView current={"a\nb\nc"} draft={"a\nb\nc"} />);
    const leftLines = getDiffLines("diff-current");
    const rightLines = getDiffLines("diff-new");
    for (const line of [...leftLines, ...rightLines]) {
      for (const cls of HIGHLIGHT_CLASSES) {
        expect(line).not.toHaveClass(cls);
      }
    }
  });

  it("pure addition renders green line on right (§B)", () => {
    render(<YamlDiffView current={"a\nb"} draft={"a\nb\nc"} />);
    const rightLines = getDiffLines("diff-new");
    // Find the line containing "c"
    const addedLine = rightLines.find((line) => line.textContent === "c");
    expect(addedLine).toBeDefined();
    expect(addedLine!).toHaveClass("bg-green-50");
  });

  it("pure deletion renders red line on left (§C)", () => {
    render(<YamlDiffView current={"a\nb\nc"} draft={"a\nb"} />);
    const leftLines = getDiffLines("diff-current");
    const removedLine = leftLines.find((line) => line.textContent === "c");
    expect(removedLine).toBeDefined();
    expect(removedLine!).toHaveClass("bg-red-50");
  });

  it("modification renders yellow on both columns (§D)", () => {
    render(<YamlDiffView current={"a\nb\nc"} draft={"a\nB\nc"} />);
    const leftLines = getDiffLines("diff-current");
    const rightLines = getDiffLines("diff-new");
    const leftModified = leftLines.find((line) => line.textContent === "b");
    const rightModified = rightLines.find((line) => line.textContent === "B");
    expect(leftModified).toBeDefined();
    expect(rightModified).toBeDefined();
    expect(leftModified!).toHaveClass("bg-yellow-50");
    expect(rightModified!).toHaveClass("bg-yellow-50");
  });

  it("first-version (empty current) renders placeholder + full draft as added (§E)", () => {
    render(<YamlDiffView current="" draft={"a\nb"} />);
    // Placeholder text inside diff-current — preserves Strategy.test.tsx:103.
    expect(screen.getByTestId("diff-current")).toHaveTextContent(/no active config/i);
    // Right column lines all green.
    const rightLines = getDiffLines("diff-new");
    expect(rightLines.length).toBeGreaterThan(0);
    for (const line of rightLines) {
      expect(line).toHaveClass("bg-green-50");
    }
  });

  it("empty draft renders full current as removed (§F)", () => {
    render(<YamlDiffView current={"a\nb"} draft="" />);
    const leftLines = getDiffLines("diff-current");
    const removedLines = leftLines.filter((line) => line.classList.contains("bg-red-50"));
    expect(removedLines.length).toBeGreaterThan(0);
  });

  it("renders Current + New (editing) column labels (§G)", () => {
    render(<YamlDiffView current="x" draft="x" />);
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("New (editing)")).toBeInTheDocument();
  });

  it("mixed 5-line scenario LCS correctness (§H — non-isolated remove+add NOT collapsed)", () => {
    // LCS for [alpha,beta,gamma,delta,epsilon] vs [alpha,BETA,delta,epsilon,zeta]
    // = [alpha, delta, epsilon]. Raw ops: equal alpha + removed beta + removed
    // gamma + added BETA + equal delta + equal epsilon + added zeta. The
    // removed+added pair (beta/BETA) is part of a contiguous removed block (beta
    // followed by gamma) so collapse rule does NOT fire — beta + gamma stay red,
    // BETA + zeta stay green. Aggressive line-position-based pairing is F5+
    // optimization (see plan §H + Pass 2 collapse rule docstring).
    const current = "alpha\nbeta\ngamma\ndelta\nepsilon";
    const draft = "alpha\nBETA\ndelta\nepsilon\nzeta";
    render(<YamlDiffView current={current} draft={draft} />);
    const leftLines = getDiffLines("diff-current");
    const rightLines = getDiffLines("diff-new");
    // beta + gamma both removed (red) — non-isolated contiguous block.
    expect(leftLines.find((line) => line.textContent === "beta")).toHaveClass("bg-red-50");
    expect(leftLines.find((line) => line.textContent === "gamma")).toHaveClass("bg-red-50");
    // BETA + zeta both added (green).
    expect(rightLines.find((line) => line.textContent === "BETA")).toHaveClass("bg-green-50");
    expect(rightLines.find((line) => line.textContent === "zeta")).toHaveClass("bg-green-50");
  });

  it("renders root data-testid='yaml-diff-view'", () => {
    render(<YamlDiffView current="x" draft="y" />);
    expect(screen.getByTestId("yaml-diff-view")).toBeInTheDocument();
  });

  it("custom freshBotPlaceholder prop overrides default", () => {
    render(<YamlDiffView current="" draft="x" freshBotPlaceholder="CUSTOM" />);
    const wrapper = screen.getByTestId("diff-current");
    expect(within(wrapper).getByText("CUSTOM")).toBeInTheDocument();
  });
});
