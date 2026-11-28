/** Batches approaching or past their expiry date, earliest first. */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ExpiryStatusName } from "@/api/types";
import { Card, EmptyState, Skeleton, StatusChip, type Tone } from "@/components/primitives";
import { date as formatDate, quantity } from "@/lib/format";

const STATUS: Record<ExpiryStatusName, [string, Tone]> = {
  none: ["No date", "neutral"],
  good: ["In date", "ok"],
  near: ["Expiring", "warning"],
  expired: ["Expired", "error"],
};

export function Expiry() {
  const batches = useQuery({
    queryKey: ["inventory", "expiry", "default"],
    queryFn: () => api.inventory.expiry(),
  });
  const settings = useQuery({
    queryKey: ["inventory", "settings"],
    queryFn: () => api.inventory.settings(),
  });

  return (
    <Card
      title="Expiry watch"
      subtitle={
        settings.data
          ? `Batches expiring within ${settings.data.expiry_warning_days} days, and those already expired.`
          : undefined
      }
      flush
    >
      {batches.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={4} />
        </div>
      ) : (batches.data ?? []).length === 0 ? (
        <EmptyState title="No batch is nearing expiry." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Item</th>
                <th style={{ width: "14%" }}>Batch</th>
                <th style={{ width: "10%" }}>Warehouse</th>
                <th style={{ width: "13%" }}>Expires</th>
                <th style={{ width: "10%" }} className="numeric">
                  Held
                </th>
                <th style={{ width: "14%" }}>Standing</th>
              </tr>
            </thead>
            <tbody>
              {(batches.data ?? []).map((row) => (
                <tr key={`${row.batch_id}-${row.warehouse}`}>
                  <td>
                    <span className="cell-strong">{row.name}</span>
                    <span className="cell-note">{row.sku}</span>
                  </td>
                  <td>{row.batch}</td>
                  <td>{row.warehouse}</td>
                  <td>
                    {formatDate(row.expires_on)}
                    {row.days !== null ? (
                      <span className="cell-note">
                        {row.days < 0
                          ? `${-row.days} days ago`
                          : row.days === 0
                            ? "Today"
                            : `In ${row.days} days`}
                      </span>
                    ) : null}
                  </td>
                  <td className="numeric">{quantity(row.quantity)}</td>
                  <td>
                    <StatusChip tone={STATUS[row.status][1]}>{STATUS[row.status][0]}</StatusChip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
