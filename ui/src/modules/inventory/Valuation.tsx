/**
 * Valuation: what the cost layers say stock is worth, against what the inventory
 * control accounts say. They agree by construction; this view proves it.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import {
  Alert,
  Card,
  EmptyState,
  Skeleton,
  StatusChip,
  TotalDisplay,
} from "@/components/primitives";
import { money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { StateChip } from "./shared";

export function Valuation() {
  const currency = useCurrency();
  const valuation = useQuery({
    queryKey: ["inventory", "valuation"],
    queryFn: () => api.inventory.valuation(),
  });
  const ledger = useQuery({
    queryKey: ["inventory", "reconciliation"],
    queryFn: () => api.inventory.reconciliation(),
  });
  const settings = useQuery({
    queryKey: ["inventory", "settings"],
    queryFn: () => api.inventory.settings(),
  });

  if (valuation.isPending) return <Skeleton rows={6} />;
  const data = valuation.data;

  return (
    <div className="stack gap-5">
      <div className="measure-row">
        <TotalDisplay label="Stock at cost" value={money(data?.total ?? "0", currency)} compact />
        {(data?.accounts ?? []).map((account) => (
          <TotalDisplay
            key={account.account}
            label={`${account.account} ${account.name}`}
            value={money(account.ledger, currency)}
            caption={
              account.agrees
                ? "Agrees with the layers"
                : `Differs by ${money(account.difference, currency)}`
            }
            compact
          />
        ))}
      </div>

      {data && !data.agrees ? (
        <Alert tone="error" title="The cost layers and the inventory accounts do not agree." />
      ) : null}
      {ledger.data && !ledger.data.agrees ? (
        <Alert
          tone="error"
          title={`${ledger.data.discrepancies.length} balances differ from the stock ledger.`}
        />
      ) : null}

      <Card
        title="By item, store and state"
        subtitle={
          settings.data
            ? `Valued at ${settings.data.valuation_policy === "fifo" ? "first in, first out" : "moving weighted average"} unless an item says otherwise.`
            : undefined
        }
        actions={
          ledger.data ? (
            <StatusChip tone={ledger.data.agrees ? "ok" : "error"}>
              {ledger.data.agrees
                ? `Ledger reconciled · ${ledger.data.movements} movements`
                : "Ledger does not reconcile"}
            </StatusChip>
          ) : undefined
        }
        flush
      >
        {(data?.rows ?? []).length === 0 ? (
          <EmptyState title="Nothing is held at cost." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: "10%" }}>Warehouse</th>
                  <th style={{ width: "13%" }}>State</th>
                  <th style={{ width: "10%" }}>Account</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Quantity
                  </th>
                  <th style={{ width: "13%" }} className="numeric">
                    Unit cost
                  </th>
                  <th style={{ width: "14%" }} className="numeric">
                    Value
                  </th>
                </tr>
              </thead>
              <tbody>
                {(data?.rows ?? []).map((row) => (
                  <tr key={`${row.product_id}-${row.warehouse}-${row.state}`}>
                    <td>
                      <span className="cell-strong">{row.name}</span>
                      <span className="cell-note">{row.sku}</span>
                    </td>
                    <td>{row.warehouse}</td>
                    <td>
                      <StateChip state={row.state} />
                    </td>
                    <td className="numeric">{row.account}</td>
                    <td className="numeric">{quantity(row.quantity)}</td>
                    <td className="numeric">{money(row.unit_cost, currency)}</td>
                    <td className="numeric">{money(row.value, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
