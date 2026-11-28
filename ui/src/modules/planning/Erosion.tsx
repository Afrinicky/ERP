/** Quoted margin against realised margin, cause by cause. */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Alert, Card, EmptyState, Measure, Skeleton, StatusChip } from "@/components/primitives";
import { describe } from "@/lib/errors";
import { money } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { useWindow } from "./shared";

const ORDER = ["price", "discount", "supplier_cost", "freight", "tax", "returns", "commission"];

export function Erosion() {
  const currency = useCurrency();
  const { range, picker } = useWindow("year");
  const report = useQuery({
    queryKey: ["analytics", "erosion", range.start, range.end],
    queryFn: () => api.analytics.erosion(range.start, range.end),
  });
  const data = report.data;
  const active = ORDER.filter((cause) =>
    (data?.rows ?? []).some((row) => Number(row.causes[cause] ?? '0') !== 0),
  );

  return (
    <div className="stack gap-5">
      <div className="command__bar">{picker}</div>
      {report.isError ? <Alert tone="error" title={describe(report.error).message} /> : null}
      {report.isPending ? (
        <Skeleton rows={4} height={80} />
      ) : !data || data.rows.length === 0 ? (
        <EmptyState title="No quoted order has been invoiced in this period." />
      ) : (
        <>
          <div className="measure-grid">
            <Measure
              label="Margin quoted"
              value={money(data.quoted_margin, currency)}
              footer={<StatusChip tone="neutral">{data.bases.quoted}</StatusChip>}
            />
            <Measure
              label="Margin realised"
              value={money(data.realised_margin, currency)}
              footer={<StatusChip tone="info">{data.bases.realised}</StatusChip>}
            />
            {ORDER.filter((cause) => data.totals[cause] && Number(data.totals[cause]) !== 0)
              .slice(0, 2)
              .map((cause) => (
                <Measure
                  key={cause}
                  label={data.cause_labels[cause] ?? cause}
                  value={money(data.totals[cause] ?? "0", currency)}
                  period="Effect on margin"
                />
              ))}
          </div>
          {data.pending > 0 ? (
            <Alert tone="info" title={`${data.pending} order lines await their actual cost.`}>
              Their realised margin uses the quoted cost until the actual is recorded, and they are
              marked below.
            </Alert>
          ) : null}
          <Card
            title="By cause"
            subtitle="Negative figures cost margin. The causes sum to the difference."
            flush
          >
            <table className="table">
              <tbody>
                {ORDER.map((cause) => (
                  <tr key={cause}>
                    <td>{data.cause_labels[cause]}</td>
                    <td className="numeric">{money(data.totals[cause] ?? "0", currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card
            title="By order line"
            subtitle="Only the causes that moved margin in this period are shown as columns."
            flush
          >
            <div className="table-frame">
              <table className="table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Item</th>
                    <th className="numeric">Quoted</th>
                    <th className="numeric">Realised</th>
                    {active.map((cause) => (
                      <th key={cause} className="numeric">
                        {data.cause_labels[cause]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.order_line_id}>
                      <td className="nowrap">
                        {row.order_number}
                        <div className="cell-note">{row.customer}</div>
                      </td>
                      <td className="table__cell--wrap">
                        {row.description}
                        <div className="cell-note">
                          {row.supplier}
                          {row.complete ? "" : " · awaiting actual cost"}
                        </div>
                      </td>
                      <td className="numeric">{money(row.quoted_margin, currency)}</td>
                      <td className="numeric">{money(row.realised_margin, currency)}</td>
                      {active.map((cause) => (
                        <td key={cause} className="numeric">
                          {Number(row.causes[cause] ?? "0") === 0
                            ? "—"
                            : money(row.causes[cause] ?? "0", currency)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
