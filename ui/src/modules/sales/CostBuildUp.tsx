/**
 * The internal cost build-up: every step of the canonical sequence, separately shown.
 *
 * A figure the user cannot decompose is a figure they cannot defend to a customer,
 * so nothing here is merged into anything else. The subtotal rows — landed cost, list
 * price, payable before tax — are marked, because those are the three a reader checks.
 */

import type { Costing } from "@/api/types";
import { Alert, Details, StatusChip } from "@/components/primitives";
import { humanise, money, percent, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export function CostBuildUp({ costing }: { costing: Costing }) {
  const currency = useCurrency();

  return (
    <div className="stack gap-5">
      {costing.requires_approval ? (
        <Alert
          tone="warning"
          title="This quotation needs approval before it can be issued"
        >
          {costing.approval_reason}
        </Alert>
      ) : null}
      {costing.tax_review.length > 0 ? (
        <Alert tone="warning" title="Some tax decisions are unresolved">
          {costing.tax_review.join(" · ")}
        </Alert>
      ) : null}

      <Details
        rows={[
          ["Landed cost", money(costing.landed_cost_total, currency)],
          ["Margin", money(costing.margin_total, currency)],
          ["Affiliate commission", money(costing.commission_total, currency)],
          ["List price", money(costing.list_total, currency)],
          ["Discount", money(costing.discount_total, currency)],
          ["Net price", money(costing.net_total, currency)],
          ["Finance charge", money(costing.finance_charge_total, currency)],
          ["Tax", money(costing.tax_total, currency)],
          ["Total payable", money(costing.total_payable, currency)],
          [
            "Contribution before commission",
            money(costing.contribution_before_commission, currency),
          ],
          [
            "Contribution after commission",
            money(costing.contribution_after_commission, currency),
          ],
        ]}
      />

      {costing.lines.map((line) => (
        <div key={line.position} className="stack gap-3">
          <div className="row gap-3">
            <strong className="grow">
              {line.position}. {line.description}
            </strong>
            <span className="muted">
              {humanise(line.margin_method)} at {percent(line.margin_rate)}
              {line.margin_source === "not configured"
                ? " · no policy set"
                : ` · set on the ${line.margin_source}`}
            </span>
          </div>

          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "10%" }}>Step</th>
                  <th>Component</th>
                  <th className="numeric" style={{ width: "24%" }}>
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {line.steps.map((step, index) => (
                  <tr
                    key={index}
                    data-selected={step.step === "—" ? "true" : undefined}
                  >
                    <td>{step.step === "—" ? "" : step.step}</td>
                    <td className="table__cell--wrap">{step.label}</td>
                    <td className="numeric">{money(step.amount, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="row gap-3">
            <StatusChip tone={line.commission_in_scope ? "accent" : "neutral"}>
              {line.commission_in_scope
                ? `Commission ${money(line.commission, currency)}`
                : "No commission"}
            </StatusChip>
            <span className="muted grow">{line.commission_reason}</span>
            {line.actual_cost !== "0.0000" ? (
              <StatusChip
                tone={Number(line.variance_rate) > 0 ? "error" : "ok"}
              >
                Variance {money(line.variance, currency)} (
                {percent(line.variance_rate)})
              </StatusChip>
            ) : null}
          </div>

          {line.sources.length > 0 ? (
            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th style={{ width: "14%" }}>Source</th>
                    <th>Supplier or warehouse</th>
                    <th className="numeric" style={{ width: "16%" }}>
                      Quantity
                    </th>
                    <th className="numeric" style={{ width: "20%" }}>
                      Unit cost
                    </th>
                    <th style={{ width: "14%" }}>Lead time</th>
                  </tr>
                </thead>
                <tbody>
                  {line.sources.map((source) => (
                    <tr key={source.id}>
                      <td>{humanise(source.kind)}</td>
                      <td className="table__cell--wrap">
                        {source.supplier_name ?? "Own stock"}
                        {source.selection_reason ? (
                          <span className="cell-note">
                            {source.selection_reason}
                          </span>
                        ) : null}
                      </td>
                      <td className="numeric">{quantity(source.quantity)}</td>
                      <td className="numeric">
                        {money(source.unit_cost, currency)}
                      </td>
                      <td>
                        {source.lead_time_days === null
                          ? "—"
                          : `${source.lead_time_days} days`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ))}

      {costing.allocations.length > 0 ? (
        <div className="stack gap-2">
          <span className="column-header">Allocated costs</span>
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th className="numeric" style={{ width: "10%" }}>
                    Line
                  </th>
                  <th>Expense</th>
                  <th style={{ width: "22%" }}>Apportioned</th>
                  <th className="numeric" style={{ width: "24%" }}>
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {costing.allocations.map((row, index) => (
                  <tr key={index}>
                    <td className="numeric">{row.line_position}</td>
                    <td>{humanise(row.category)}</td>
                    <td>{humanise(row.method)}</td>
                    <td className="numeric">{money(row.amount, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
