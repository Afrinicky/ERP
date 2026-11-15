/**
 * Purchase orders: raised from a sales order's chosen sources or for replenishment,
 * approved, issued, received against.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  PurchaseLineInput,
  PurchaseOrder,
  SalesOrder,
  SuggestedOrder,
} from "@/api/types";
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
  Stepper,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, humanise, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

type Tone = "neutral" | "info" | "ok" | "warning" | "error";

export const PURCHASE_TONE: Record<PurchaseOrder["state"], Tone> = {
  draft: "neutral",
  approved: "info",
  issued: "info",
  partially_received: "warning",
  received: "ok",
  closed: "neutral",
  cancelled: "neutral",
};

export function PurchaseOrders({
  canManage,
  canApprove,
  canReceive,
  onReceive,
}: {
  canManage: boolean;
  canApprove: boolean;
  canReceive: boolean;
  onReceive: (orderId: string) => void;
}) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [raising, setRaising] = useState(false);
  const [cancelling, setCancelling] = useState<PurchaseOrder | null>(null);

  const orders = useQuery({
    queryKey: ["purchases", "orders", state],
    queryFn: () => api.purchases.orders({ state: state || undefined, limit: 50 }),
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "issue" | "close" }) =>
      action === "approve"
        ? api.purchases.approve(id)
        : action === "issue"
          ? api.purchases.issue(id)
          : api.purchases.close(id),
    onSuccess: (updated) => {
      toast.show("success", `${updated.number ?? "The order"} is now ${humanise(updated.state)}.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      void client.invalidateQueries({ queryKey: ["sales"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  const open = (orders.data ?? []).find((item) => item.id === openId) ?? null;

  return (
    <div className="stack gap-5">
      <Card
        title={`${orders.data?.length ?? 0} purchase orders`}
        subtitle="Numbered at issue, so a draft that is discarded leaves no gap."
        actions={
          <div className="row gap-2">
            <Select
              aria-label="Filter by state"
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="">Every state</option>
              <option value="draft">Draft</option>
              <option value="approved">Approved</option>
              <option value="issued">Issued</option>
              <option value="partially_received">Partially received</option>
              <option value="received">Received</option>
              <option value="closed">Closed</option>
              <option value="cancelled">Cancelled</option>
            </Select>
            {canManage ? (
              <Button tone="primary" onClick={() => setRaising(true)}>
                Raise an order
              </Button>
            ) : null}
          </div>
        }
        flush
      >
        {orders.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (orders.data ?? []).length === 0 ? (
          <EmptyState
            title="No purchase orders yet."
            detail="Raise one from an accepted sales order, or for replenishment."
          />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "13%" }}>Number</th>
                  <th>Supplier</th>
                  <th style={{ width: "12%" }}>For</th>
                  <th style={{ width: "10%" }}>Ordered</th>
                  <th style={{ width: "10%" }}>Expected</th>
                  <th style={{ width: "13%" }} className="numeric">
                    Total
                  </th>
                  <th style={{ width: "13%" }}>Standing</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(orders.data ?? []).map((item) => (
                  <tr key={item.id} className={item.id === openId ? "table__row--on" : undefined}>
                    <td className="numeric">{item.number ?? <span className="caption">Draft</span>}</td>
                    <td>{item.supplier_name ?? "—"}</td>
                    <td className="caption">
                      {item.sales_order_number ?? humanise(item.purpose)}
                    </td>
                    <td>{formatDate(item.ordered_on)}</td>
                    <td>{item.expected_on ? formatDate(item.expected_on) : "—"}</td>
                    <td className="numeric">{money(item.total, currency)}</td>
                    <td>
                      <StatusChip tone={PURCHASE_TONE[item.state]}>{humanise(item.state)}</StatusChip>
                    </td>
                    <td className="table__actions">
                      {canApprove && item.state === "draft" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => act.mutate({ id: item.id, action: "approve" })}
                        >
                          Approve
                        </Button>
                      ) : null}
                      {canApprove && item.state === "approved" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => act.mutate({ id: item.id, action: "issue" })}
                        >
                          Issue
                        </Button>
                      ) : null}
                      {canReceive && (item.state === "issued" || item.state === "partially_received") ? (
                        <Button tone="plain" size="compact" onClick={() => onReceive(item.id)}>
                          Receive
                        </Button>
                      ) : null}
                      {canManage && item.state === "partially_received" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => act.mutate({ id: item.id, action: "close" })}
                        >
                          Close short
                        </Button>
                      ) : null}
                      {canManage && ["draft", "approved", "issued"].includes(item.state) ? (
                        <Button tone="plain" size="compact" onClick={() => setCancelling(item)}>
                          Cancel
                        </Button>
                      ) : null}
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

      {open ? <OrderLines order={open} /> : null}
      {raising ? <RaiseDialog onClose={() => setRaising(false)} /> : null}
      {cancelling ? (
        <CancelDialog order={cancelling} onClose={() => setCancelling(null)} />
      ) : null}
    </div>
  );
}

function OrderLines({ order }: { order: PurchaseOrder }) {
  const currency = useCurrency();
  return (
    <Card
      title={order.number ?? "Draft purchase order"}
      subtitle={`${order.supplier_name ?? "Supplier"} · ${order.currency_code}${
        order.supplier_reference ? ` · their ref ${order.supplier_reference}` : ""
      }`}
      flush
    >
      <div className="table-frame">
        <table className="table table--compact">
          <thead>
            <tr>
              <th style={{ width: "4%" }}>#</th>
              <th>Item</th>
              <th className="numeric">Ordered</th>
              <th className="numeric">Received</th>
              <th className="numeric">Accepted</th>
              <th className="numeric">Invoiced</th>
              <th className="numeric">Unit cost</th>
              <th className="numeric">Amount</th>
            </tr>
          </thead>
          <tbody>
            {order.lines.map((line) => (
              <tr key={line.id}>
                <td className="numeric">{line.position}</td>
                <td>{line.description}</td>
                <td className="numeric">{quantity(line.quantity_ordered)}</td>
                <td className="numeric">{quantity(line.quantity_received)}</td>
                <td className="numeric">
                  {quantity(line.quantity_accepted)}
                  {Number(line.quantity_rejected) > 0 ? (
                    <span className="cell-note">{quantity(line.quantity_rejected)} rejected</span>
                  ) : null}
                </td>
                <td className="numeric">{quantity(line.quantity_invoiced)}</td>
                <td className="numeric">{money(line.net_unit_cost, currency)}</td>
                <td className="numeric">{money(line.line_total, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card__foot">
        <Details
          rows={[
            ["Net", money(order.net_total, currency)],
            ["Total", money(order.total, currency)],
            ["Expected", order.expected_on ? formatDate(order.expected_on) : "Not stated"],
          ]}
        />
      </div>
    </Card>
  );
}

/**
 * Raising an order: from a sales order's chosen sources, one draft per supplier, or
 * typed for replenishment.
 */
function RaiseDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const currency = useCurrency();
  const [mode, setMode] = useState<"order" | "replenishment">("order");
  const [salesOrderId, setSalesOrderId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([
    { description: "", quantity: "1", unit_cost: "0" },
  ]);
  const [notes, setNotes] = useState("");

  const salesOrders = useQuery({
    queryKey: ["sales", "orders", "for-purchasing"],
    queryFn: () => api.orders.list(undefined, 100),
  });
  const suppliers = useQuery({
    queryKey: ["parties", "suppliers"],
    queryFn: () => api.parties.search({ kind: "supplier", limit: 200 }),
  });
  const suggestions = useQuery({
    queryKey: ["purchases", "suggest", salesOrderId],
    queryFn: () => api.purchases.suggest(salesOrderId),
    enabled: mode === "order" && Boolean(salesOrderId),
  });

  const raise = useMutation({
    mutationFn: async (payloads: SuggestedOrder[] | null) => {
      if (payloads) {
        const raised: PurchaseOrder[] = [];
        for (const suggestion of payloads) {
          raised.push(
            await api.purchases.raise({
              supplier_id: suggestion.supplier_id,
              sales_order_id: salesOrderId,
              supplier_quotation_id: suggestion.supplier_quotation_id,
              currency_code: suggestion.currency_code,
              exchange_rate: suggestion.exchange_rate,
              expected_on: suggestion.expected_on,
              lines: suggestion.lines,
            }),
          );
        }
        return raised;
      }
      return [
        await api.purchases.raise({
          supplier_id: supplierId,
          purpose: "replenishment",
          lines: lines.filter((line) => line.description.trim()),
          notes: notes || null,
        }),
      ];
    },
    onSuccess: (raised) => {
      toast.show(
        "success",
        raised.length === 1
          ? "One purchase order raised in draft."
          : `${raised.length} purchase orders raised in draft, one per supplier.`,
      );
      void client.invalidateQueries({ queryKey: ["purchases"] });
      onClose();
    },
  });

  const amend = (index: number, patch: Partial<PurchaseLineInput>) =>
    setLines((rows) => rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));

  const openOrders = (salesOrders.data ?? []).filter(
    (item: SalesOrder) => item.state === "open" || item.state === "partially_fulfilled",
  );
  const ready =
    mode === "order"
      ? Boolean(salesOrderId) && (suggestions.data ?? []).length > 0
      : Boolean(supplierId) && lines.some((line) => line.description.trim());

  return (
    <Dialog
      size="lg"
      title="Raise a purchase order"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => raise.mutate(mode === "order" ? (suggestions.data ?? []) : null)}
            busy={raise.isPending}
            disabled={!ready}
          >
            {mode === "order" && (suggestions.data ?? []).length > 1
              ? `Raise ${suggestions.data?.length} drafts`
              : "Raise in draft"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {raise.isError ? <Alert tone="error" title={describe(raise.error).message} /> : null}
        <Field label="Raise for">
          {(id) => (
            <Select
              id={id}
              value={mode}
              onChange={(event) => setMode(event.target.value as "order" | "replenishment")}
            >
              <option value="order">A customer order, from the sources the quotation chose</option>
              <option value="replenishment">Replenishment of the shelf</option>
            </Select>
          )}
        </Field>

        {mode === "order" ? (
          <>
            <Field label="Sales order" required hint="Only what is not yet procured is suggested.">
              {(id) => (
                <Select
                  id={id}
                  value={salesOrderId}
                  onChange={(event) => setSalesOrderId(event.target.value)}
                >
                  <option value="">Choose an order</option>
                  {openOrders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.number} · {item.customer_name ?? "Customer"} · {humanise(item.state)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {salesOrderId && suggestions.isPending ? <Skeleton rows={3} /> : null}
            {salesOrderId && suggestions.data && suggestions.data.length === 0 ? (
              <Alert tone="info" title="Nothing left to procure on this order." />
            ) : null}
            {(suggestions.data ?? []).map((suggestion) => (
              <Card key={suggestion.supplier_id} title={suggestion.supplier_name} flush>
                <div className="table-frame">
                  <table className="table table--compact">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="numeric">Quantity</th>
                        <th className="numeric">Unit cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {suggestion.lines.map((line, index) => (
                        <tr key={index}>
                          <td>{line.description}</td>
                          <td className="numeric">{quantity(line.quantity)}</td>
                          <td className="numeric">{money(line.unit_cost, currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            ))}
          </>
        ) : (
          <>
            <Field label="Supplier" required>
              {(id) => (
                <Select
                  id={id}
                  value={supplierId}
                  onChange={(event) => setSupplierId(event.target.value)}
                >
                  <option value="">Choose a supplier</option>
                  {(suppliers.data?.items ?? []).map((party) => (
                    <option key={party.id} value={party.id}>
                      {party.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <p className="caption caption--strong">Lines</p>
            {lines.map((line, index) => (
              <div className="form-grid" key={index}>
                <Field label={`Item ${index + 1}`}>
                  {(id) => (
                    <TextInput
                      id={id}
                      value={line.description}
                      onChange={(event) => amend(index, { description: event.target.value })}
                      placeholder="Reagent pack, 500 tests"
                    />
                  )}
                </Field>
                <Field label="Quantity">
                  {(id) => (
                    <Stepper
                      id={id}
                      size="standard"
                      min={1}
                      value={line.quantity}
                      onChange={(value) => amend(index, { quantity: value })}
                    />
                  )}
                </Field>
                <Field label="Unit cost">
                  {(id) => (
                    <TextInput
                      id={id}
                      numeric
                      inputMode="decimal"
                      value={line.unit_cost}
                      onChange={(event) => amend(index, { unit_cost: event.target.value })}
                    />
                  )}
                </Field>
              </div>
            ))}
            <div>
              <Button
                tone="plain"
                size="compact"
                onClick={() =>
                  setLines((rows) => [...rows, { description: "", quantity: "1", unit_cost: "0" }])
                }
              >
                Add a line
              </Button>
            </div>
            <Field label="Notes" optional>
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              )}
            </Field>
          </>
        )}
      </div>
    </Dialog>
  );
}

function CancelDialog({ order, onClose }: { order: PurchaseOrder; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const cancel = useMutation({
    mutationFn: () => api.purchases.cancelOrder(order.id, reason),
    onSuccess: () => {
      toast.show("success", `${order.number ?? "The draft"} cancelled.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      void client.invalidateQueries({ queryKey: ["sales"] });
      onClose();
    },
  });
  return (
    <Dialog
      size="sm"
      title={`Cancel ${order.number ?? "this draft"}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Keep it
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
