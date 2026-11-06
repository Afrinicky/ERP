/** The sales position: what is out, what has been won, and what is waiting on you. */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Measure,
  Skeleton,
  StatusChip,
  type Tone,
} from "@/components/primitives";
import { date as formatDate, humanise, money, percent } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export const STATE_TONE: Record<string, Tone> = {
  draft: "neutral",
  pending_approval: "warning",
  issued: "info",
  accepted: "ok",
  rejected: "error",
  expired: "neutral",
  superseded: "neutral",
};

export function SalesOverview({ onOpen }: { onOpen: (view: string) => void }) {
  const currency = useCurrency();
  const summary = useQuery({
    queryKey: ["sales", "summary"],
    queryFn: api.quotations.summary,
  });
  const issued = useQuery({
    queryKey: ["sales", "quotations", "issued"],
    queryFn: () => api.quotations.list({ state: "issued", limit: 8 }),
  });

  if (summary.isPending) return <Skeleton rows={6} height={64} />;
  if (!summary.data)
    return <EmptyState title="The sales position is not available." />;

  const figures = summary.data;
  const counts = figures.counts ?? {};

  return (
    <div className="stack gap-6">
      {figures.awaiting_approval > 0 ? (
        <Alert
          tone="warning"
          title={`${figures.awaiting_approval} quotation(s) are waiting for approval`}
        >
          A quotation whose discount exceeds the author’s limit cannot be issued
          until someone with the authority has approved it.
        </Alert>
      ) : null}

      <div className="measure-grid">
        <Measure
          label="Out with customers"
          value={money(figures.issued_value, currency)}
          period={`${counts.issued ?? 0} issued quotations`}
        />
        <Measure
          label="Accepted"
          value={money(figures.accepted_value, currency)}
          period={`${counts.accepted ?? 0} quotations taken up`}
        />
        <Measure
          label="Acceptance rate"
          value={percent(figures.acceptance_rate, 1)}
          period="Of those the customer has decided on"
        />
        <Measure
          label="Open requests"
          value={String(figures.open_requests)}
          period="Awaiting a quotation"
          footer={
            figures.open_requests > 0 ? (
              <Button
                tone="plain"
                size="compact"
                onClick={() => onOpen("requests")}
              >
                Open requests
              </Button>
            ) : (
              <StatusChip tone="ok">Nothing outstanding</StatusChip>
            )
          }
        />
      </div>

      <Card
        title="Issued and awaiting an answer"
        subtitle="These are the ones to chase."
        actions={
          <Button
            tone="plain"
            size="compact"
            onClick={() => onOpen("quotations")}
          >
            All quotations
          </Button>
        }
        flush
      >
        {issued.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (issued.data?.items ?? []).length === 0 ? (
          <EmptyState title="Nothing is out with a customer." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "15%" }}>Number</th>
                  <th>Customer</th>
                  <th style={{ width: "14%" }}>Quoted</th>
                  <th style={{ width: "14%" }}>Valid until</th>
                  <th className="numeric" style={{ width: "18%" }}>
                    Total payable
                  </th>
                  <th style={{ width: "12%" }}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {(issued.data?.items ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.number}
                      {item.revision > 1 ? (
                        <span className="cell-note">
                          Revision {item.revision}
                        </span>
                      ) : null}
                    </td>
                    <td className="table__cell--wrap">
                      {item.customer_name ?? "—"}
                    </td>
                    <td>{formatDate(item.quoted_on)}</td>
                    <td>
                      {item.valid_until
                        ? formatDate(item.valid_until)
                        : "No expiry"}
                    </td>
                    <td className="numeric">
                      {money(item.total_payable, currency)}
                    </td>
                    <td>
                      <StatusChip tone={STATE_TONE[item.state] ?? "neutral"}>
                        {humanise(item.state)}
                      </StatusChip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Where quotations stand"
        subtitle="Every quotation on the system, by state."
      >
        {Object.entries(counts).length === 0 ? (
          <EmptyState title="No quotation has been raised yet." />
        ) : (
          <div className="row gap-3">
            {Object.entries(counts).map(([state, count]) => (
              <StatusChip key={state} tone={STATE_TONE[state] ?? "neutral"}>
                {humanise(state)}: {count}
              </StatusChip>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
