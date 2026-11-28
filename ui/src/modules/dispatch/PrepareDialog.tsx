/**
 * Prepare a shipment: choose the order, then how much of each line goes on this
 * consignment, up to what is reserved for it and not already on another.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import {
  Alert,
  Button,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  Stepper,
  TextArea,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { editable, quantity } from "@/lib/format";

interface Entry {
  quantity: string;
  serials: string;
}

export function PrepareDialog({
  orderId: initialOrder,
  onClose,
  onPrepared,
}: {
  orderId?: string;
  onClose: () => void;
  onPrepared: (shipmentId: string) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [orderId, setOrderId] = useState(initialOrder ?? "");
  const [destination, setDestination] = useState("");
  const [entries, setEntries] = useState<Record<string, Entry>>({});

  const orders = useQuery({
    queryKey: ["sales", "orders", "deliverable"],
    queryFn: async () => {
      const [open, partial, fulfilled] = await Promise.all([
        api.orders.list("open", 100),
        api.orders.list("partially_fulfilled", 100),
        api.orders.list("fulfilled", 100),
      ]);
      return [...open, ...partial, ...fulfilled];
    },
  });
  const lines = useQuery({
    queryKey: ["dispatch", "shippable", orderId],
    queryFn: () => api.dispatch.shippable(orderId),
    enabled: orderId !== "",
  });

  useEffect(() => {
    if (!lines.data) return;
    setEntries(
      Object.fromEntries(
        lines.data
          .filter((line) => line.holds_stock)
          .map((line) => [line.line_id, { quantity: editable(line.shippable), serials: "" }]),
      ),
    );
  }, [lines.data]);

  const prepare = useMutation({
    mutationFn: () =>
      api.dispatch.prepare({
        sales_order_id: orderId,
        destination: destination || null,
        lines: Object.entries(entries)
          .filter(([, entry]) => Number(entry.quantity) > 0)
          .map(([lineId, entry]) => ({
            sales_order_line_id: lineId,
            quantity: entry.quantity,
            serials: entry.serials
              .split(/[\n,]/)
              .map((item) => item.trim())
              .filter(Boolean),
          })),
      }),
    onSuccess: (shipment) => {
      toast.show("success", `${shipment.number} prepared.`);
      void client.invalidateQueries({ queryKey: ["dispatch"] });
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onPrepared(shipment.id);
    },
  });

  const stockLines = (lines.data ?? []).filter((line) => line.holds_stock);
  const anything = Object.values(entries).some((entry) => Number(entry.quantity) > 0);

  return (
    <Dialog
      size="lg"
      title="Prepare a shipment"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => prepare.mutate()}
            busy={prepare.isPending}
            disabled={!orderId || !anything}
          >
            Prepare the shipment
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {prepare.isError ? <Alert tone="error" title={describe(prepare.error).message} /> : null}
        <div className="form-grid">
          <Field label="Customer order" required>
            {(id) => (
              <Select id={id} value={orderId} onChange={(event) => setOrderId(event.target.value)}>
                <option value="">Select an order</option>
                {(orders.data ?? []).map((order) => (
                  <option key={order.id} value={order.id}>
                    {order.number} · {order.customer_name ?? "Customer"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Deliver to" optional hint="Leave blank for the customer's address.">
            {(id) => (
              <TextArea
                id={id}
                rows={2}
                value={destination}
                onChange={(event) => setDestination(event.target.value)}
                placeholder="Stores, Korle Bu Teaching Hospital, Accra"
              />
            )}
          </Field>
        </div>

        {orderId && lines.isPending ? <Skeleton rows={3} height={44} /> : null}
        {orderId && lines.data && stockLines.length === 0 ? (
          <EmptyState title="This order carries no stock items to ship." />
        ) : null}
        {stockLines.map((line) => {
          const entry = entries[line.line_id] ?? { quantity: "0", serials: "" };
          const limit = Number(line.shippable);
          return (
            <div className="receive-line" key={line.line_id}>
              <div className="receive-line__item">
                <span className="receive-line__name">{line.description}</span>
                <span className="caption">
                  {line.sku} · {quantity(line.outstanding)} owed · {quantity(line.shippable)} ready
                  to ship
                </span>
              </div>
              {limit > 0 ? (
                <Field label="On this shipment">
                  {(id) => (
                    <Stepper
                      id={id}
                      min={0}
                      max={limit}
                      value={entry.quantity}
                      disabled={limit === 0}
                      onChange={(value) =>
                        setEntries((rows) => ({
                          ...rows,
                          [line.line_id]: { ...entry, quantity: value },
                        }))
                      }
                    />
                  )}
                </Field>
              ) : (
                <span className="caption">Nothing reserved and free to ship.</span>
              )}
              {line.tracking === "serial" && Number(entry.quantity) > 0 ? (
                <Field label="Serial numbers" required hint="One per line">
                  {(id) => (
                    <TextArea
                      id={id}
                      rows={2}
                      value={entry.serials}
                      onChange={(event) =>
                        setEntries((rows) => ({
                          ...rows,
                          [line.line_id]: { ...entry, serials: event.target.value },
                        }))
                      }
                    />
                  )}
                </Field>
              ) : null}
            </div>
          );
        })}
        {stockLines.length > 0 && stockLines.every((line) => Number(line.shippable) === 0) ? (
          <Alert
            tone="info"
            title="Nothing on this order is reserved and free to ship. Reserve stock in Order assembly first."
          />
        ) : null}
      </div>
    </Dialog>
  );
}
