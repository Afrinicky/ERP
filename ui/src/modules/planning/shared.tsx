/** What the planning screens share: the reporting window and the record dialogs. */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PostingRecord, SaleFact } from "@/api/types";
import { Dialog, EmptyState, Skeleton, Tabs } from "@/components/primitives";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export type Span = "month" | "quarter" | "year" | "twelve";

export const WINDOWS: { id: Span; label: string }[] = [
  { id: "month", label: "This month" },
  { id: "quarter", label: "This quarter" },
  { id: "year", label: "This year" },
  { id: "twelve", label: "Last 12 months" },
];

function iso(day: Date): string {
  const m = String(day.getMonth() + 1).padStart(2, "0");
  const d = String(day.getDate()).padStart(2, "0");
  return `${day.getFullYear()}-${m}-${d}`;
}

export function windowOf(choice: Span): { start: string; end: string } {
  const now = new Date();
  const year = now.getFullYear();
  if (choice === "year") return { start: `${year}-01-01`, end: iso(now) };
  if (choice === "quarter") {
    return { start: iso(new Date(year, Math.floor(now.getMonth() / 3) * 3, 1)), end: iso(now) };
  }
  if (choice === "twelve") {
    return { start: iso(new Date(year, now.getMonth() - 11, 1)), end: iso(now) };
  }
  return { start: iso(new Date(year, now.getMonth(), 1)), end: iso(now) };
}

export function useWindow(initial: Span = "year") {
  const [choice, setChoice] = useState<Span>(initial);
  const range = useMemo(() => windowOf(choice), [choice]);
  const picker = <Tabs items={WINDOWS} active={choice} onChange={setChoice} />;
  return { choice, range, picker };
}

export function today(): string {
  return iso(new Date());
}

/** The postings a ledger-built figure is made of. */
export function PostingsDialog({
  title,
  load,
  queryKey,
  onClose,
}: {
  title: string;
  load: () => Promise<PostingRecord[]>;
  queryKey: unknown[];
  onClose: () => void;
}) {
  const currency = useCurrency();
  const lines = useQuery({ queryKey, queryFn: load });
  return (
    <Dialog title={title} size="lg" onClose={onClose}>
      {lines.isPending ? (
        <Skeleton rows={6} />
      ) : (lines.data ?? []).length === 0 ? (
        <EmptyState title="No postings make up this figure." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Entry</th>
                <th>Account</th>
                <th>Narration</th>
                <th className="numeric">Amount</th>
              </tr>
            </thead>
            <tbody>
              {(lines.data ?? []).map((line, index) => (
                <tr key={`${line.entry_id}-${index}`}>
                  <td className="nowrap">{formatDate(line.posting_date)}</td>
                  <td className="nowrap">{line.number ?? "—"}</td>
                  <td>
                    {line.account_code}
                    <div className="cell-note">{line.account_name}</div>
                  </td>
                  <td className="table__cell--wrap">{line.narration}</td>
                  <td className="numeric">{money(line.amount, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}

/** The sale lines behind a profitability row or chart segment. */
export function SalesDialog({
  title,
  load,
  queryKey,
  onClose,
}: {
  title: string;
  load: () => Promise<SaleFact[]>;
  queryKey: unknown[];
  onClose: () => void;
}) {
  const currency = useCurrency();
  const lines = useQuery({ queryKey, queryFn: load });
  return (
    <Dialog title={title} size="lg" onClose={onClose}>
      {lines.isPending ? (
        <Skeleton rows={6} />
      ) : (lines.data ?? []).length === 0 ? (
        <EmptyState title="No sales make up this figure." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Reference</th>
                <th>Item</th>
                <th className="numeric">Quantity</th>
                <th className="numeric">Revenue</th>
                <th className="numeric">Cost of goods</th>
                <th className="numeric">Commission</th>
              </tr>
            </thead>
            <tbody>
              {(lines.data ?? []).map((line, index) => (
                <tr key={`${line.source_id}-${index}`}>
                  <td className="nowrap">{formatDate(line.on)}</td>
                  <td>
                    {line.reference}
                    <div className="cell-note">
                      {line.channel === "register" ? "Point of sale" : "Invoice"}
                    </div>
                  </td>
                  <td className="table__cell--wrap">{line.description}</td>
                  <td className="numeric">{Number(line.quantity).toLocaleString("en-GB")}</td>
                  <td className="numeric">{money(line.revenue, currency)}</td>
                  <td className="numeric">
                    {line.cost === null ? (
                      <span className="cell-note">Awaiting actual cost</span>
                    ) : (
                      money(line.cost, currency)
                    )}
                  </td>
                  <td className="numeric">{money(line.commission, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}

export const loadRecords = (dimension: string, key: string, start: string, end: string) => () =>
  api.analytics.records(dimension, key, start, end);
