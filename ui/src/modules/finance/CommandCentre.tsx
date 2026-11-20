/**
 * The command centre: the headline measures for a period, each opening onto the journal
 * lines it is made of.
 *
 * Every figure is the ledger's. A tile is a sum of postings on named accounts, and its
 * drill-down lists those postings with a total that equals the tile, so the dashboard and
 * the books can never tell two stories.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { MeasureRecord, Widget } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Select,
  Skeleton,
  Tabs,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

type Period = "month" | "quarter" | "year";

const PERIODS: { id: Period; label: string }[] = [
  { id: "month", label: "This month" },
  { id: "quarter", label: "This quarter" },
  { id: "year", label: "This year" },
];

const DIMENSIONS = [
  { id: "channel", label: "Channel" },
  { id: "product", label: "Product" },
  { id: "category", label: "Category" },
  { id: "customer", label: "Customer" },
  { id: "branch", label: "Branch" },
  { id: "salesperson", label: "Sales person" },
] as const;

type Dimension = (typeof DIMENSIONS)[number]["id"];

const SIZE_LABEL: Record<Widget["size"], string> = { small: "S", medium: "M", large: "L" };

function iso(day: Date): string {
  const month = String(day.getMonth() + 1).padStart(2, "0");
  const date = String(day.getDate()).padStart(2, "0");
  return `${day.getFullYear()}-${month}-${date}`;
}

function periodOf(period: Period): { start: string; end: string } {
  const today = new Date();
  const year = today.getFullYear();
  if (period === "year") return { start: `${year}-01-01`, end: iso(today) };
  if (period === "quarter") {
    const first = Math.floor(today.getMonth() / 3) * 3;
    return { start: iso(new Date(year, first, 1)), end: iso(today) };
  }
  return { start: iso(new Date(year, today.getMonth(), 1)), end: iso(today) };
}

export function CommandCentre() {
  const { can } = useSession();
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const [period, setPeriod] = useState<Period>("month");
  const [drilling, setDrilling] = useState<MeasureRecord | null>(null);
  const [arranging, setArranging] = useState<Widget[] | null>(null);
  const [dimension, setDimension] = useState<Dimension>("channel");
  const { start, end } = useMemo(() => periodOf(period), [period]);

  const layout = useQuery({ queryKey: ["command", "layout"], queryFn: api.commandCentre.layout });
  const measures = useQuery({
    queryKey: ["command", "measures", start, end],
    queryFn: () => api.commandCentre.measures(start, end),
  });
  const revenue = useQuery({
    queryKey: ["command", "revenue", dimension, start, end],
    queryFn: () => api.commandCentre.revenue(dimension, start, end),
  });

  const save = useMutation({
    mutationFn: (widgets: Widget[]) =>
      api.commandCentre.saveLayout(widgets, layout.data?.preset ?? "owner"),
    onSuccess: (next) => {
      queries.setQueryData(["command", "layout"], next);
      setArranging(null);
      toast.show("success", "Layout saved.");
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const reset = useMutation({
    mutationFn: api.commandCentre.resetLayout,
    onSuccess: (next) => {
      queries.setQueryData(["command", "layout"], next);
      setArranging(null);
    },
  });

  if (!can("report.view")) {
    return <EmptyState title="The command centre is shown to those who read reports." />;
  }

  const byCode = new Map((measures.data ?? []).map((item) => [item.code, item]));
  const widgets = arranging ?? layout.data?.widgets ?? [];
  const unused = (measures.data ?? []).filter(
    (item) => !widgets.some((widget) => widget.measure === item.code),
  );

  const move = (index: number, by: number) => {
    const next = [...widgets];
    const [item] = next.splice(index, 1);
    if (item) next.splice(index + by, 0, item);
    setArranging(next);
  };
  const resize = (index: number, size: Widget["size"]) =>
    setArranging(widgets.map((item, at) => (at === index ? { ...item, size } : item)));

  return (
    <div className="stack gap-6">
      <div className="command__bar">
        <Tabs items={PERIODS} active={period} onChange={setPeriod} />
        <span className="caption">
          {formatDate(start)} to {formatDate(end)}
        </span>
        <div className="row gap-2">
          {arranging ? (
            <>
              <Button onClick={() => reset.mutate()} busy={reset.isPending}>
                Use the default
              </Button>
              <Button onClick={() => setArranging(null)}>Cancel</Button>
              <Button tone="primary" busy={save.isPending} onClick={() => save.mutate(widgets)}>
                Save layout
              </Button>
            </>
          ) : (
            <Button onClick={() => setArranging(layout.data?.widgets ?? [])}>Arrange</Button>
          )}
        </div>
      </div>

      {measures.isError ? <Alert tone="error" title={describe(measures.error).message} /> : null}
      {measures.isPending || layout.isPending ? (
        <Skeleton rows={3} height={96} />
      ) : (
        <div className="command__grid">
          {widgets.map((widget, index) => {
            const item = byCode.get(widget.measure);
            if (!item) return null;
            return (
              <div key={widget.measure} className={`command__tile command__tile--${widget.size}`}>
                <button
                  type="button"
                  className="measure command__measure"
                  disabled={Boolean(arranging)}
                  onClick={() => setDrilling(item)}
                  aria-label={`${item.label}: show the postings`}
                >
                  <span className="measure__label">{item.label}</span>
                  <span className="measure__value">{money(item.value, currency)}</span>
                  <span className="measure__footer">
                    {item.basis === "position"
                      ? `At ${formatDate(item.ends_on)}`
                      : `${formatDate(item.starts_on)} to ${formatDate(item.ends_on)}`}
                  </span>
                </button>
                {arranging ? (
                  <div
                    className="command__controls"
                    role="group"
                    aria-label={`Arrange ${item.label}`}
                  >
                    <Button
                      size="compact"
                      tone="plain"
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      Earlier
                    </Button>
                    <Button
                      size="compact"
                      tone="plain"
                      disabled={index === widgets.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      Later
                    </Button>
                    {(["small", "medium", "large"] as const).map((size) => (
                      <Button
                        key={size}
                        size="compact"
                        tone={widget.size === size ? "primary" : "plain"}
                        aria-pressed={widget.size === size}
                        onClick={() => resize(index, size)}
                      >
                        {SIZE_LABEL[size]}
                      </Button>
                    ))}
                    <Button
                      size="compact"
                      tone="plain"
                      disabled={widgets.length === 1}
                      onClick={() => setArranging(widgets.filter((_, at) => at !== index))}
                    >
                      Remove
                    </Button>
                  </div>
                ) : null}
              </div>
            );
          })}
          {arranging && unused.length > 0 ? (
            <div className="command__tile command__tile--small command__add">
              <Select
                aria-label="Add a measure"
                value=""
                onChange={(event) =>
                  event.target.value &&
                  setArranging([...widgets, { measure: event.target.value, size: "medium" }])
                }
              >
                <option value="">Add a measure…</option>
                {unused.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </div>
          ) : null}
        </div>
      )}

      <Card
        title="Revenue"
        subtitle="Net of discount, from invoices and register sales in the period."
        flush
      >
        <div className="command__dimensions">
          <Tabs
            items={DIMENSIONS.map(({ id, label }) => ({ id, label }))}
            active={dimension}
            onChange={setDimension}
          />
        </div>
        {revenue.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (revenue.data ?? []).length === 0 ? (
          <EmptyState title="No sales in this period." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{DIMENSIONS.find((item) => item.id === dimension)?.label}</th>
                <th className="numeric">Net sales</th>
              </tr>
            </thead>
            <tbody>
              {(revenue.data ?? []).map((row) => (
                <tr key={row.key}>
                  <td>{row.label}</td>
                  <td className="numeric">{money(row.amount, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {drilling ? (
        <DrillDialog measure={drilling} start={start} end={end} onClose={() => setDrilling(null)} />
      ) : null}
    </div>
  );
}

function DrillDialog({
  measure,
  start,
  end,
  onClose,
}: {
  measure: MeasureRecord;
  start: string;
  end: string;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const drill = useQuery({
    queryKey: ["command", "drill", measure.code, start, end],
    queryFn: () => api.commandCentre.drill(measure.code, start, end),
  });

  return (
    <Dialog title={measure.label} size="lg" onClose={onClose}>
      <div className="stack gap-4">
        <p className="caption">{measure.description}</p>
        {drill.isPending ? (
          <Skeleton rows={6} />
        ) : !drill.data || drill.data.lines.length === 0 ? (
          <EmptyState title="No postings make up this measure in the period." />
        ) : (
          <>
            <div className="table-frame">
              <table className="table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Entry</th>
                    <th>Account</th>
                    <th>Narration</th>
                    <th className="numeric">Contributes</th>
                  </tr>
                </thead>
                <tbody>
                  {drill.data.lines.map((line, index) => (
                    <tr key={`${line.entry_id}-${line.account_code}-${index}`}>
                      <td>{formatDate(line.posting_date)}</td>
                      <td>{line.number}</td>
                      <td>
                        {line.account_code}
                        <div className="cell-note">{line.account_name}</div>
                      </td>
                      <td className="table__cell--wrap">{line.narration}</td>
                      <td className="numeric">{money(line.contribution, currency)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th colSpan={4}>Total, as the measure</th>
                    <th className="numeric">{money(drill.data.total, currency)}</th>
                  </tr>
                </tfoot>
              </table>
            </div>
            {drill.data.truncated ? (
              <p className="cell-note">The first lines are shown; the total covers them all.</p>
            ) : null}
          </>
        )}
      </div>
    </Dialog>
  );
}
