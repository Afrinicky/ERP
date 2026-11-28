/**
 * Supplier offers against one request, compared on what they will actually cost.
 *
 * The comparison leads on landed cost, not on unit price. A supplier quoting 9,300
 * with heavy freight is dearer than one quoting 9,800 without it, and a table sorted
 * on the headline figure would recommend the wrong one.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import {
  Card,
  EmptyState,
  Select,
  Skeleton,
  StatusChip,
} from "@/components/primitives";
import { date as formatDate, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export function Sourcing({
  requestId,
  canManage,
  onSelect,
}: {
  requestId: string | null;
  canManage: boolean;
  onSelect: (requestId: string) => void;
}) {
  const currency = useCurrency();
  const requests = useQuery({
    queryKey: ["sales", "requests", "sourceable"],
    queryFn: () => api.sourcing.requests({ limit: 100 }),
  });

  const [selected, setSelected] = useState(requestId ?? "");
  useEffect(() => {
    if (!selected && (requests.data?.items ?? []).length > 0) {
      setSelected(requests.data!.items[0]!.id);
    }
  }, [requests.data, selected]);
  useEffect(() => {
    if (selected) onSelect(selected);
  }, [selected, onSelect]);

  const comparison = useQuery({
    queryKey: ["sales", "comparison", selected],
    queryFn: () => api.sourcing.comparison(selected),
    enabled: Boolean(selected),
  });
  const quotations = useQuery({
    queryKey: ["sales", "supplier-quotations", selected],
    queryFn: () => api.sourcing.supplierQuotations(selected),
    enabled: Boolean(selected),
  });

  const options = (requests.data?.items ?? []).map((item) => ({
    value: item.id,
    label: `${item.number} — ${item.customer_name ?? "Customer"}`,
  }));

  return (
    <div className="stack gap-5">
      <Card
        title="Supplier comparison"
        subtitle="Offers converted to base currency, with each supplier’s own freight apportioned over their lines."
        actions={
          options.length > 0 ? (
            <Select
              aria-label="Request to source against"
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          ) : null
        }
      >
        {options.length === 0 ? (
          <EmptyState
            title="No request to source against."
            detail="Record a customer request first; supplier offers are compared against its lines."
          />
        ) : comparison.isPending ? (
          <Skeleton rows={5} />
        ) : (comparison.data ?? []).every(
            (line) => line.offers.length === 0,
          ) ? (
          <EmptyState
            title="No supplier has answered yet."
            detail={
              canManage
                ? "Record a supplier quotation against this request and the comparison will build itself."
                : "Supplier offers appear here once they are recorded."
            }
          />
        ) : (
          <div className="stack gap-5">
            {(comparison.data ?? []).map((line) => (
              <div key={line.request_line_id} className="stack gap-2">
                <div className="row gap-3">
                  <strong className="grow">{line.description}</strong>
                  <span className="muted">
                    {quantity(line.quantity)} required
                  </span>
                </div>
                {line.offers.length === 0 ? (
                  <p className="muted">
                    No supplier was asked about this line.
                  </p>
                ) : (
                  <div className="table-frame">
                    <table className="table table--compact">
                      <thead>
                        <tr>
                          <th>Supplier</th>
                          <th className="numeric" style={{ width: "13%" }}>
                            Unit
                          </th>
                          <th className="numeric" style={{ width: "14%" }}>
                            Freight a unit
                          </th>
                          <th className="numeric" style={{ width: "15%" }}>
                            Landed unit cost
                          </th>
                          <th style={{ width: "10%" }}>Lead time</th>
                          <th style={{ width: "12%" }}>Valid until</th>
                          <th style={{ width: "13%" }}>Standing</th>
                        </tr>
                      </thead>
                      <tbody>
                        {line.offers.map((offer) => (
                          <tr
                            key={offer.supplier_quotation_line_id}
                            data-selected={
                              offer.supplier_quotation_line_id ===
                              line.best_offer_id
                                ? "true"
                                : undefined
                            }
                          >
                            <td className="table__cell--wrap">
                              {offer.supplier_name}
                              <span className="cell-note">
                                {offer.reference}
                                {offer.currency_code !== currency.code
                                  ? ` · quoted in ${offer.currency_code}`
                                  : ""}
                              </span>
                            </td>
                            <td className="numeric">
                              {money(offer.net_unit_cost, currency)}
                            </td>
                            <td className="numeric">
                              {money(offer.charges_per_unit, currency)}
                            </td>
                            <td className="numeric">
                              <strong>
                                {money(offer.comparable_unit_cost, currency)}
                              </strong>
                            </td>
                            <td>
                              {offer.lead_time_days === null
                                ? "—"
                                : `${offer.lead_time_days} days`}
                            </td>
                            <td>
                              {offer.valid_until
                                ? formatDate(offer.valid_until)
                                : "—"}
                            </td>
                            <td>
                              {!offer.is_available ? (
                                <StatusChip tone="error">
                                  Cannot supply
                                </StatusChip>
                              ) : !offer.meets_quantity ? (
                                <StatusChip tone="warning">
                                  Below minimum order
                                </StatusChip>
                              ) : offer.supplier_quotation_line_id ===
                                line.best_offer_id ? (
                                <StatusChip tone="ok">Lowest landed</StatusChip>
                              ) : (
                                <StatusChip tone="neutral">
                                  Available
                                </StatusChip>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Offers on file"
        subtitle="Each supplier’s quotation as they sent it."
        flush
      >
        {(quotations.data ?? []).length === 0 ? (
          <EmptyState title="No supplier quotation has been recorded against this request." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th style={{ width: "16%" }}>Their reference</th>
                  <th style={{ width: "12%" }}>Received</th>
                  <th className="numeric" style={{ width: "14%" }}>
                    Freight
                  </th>
                  <th className="numeric" style={{ width: "14%" }}>
                    Other charges
                  </th>
                  <th className="numeric" style={{ width: "8%" }}>
                    Lines
                  </th>
                </tr>
              </thead>
              <tbody>
                {(quotations.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td className="table__cell--wrap">
                      {item.supplier_name ?? "—"}
                      {item.payment_terms ? (
                        <span className="cell-note">{item.payment_terms}</span>
                      ) : null}
                    </td>
                    <td>{item.reference}</td>
                    <td>{formatDate(item.received_on)}</td>
                    <td className="numeric">
                      {money(item.freight_amount, currency)}
                    </td>
                    <td className="numeric">
                      {money(item.other_charges, currency)}
                    </td>
                    <td className="numeric">{item.lines.length}</td>
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
