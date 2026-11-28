/**
 * Goods receipts: what arrived against an order, line by line.
 *
 * The receiving bench works by touch, so the quantities use the transactional
 * stepper and the rejected quantity sits beside the received one with its reason.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { GoodsReceipt, PurchaseOrder, ReceiptLineInput } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  StatusChip,
  Stepper,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export function Receipts({
  canReceive,
  focusOrderId,
}: {
  canReceive: boolean;
  focusOrderId: string | null;
}) {
  const currency = useCurrency();
  const [receivingId, setReceivingId] = useState<string | null>(focusOrderId);

  useEffect(() => setReceivingId(focusOrderId), [focusOrderId]);

  const receipts = useQuery({
    queryKey: ["purchases", "receipts"],
    queryFn: () => api.purchases.receipts({ limit: 50 }),
  });
  const receivable = useQuery({
    queryKey: ["purchases", "orders", "receivable"],
    queryFn: async () => {
      const [issued, partial] = await Promise.all([
        api.purchases.orders({ state: "issued", limit: 100 }),
        api.purchases.orders({ state: "partially_received", limit: 100 }),
      ]);
      return [...issued, ...partial];
    },
    enabled: canReceive,
  });

  const receiving = (receivable.data ?? []).find((item) => item.id === receivingId) ?? null;

  return (
    <div className="stack gap-5">
      {canReceive ? (
        <Card
          title="Receive against an order"
          subtitle="Issued orders with something still to arrive."
        >
          {receivable.isPending ? (
            <Skeleton rows={2} />
          ) : (receivable.data ?? []).length === 0 ? (
            <EmptyState title="Nothing is awaiting delivery." />
          ) : (
            <div className="row gap-3 wrap">
              {(receivable.data ?? []).map((item) => (
                <Button
                  key={item.id}
                  tone="secondary"
                  size="transactional"
                  onClick={() => setReceivingId(item.id)}
                >
                  {item.number} · {item.supplier_name}
                </Button>
              ))}
            </div>
          )}
        </Card>
      ) : null}

      <Card
        title={`${receipts.data?.length ?? 0} receipts`}
        subtitle="Posted as they are recorded: goods on the floor are goods in the ledger."
        flush
      >
        {receipts.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (receipts.data ?? []).length === 0 ? (
          <EmptyState title="Nothing has been received yet." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "14%" }}>Number</th>
                  <th style={{ width: "14%" }}>Order</th>
                  <th>Supplier</th>
                  <th style={{ width: "12%" }}>Received</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Lines
                  </th>
                  <th style={{ width: "14%" }} className="numeric">
                    Value accepted
                  </th>
                  <th style={{ width: "12%" }}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {(receipts.data ?? []).map((item: GoodsReceipt) => (
                  <tr key={item.id}>
                    <td className="numeric">{item.number ?? "—"}</td>
                    <td className="numeric">{item.purchase_order_number ?? "—"}</td>
                    <td>
                      {item.supplier_name ?? "—"}
                      {item.delivery_reference ? (
                        <span className="cell-note">Their note {item.delivery_reference}</span>
                      ) : null}
                    </td>
                    <td>{formatDate(item.received_on)}</td>
                    <td className="numeric">{item.lines.length}</td>
                    <td className="numeric">{money(item.accepted_value, currency)}</td>
                    <td>
                      <StatusChip tone={item.inspection_pending ? "warning" : "ok"}>
                        {item.inspection_pending ? "In quarantine" : "Posted"}
                      </StatusChip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {receiving ? <ReceiveDialog order={receiving} onClose={() => setReceivingId(null)} /> : null}
    </div>
  );
}

interface LineEntry extends ReceiptLineInput {
  outstanding: number;
}

function ReceiveDialog({ order, onClose }: { order: PurchaseOrder; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [deliveryReference, setDeliveryReference] = useState("");
  const [quarantine, setQuarantine] = useState(false);
  const [entries, setEntries] = useState<LineEntry[]>(() =>
    order.lines
      .map((line) => {
        const outstanding = Number(line.quantity_ordered) - Number(line.quantity_received);
        return {
          purchase_order_line_id: line.id,
          quantity_received: String(outstanding),
          quantity_rejected: "0",
          rejection_reason: "",
          batch: "",
          expiry: "",
          outstanding,
        };
      })
      .filter((entry) => entry.outstanding > 0),
  );

  const receive = useMutation({
    mutationFn: () =>
      api.purchases.receive({
        purchase_order_id: order.id,
        delivery_reference: deliveryReference || null,
        inspection_pending: quarantine,
        lines: entries
          .filter((entry) => Number(entry.quantity_received) > 0)
          .map((entry) => ({
            purchase_order_line_id: entry.purchase_order_line_id,
            quantity_received: entry.quantity_received,
            quantity_rejected: entry.quantity_rejected || "0",
            rejection_reason: entry.rejection_reason || null,
            batch: entry.batch || null,
            expiry: entry.expiry || null,
          })),
      }),
    onSuccess: (receipt) => {
      toast.show("success", `${receipt.number} posted.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      void client.invalidateQueries({ queryKey: ["sales"] });
      onClose();
    },
  });

  const amend = (index: number, patch: Partial<LineEntry>) =>
    setEntries((rows) => rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));

  const lineOf = (id: string) => order.lines.find((line) => line.id === id);
  const anything = entries.some((entry) => Number(entry.quantity_received) > 0);
  const reasonsGiven = entries.every(
    (entry) => Number(entry.quantity_rejected) === 0 || (entry.rejection_reason ?? "").trim(),
  );

  return (
    <Dialog
      size="lg"
      title={`Receive against ${order.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="transactional"
            onClick={() => receive.mutate()}
            busy={receive.isPending}
            disabled={!anything || !reasonsGiven}
          >
            Post the receipt
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {receive.isError ? <Alert tone="error" title={describe(receive.error).message} /> : null}
        <div className="form-grid">
          <Field label="Supplier's delivery note" optional>
            {(id) => (
              <TextInput
                id={id}
                value={deliveryReference}
                onChange={(event) => setDeliveryReference(event.target.value)}
                placeholder="DN-48812"
              />
            )}
          </Field>
          <div className="field">
            <span className="field__label">Inspection</span>
            <Checkbox
              label="Hold in quarantine pending inspection"
              hint="Valued to goods in quarantine rather than inventory until released."
              checked={quarantine}
              onChange={setQuarantine}
            />
          </div>
        </div>

        {entries.map((entry, index) => {
          const line = lineOf(entry.purchase_order_line_id);
          return (
            <div className="receive-line" key={entry.purchase_order_line_id}>
              <div className="receive-line__item">
                <span className="receive-line__name">{line?.description}</span>
                <span className="caption">
                  {quantity(String(entry.outstanding))} outstanding of{" "}
                  {quantity(line?.quantity_ordered ?? "0")} ordered
                </span>
              </div>
              <Field label="Received">
                {(id) => (
                  <Stepper
                    id={id}
                    min={0}
                    max={entry.outstanding}
                    value={entry.quantity_received}
                    onChange={(value) => amend(index, { quantity_received: value })}
                  />
                )}
              </Field>
              <Field label="Rejected">
                {(id) => (
                  <Stepper
                    id={id}
                    min={0}
                    max={Number(entry.quantity_received) || 0}
                    value={entry.quantity_rejected ?? "0"}
                    onChange={(value) => amend(index, { quantity_rejected: value })}
                  />
                )}
              </Field>
              {Number(entry.quantity_rejected) > 0 ? (
                <Field label="Why rejected" required>
                  {(id) => (
                    <TextInput
                      id={id}
                      value={entry.rejection_reason ?? ""}
                      onChange={(event) => amend(index, { rejection_reason: event.target.value })}
                      placeholder="Water damaged"
                    />
                  )}
                </Field>
              ) : null}
              <Field label="Batch" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={entry.batch ?? ""}
                    onChange={(event) => amend(index, { batch: event.target.value })}
                  />
                )}
              </Field>
              <Field label="Expiry" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    type="date"
                    value={entry.expiry ?? ""}
                    onChange={(event) => amend(index, { expiry: event.target.value })}
                  />
                )}
              </Field>
            </div>
          );
        })}
        {entries.length === 0 ? (
          <Alert tone="info" title="Everything on this order has already arrived." />
        ) : null}
      </div>
    </Dialog>
  );
}
