/** The charts of PRD §20.7, each opening onto its records. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ChartPoint } from "@/api/types";
import { ChartPanel } from "@/components/charts";
import { Alert, Card, Dialog, EmptyState, Skeleton, Tabs } from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { PostingsDialog, SalesDialog, useWindow } from "./shared";

type Interval = "week" | "month" | "quarter";

const SUBTITLES: Record<string, string> = {
  revenue_profit:
    "Revenue and the result after every expense, against the same months a year before.",
  income_expenses: "Income and expenses posted in each period.",
  cash_flow: "Money into and out of cash, bank and Mobile Money, with the balance beneath.",
  expense_composition: "Expense accounts, largest first.",
  profit_by_product: "Contribution after commission on realised sales.",
  profit_by_category: "Contribution after commission, by catalogue category.",
  channels: "Quotation-led sales against the register.",
  tax_position: "Charged, recoverable and paid in each period; the net owed beneath.",
  ageing: "Outstanding today, by how far past due.",
  supplier_variance: "Where actual landed cost departed from what the quotation estimated.",
  monthly_performance: "This month to date, last month, and the budget where one is approved.",
};

interface Opening {
  title: string;
  point: ChartPoint;
}

export function Analytics() {
  const { range, picker } = useWindow("year");
  const [interval, setStep] = useState<Interval>("month");
  const [opening, setOpening] = useState<Opening | null>(null);
  const list = useQuery({ queryKey: ["analytics", "charts"], queryFn: api.analytics.charts });

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        {picker}
        <Tabs
          items={[
            { id: "week", label: "Weekly" },
            { id: "month", label: "Monthly" },
            { id: "quarter", label: "Quarterly" },
          ]}
          active={interval}
          onChange={setStep}
        />
      </div>
      {list.isPending ? (
        <Skeleton rows={4} height={200} />
      ) : (
        <div className="viz-grid">
          {(list.data ?? []).map((item) => (
            <ChartCard
              key={item.id}
              id={item.id}
              title={item.title}
              start={range.start}
              end={range.end}
              interval={interval}
              onOpen={(point) => setOpening({ title: `${item.title}: ${point.label}`, point })}
            />
          ))}
        </div>
      )}
      {opening ? <Opened opening={opening} onClose={() => setOpening(null)} /> : null}
    </div>
  );
}

function ChartCard({
  id,
  title,
  start,
  end,
  interval,
  onOpen,
}: {
  id: string;
  title: string;
  start: string;
  end: string;
  interval: Interval;
  onOpen: (point: ChartPoint) => void;
}) {
  const currency = useCurrency();
  const chart = useQuery({
    queryKey: ["analytics", "chart", id, start, end, interval],
    queryFn: () => api.analytics.chart(id, start, end, interval),
  });
  return (
    <Card title={title} subtitle={SUBTITLES[id]}>
      {chart.isError ? (
        <Alert tone="error" title={describe(chart.error).message} />
      ) : chart.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <ChartPanel chart={chart.data} currency={currency} onSelect={(point) => onOpen(point)} />
      )}
    </Card>
  );
}

function Opened({ opening, onClose }: { opening: Opening; onClose: () => void }) {
  const { point, title } = opening;
  const drill = point.drill;
  if (drill.measure && drill.start && drill.end) {
    return (
      <PostingsDialog
        title={title}
        queryKey={["analytics", "drill", drill.measure, drill.start, drill.end]}
        load={() => api.analytics.drill(drill.measure!, drill.start!, drill.end!)}
        onClose={onClose}
      />
    );
  }
  if (drill.profit_dimension && drill.start && drill.end) {
    const keys = drill.key ? [drill.key] : ["quotation", "register"];
    return (
      <SalesDialog
        title={title}
        queryKey={[
          "analytics",
          "records",
          drill.profit_dimension,
          keys.join(","),
          drill.start,
          drill.end,
        ]}
        load={async () =>
          (
            await Promise.all(
              keys.map((key) =>
                api.analytics.records(drill.profit_dimension!, key, drill.start!, drill.end!),
              ),
            )
          ).flat()
        }
        onClose={onClose}
      />
    );
  }
  if (drill.register === "ageing")
    return <AgeingDialog bucket={drill.bucket ?? "current"} title={title} onClose={onClose} />;
  if (drill.erosion_supplier) {
    return (
      <SupplierDialog
        supplier={drill.erosion_supplier}
        start={drill.start ?? ""}
        end={drill.end ?? ""}
        title={title}
        onClose={onClose}
      />
    );
  }
  return null;
}

function AgeingDialog({
  bucket,
  title,
  onClose,
}: {
  bucket: string;
  title: string;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const lines = useQuery({
    queryKey: ["analytics", "ageing", bucket],
    queryFn: async () => {
      const [owed, owing] = await Promise.all([
        api.financing.register({ side: "receivable" }),
        api.financing.register({ side: "payable" }),
      ]);
      return [...owed, ...owing].filter((line) => line.bucket === bucket);
    },
  });
  return (
    <Dialog title={title} size="lg" onClose={onClose}>
      {lines.isPending ? (
        <Skeleton rows={5} />
      ) : (lines.data ?? []).length === 0 ? (
        <EmptyState title="Nothing in this band." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Counterparty</th>
              <th>Due</th>
              <th className="numeric">Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {(lines.data ?? []).map((line) => (
              <tr key={`${line.source_type}-${line.source_id}`}>
                <td>
                  {line.reference}
                  <div className="cell-note">{line.kind_label}</div>
                </td>
                <td>{line.counterparty}</td>
                <td className="nowrap">{line.due_on ? formatDate(line.due_on) : "—"}</td>
                <td className="numeric">{money(line.outstanding, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Dialog>
  );
}

function SupplierDialog({
  supplier,
  start,
  end,
  title,
  onClose,
}: {
  supplier: string;
  start: string;
  end: string;
  title: string;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ["analytics", "erosion", start, end],
    queryFn: () => api.analytics.erosion(start, end),
  });
  const rows = (report.data?.rows ?? []).filter((row) => row.supplier === supplier);
  return (
    <Dialog title={title} size="lg" onClose={onClose}>
      {report.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Item</th>
              <th className="numeric">Quoted cost</th>
              <th className="numeric">Actual cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.order_line_id}>
                <td>
                  {row.order_number}
                  <div className="cell-note">{row.customer}</div>
                </td>
                <td className="table__cell--wrap">{row.description}</td>
                <td className="numeric">{money(row.quoted_cost, currency)}</td>
                <td className="numeric">
                  {row.actual_cost === null ? "—" : money(row.actual_cost, currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Dialog>
  );
}
