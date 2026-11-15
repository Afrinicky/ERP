/**
 * Sales orders: what the customer agreed to, where each line stands, and the purchase
 * orders serving it.
 *
 * The position is read from the lines — ordered, procured, received — and never set by
 * hand. The cost variance sits in its own card behind the costing permission, because
 * a sales officer reads the order and the proprietor reads what it is costing.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { SalesOrder, SalesOrderLine } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Details,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, humanise, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

type Tone = "neutral" | "info" | "ok" | "warning" | "error";

export const ORDER_TONE: Record<SalesOrder["state"], Tone> = {
  open: "info",
  partially_fulfilled: "warning",
  fulfilled: "ok",
  cancelled: "neutral",
};

const LINE_TONE: Record<SalesOrderLine["state"], Tone> = {
  open: "info",
  backordered: "warning",
  fulfilled: "ok",
  cancelled: "neutral",
};

export function Orders({
  canManage,
  canSeeCosting,
  focusId,
}: {
  canManage: boolean;
  canSeeCosting: boolean;
  focusId?: string | null;
}) {
  const currency = useCurrency();
  const [state, setState] = useState("");
  const [openId, setOpenId] = useState<string | null>(focusId ?? null);
  const [cancelling, setCancelling] = useState<SalesOrder | null>(null);

  const orders = useQuery({
    queryKey: ["sales", "orders", state],
    queryFn: () => api.orders.list(state || undefined, 50),
  });

  const open = (orders.data ?? []).find((item) => item.id === openId) ?? null;

  return (
    <div className="stack gap-5">
      <Card
        title={`${orders.data?.length ?? 0} orders`}
        subtitle="Converted from accepted quotations. The position is read from the lines."
        actions={
          <Select
            aria-label="Filter by state"
            value={state}
            onChange={(event) => setState(event.target.value)}
          >
            <option value="">Every state</option>
            <option value="open">Open</option>
            <option value="partially_fulfilled">Partially fulfilled</option>
            <option value="fulfilled">Fulfilled</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        }
        flush
      >
        {orders.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (orders.data ?? []).length === 0 ? (
          <EmptyState
            title="No orders yet."
            detail="An order appears here when an accepted quotation is converted."
          />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "14%" }}>Number</th>
                  <th>Customer</th>
                  <th style={{ width: "11%" }}>Ordered</th>
                  <th style={{ width: "11%" }}>Required by</th>
                  <th style={{ width: "14%" }} className="numeric">
                    Total
                  </th>
                  <th style={{ width: "12%" }}>Progress</th>
                  <th style={{ width: "13%" }}>Standing</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(orders.data ?? []).map((item) => (
                  <tr key={item.id} className={item.id === openId ? "table__row--on" : undefined}>
                    <td className="numeric">
                      {item.number}
                      {item.customer_reference ? (
                        <span className="cell-note">Their ref {item.customer_reference}</span>
                      ) : null}
                    </td>
                    <td>{item.customer_name ?? "—"}</td>
                    <td>{formatDate(item.ordered_on)}</td>
                    <td>{item.required_by ? formatDate(item.required_by) : "—"}</td>
                    <td className="numeric">{money(item.total_payable, currency)}</td>
                    <td>
                      <Progress order={item} />
                    </td>
                    <td>
                      <StatusChip tone={ORDER_TONE[item.state]}>{humanise(item.state)}</StatusChip>
                    </td>
                    <td className="table__actions">
                      <Button
                        tone="plain"
                        size="compact"
                        onClick={() => setOpenId(item.id === openId ? null : item.id)}
                      >
                        {item.id === openId ? "Close" : "Open"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {open ? (
        <OrderDetail
          order={open}
          canManage={canManage}
          canSeeCosting={canSeeCosting}
          onCancel={() => setCancelling(open)}
        />
      ) : null}

      {cancelling ? (
        <CancelDialog order={cancelling} onClose={() => setCancelling(null)} />
      ) : null}
    </div>
  );
}

/** Units received against units ordered, as a short bar with the figures beside it. */
function Progress({ order }: { order: SalesOrder }) {
  const ordered = order.lines.reduce(
    (sum, line) => sum + Number(line.quantity_ordered) - Number(line.quantity_cancelled),
    0,
  );
  const received = order.lines.reduce((sum, line) => sum + Number(line.quantity_received), 0);
  const share = ordered > 0 ? Math.min(100, Math.round((received / ordered) * 100)) : 0;
  return (
    <div className="progress" title={`${received} of ${ordered} units received`}>
      <div className="progress__track">
        <div className="progress__bar" style={{ width: `${share}%` }} />
      </div>
      <span className="caption">{share}%</span>
    </div>
  );
}

function OrderDetail({
  order,
  canManage,
  canSeeCosting,
  onCancel,
}: {
  order: SalesOrder;
  canManage: boolean;
  canSeeCosting: boolean;
  onCancel: () => void;
}) {
  const currency = useCurrency();
  const live = order.state !== "cancelled" && order.state !== "fulfilled";
  return (
    <div className="stack gap-5">
      <Card
        title={order.number}
        subtitle={`${order.customer_name ?? "Customer"} · ${humanise(order.payment_plan)} · ${
          order.purchase_order_numbers.length
            ? `served by ${order.purchase_order_numbers.join(", ")}`
            : "no purchase order raised yet"
        }`}
        actions={
          canManage && live ? (
            <Button tone="plain" size="compact" onClick={onCancel}>
              Cancel order
            </Button>
          ) : undefined
        }
        flush
      >
        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: "4%" }}>#</th>
                <th>Item</th>
                <th style={{ width: "9%" }}>Source</th>
                <th style={{ width: "9%" }} className="numeric">
                  Ordered
                </th>
                <th style={{ width: "9%" }} className="numeric">
                  Procured
                </th>
                <th style={{ width: "9%" }} className="numeric">
                  Received
                </th>
                <th style={{ width: "12%" }} className="numeric">
                  Unit price
                </th>
                <th style={{ width: "12%" }} className="numeric">
                  Amount
                </th>
                <th style={{ width: "12%" }}>Standing</th>
              </tr>
            </thead>
            <tbody>
              {order.lines.map((line) => (
                <tr key={line.id}>
                  <td className="numeric">{line.position}</td>
                  <td>
                    {line.description}
                    {line.specification ? (
                      <span className="cell-note">{line.specification}</span>
                    ) : null}
                    {line.substitution_reason ? (
                      <span className="cell-note">Substituted: {line.substitution_reason}</span>
                    ) : null}
                  </td>
                  <td className="caption">{humanise(line.source_kind)}</td>
                  <td className="numeric">
                    {quantity(line.quantity_ordered)}
                    {Number(line.quantity_cancelled) > 0 ? (
                      <span className="cell-note">−{quantity(line.quantity_cancelled)} cancelled</span>
                    ) : null}
                  </td>
                  <td className="numeric">{quantity(line.quantity_procured)}</td>
                  <td className="numeric">{quantity(line.quantity_received)}</td>
                  <td className="numeric">{money(line.unit_price, currency)}</td>
                  <td className="numeric">{money(line.line_total, currency)}</td>
                  <td>
                    <StatusChip tone={LINE_TONE[line.state]}>{humanise(line.state)}</StatusChip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card__foot">
          <Details
            rows={[
              ["Net", money(order.net_total, currency)],
              ["Tax", money(order.tax_total, currency)],
              ["Total payable", money(order.total_payable, currency)],
            ]}
          />
        </div>
      </Card>

      {canSeeCosting ? <CostVariance orderId={order.id} /> : null}
    </div>
  );
}

function CostVariance({ orderId }: { orderId: string }) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ["sales", "orders", orderId, "variance"],
    queryFn: () => api.orders.costVariance(orderId),
  });
  if (report.isPending) {
    return (
      <Card title="Cost variance">
        <Skeleton rows={3} />
      </Card>
    );
  }
  if (!report.data) return null;
  const data = report.data;
  const known = data.lines_known === data.lines.length;
  return (
    <Card
      title="Cost variance"
      subtitle={
        known
          ? "Quoted landed cost against what procurement actually cost. Internal."
          : `Known for ${data.lines_known} of ${data.lines.length} lines; the rest await the supplier's invoice. Internal.`
      }
      flush
    >
      <div className="table-frame">
        <table className="table table--compact">
          <thead>
            <tr>
              <th style={{ width: "4%" }}>#</th>
              <th>Item</th>
              <th className="numeric">Quantity</th>
              <th className="numeric">Expected / unit</th>
              <th className="numeric">Actual / unit</th>
              <th className="numeric">Difference / unit</th>
              <th className="numeric">Difference</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line) => (
              <tr key={line.line_id}>
                <td className="numeric">{line.position}</td>
                <td>{line.description}</td>
                <td className="numeric">{quantity(line.quantity)}</td>
                <td className="numeric">{money(line.expected_unit_cost, currency)}</td>
                <td className="numeric">
                  {line.actual_unit_cost ? money(line.actual_unit_cost, currency) : "—"}
                </td>
                <td className="numeric">{line.per_unit ? money(line.per_unit, currency) : "—"}</td>
                <td className="numeric">{line.total ? money(line.total, currency) : "—"}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>Order</td>
              <td className="numeric">{money(data.expected_total, currency)}</td>
              <td className="numeric">
                {data.actual_total ? money(data.actual_total, currency) : "—"}
              </td>
              <td />
              <td className="numeric">
                {data.variance_total ? money(data.variance_total, currency) : "—"}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}

function CancelDialog({ order, onClose }: { order: SalesOrder; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const cancel = useMutation({
    mutationFn: () => api.orders.cancel(order.id, reason),
    onSuccess: () => {
      toast.show("success", `${order.number} cancelled.`);
      void client.invalidateQueries({ queryKey: ["sales"] });
      onClose();
    },
  });
  return (
    <Dialog
      size="sm"
      title={`Cancel ${order.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Keep the order
          </Button>
          <Button
            tone="destructive"
            onClick={() => cancel.mutate()}
            busy={cancel.isPending}
            disabled={!reason.trim()}
          >
            Cancel the order
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {cancel.isError ? <Alert tone="error" title={describe(cancel.error).message} /> : null}
        <Alert tone="warning" title="Outstanding quantity on every line is cancelled.">
          An order that has received goods is cancelled line by line instead.
        </Alert>
        <Field label="Reason" required hint="Recorded in the audit trail.">
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
