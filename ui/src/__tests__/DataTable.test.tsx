import { type ColumnDef } from "@tanstack/react-table";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "../components/DataTable";

interface Row {
  id: number;
  name: string;
}

const COLUMNS: ColumnDef<Row, unknown>[] = [
  { accessorKey: "id", header: "ID" },
  { accessorKey: "name", header: "Name" },
];

const SAMPLE: Row[] = [
  { id: 1, name: "alpha" },
  { id: 2, name: "beta" },
];

describe("DataTable", () => {
  it("renders rows from data prop", () => {
    render(<DataTable columns={COLUMNS} data={SAMPLE} />);
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("renders emptyMessage when data is empty", () => {
    render(<DataTable columns={COLUMNS} data={[]} emptyMessage="Nothing yet" />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });

  it("onRowClick fires with row data when clicking a row", async () => {
    const onRowClick = vi.fn();
    const user = userEvent.setup();
    render(<DataTable columns={COLUMNS} data={SAMPLE} onRowClick={onRowClick} />);
    await user.click(screen.getByText("alpha"));
    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick.mock.calls[0]?.[0]).toEqual({ id: 1, name: "alpha" });
  });
});
