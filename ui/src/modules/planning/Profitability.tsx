/** Contribution by dimension, on what was realised. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ProfitRow } from "@/api/types";
import {
  Alert,
  Card,
  Checkbox,
  EmptyState,
  Skeleton,
  StatusChip,
  Tabs,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { money } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { SalesDialog, loadRecords, useWindow } from "./shared";

const DIMENSIONS = [
  { id: "product", label: "Product" },
  { id: "category", label: "Category" },
  { id: "customer", label: "Customer" },
  { id: "order", label: "Order" },
  { id: "supplier", label: "Supplier or source" },
  { id: "channel", label: "Channel" },
  { id: "branch", label: "Branch" },
  { id: "salesperson", label: "Sales person" },
  { id: "affiliate", label: "Affiliate" },
  { id: "terminal", label: "Register" },
] as const;

type Dimension = (typeof DIMENSIONS)[number]["id"];

export function Profitability() {
  const currency = useCurrency();
  const { range, picker } = useWindow("year");
  const [dimension, setDimension] = useState<Dimension>("product");
  const [allocate, setAllocate] = useState(false);
  const [open, setOpen] = useState<ProfitRow | null>(null);
  const report = useQuery({
    queryKey: ["analytics", "profitability", dimension, range.start, range.end, allocate],
    queryFn: () => api.analytics.profitability(dimension, range.start, range.end, allocate),
  });
  const data = report.data;

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        {picker}
        <Checkbox
          label="Allocate untraced costs and overheads"
          hint="By share of revenue, shown in their own columns."
          checked={allocate}
          onChange={setAllocate}
        />
      </div>
      <Card
        title="Contribution"
        subtitle="Revenue less the actual cost of the goods and the commission earned."
        actions={<StatusChip tone="info">{data?.basis_label ?? "Actual, posted"}</StatusChip>}
        flush
      >
        <div className="command__dimensions">
          <Tabs
            items={DIMENSIONS.map(({ id, label }) => ({ id, label }))}
            active={dimension}
            onChange={setDimension}
          />
        </div>
        {report.isError ? (
          <Alert tone="error" title={describe(report.error).message} />
        ) : report.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={6} />
          </div>
        ) : !data || data.rows.length === 0 ? (
          <EmptyState title="No sales were realised in this period." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>{data.dimension_label}</th>
                  <th className="numeric">Revenue</th>
                  <th className="numeric">Cost of goods</th>
                  <th className="numeric">Gross contribution</th>
                  <th className="numeric">Commission</th>
                  <th className="numeric">After commission</th>
                  {data.allocated ? (
                    <>
                      <th className="numeric">
                        Freight and tax<div className="cell-note">Allocated</div>
                      </th>
                      <th className="numeric">
                        Overheads<div className="cell-note">Allocated</div>
                      </th>
                      <th className="numeric">After allocation</th>
                    </>
                  ) : null}
                  <th className="numeric">Margin</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <Row
                    key={row.key}
                    row={row}
                    allocated={data.allocated}
                    onOpen={() => setOpen(row)}
                  />
                ))}
              </tbody>
              <tfoot>
                <Row row={data.total} allocated={data.allocated} />
              </tfoot>
            </table>
          </div>
        )}
        {data && data.total.pending_lines > 0 ? (
          <p className="caption" style={{ padding: "var(--space-3) var(--space-4)" }}>
            {data.total.pending_lines} lines, {money(data.total.pending_revenue, currency)} of
            revenue, await their actual cost. They are counted in revenue and left out of
            contribution until it is known.
          </p>
        ) : null}
      </Card>
      {open ? (
        <SalesDialog
          title={`${open.label}: sale lines`}
          queryKey={["analytics", "records", dimension, open.key, range.start, range.end]}
          load={loadRecords(dimension, open.key, range.start, range.end)}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}

function Row({
  row,
  allocated,
  onOpen,
}: {
  row: ProfitRow;
  allocated: boolean;
  onOpen?: () => void;
}) {
  const currency = useCurrency();
  const Cell = onOpen ? "td" : "th";
  return (
    <tr>
      <Cell>
        {onOpen ? (
          <button type="button" className="text-button" onClick={onOpen}>
            {row.label}
          </button>
        ) : (
          "All"
        )}
        <div className="cell-note">
          {row.lines} {row.lines === 1 ? "line" : "lines"}
          {row.pending_lines ? ` · ${row.pending_lines} awaiting cost` : ""}
        </div>
      </Cell>
      <td className="numeric">{money(row.revenue, currency)}</td>
      <td className="numeric">{money(row.cost, currency)}</td>
      <td className="numeric">{money(row.gross, currency)}</td>
      <td className="numeric">{money(row.commission, currency)}</td>
      <td className="numeric">{money(row.after_commission, currency)}</td>
      {allocated ? (
        <>
          <td className="numeric">{money(row.direct, currency)}</td>
          <td className="numeric">{money(row.overheads, currency)}</td>
          <td className="numeric">{money(row.after_allocation, currency)}</td>
        </>
      ) : null}
      <td className="numeric">{row.margin === null ? "—" : `${row.margin}%`}</td>
    </tr>
  );
}
