/**
 * Transfers between warehouses. Stock is in transit, and unavailable at either end,
 * from dispatch until it is received.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Product, StockTransfer } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  StatusChip,
  Stepper,
  TextInput,
  type Tone,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, quantity } from "@/lib/format";

import { ProductSelect, useStockedProducts, useWarehouses, WarehouseSelect } from "./shared";

const STATE: Record<StockTransfer["state"], [string, Tone]> = {
  draft: ["Prepared", "neutral"],
  dispatched: ["In transit", "info"],
  received: ["Received", "ok"],
  cancelled: ["Cancelled", "neutral"],
};

export function Transfers({ canTransfer }: { canTransfer: boolean }) {
  const client = useQueryClient();
  const toast = useToast();
  const [raising, setRaising] = useState(false);
  const transfers = useQuery({
    queryKey: ["inventory", "transfers"],
    queryFn: () => api.inventory.transfers(),
  });

  const act = useMutation({
    mutationFn: ({ id, step }: { id: string; step: "dispatch" | "receive" | "cancel" }) =>
      step === "dispatch"
        ? api.inventory.dispatchTransfer(id)
        : step === "receive"
          ? api.inventory.receiveTransfer(id)
          : api.inventory.cancelTransfer(id),
    onSuccess: (transfer) => {
      toast.show("success", `${transfer.number}: ${STATE[transfer.state][0].toLowerCase()}.`);
      void client.invalidateQueries({ queryKey: ["inventory"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <div className="stack gap-5">
      <Card
        title="Transfers"
        actions={
          canTransfer ? (
            <Button tone="primary" onClick={() => setRaising(true)}>
              Prepare a transfer
            </Button>
          ) : undefined
        }
        flush
      >
        {transfers.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : (transfers.data ?? []).length === 0 ? (
          <EmptyState
            title="No transfer has been prepared."
            action={
              canTransfer ? (
                <Button tone="primary" onClick={() => setRaising(true)}>
                  Prepare a transfer
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "13%" }}>Number</th>
                  <th style={{ width: "13%" }}>From</th>
                  <th style={{ width: "13%" }}>To</th>
                  <th>Items</th>
                  <th style={{ width: "12%" }}>Dispatched</th>
                  <th style={{ width: "12%" }}>Standing</th>
                  {canTransfer ? <th style={{ width: "18%" }} /> : null}
                </tr>
              </thead>
              <tbody>
                {(transfers.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td className="numeric">{item.number ?? "—"}</td>
                    <td>{item.from_warehouse}</td>
                    <td>{item.to_warehouse}</td>
                    <td>
                      {item.lines
                        .map((line) => `${quantity(line.quantity)} × ${line.sku}`)
                        .join(", ")}
                    </td>
                    <td>{item.dispatched_on ? formatDate(item.dispatched_on) : "—"}</td>
                    <td>
                      <StatusChip tone={STATE[item.state][1]}>{STATE[item.state][0]}</StatusChip>
                    </td>
                    {canTransfer ? (
                      <td className="table__actions">
                        {item.state === "draft" ? (
                          <>
                            <Button
                              tone="secondary"
                              size="touch"
                              busy={act.isPending}
                              onClick={() => act.mutate({ id: item.id, step: "dispatch" })}
                            >
                              Dispatch
                            </Button>
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => act.mutate({ id: item.id, step: "cancel" })}
                            >
                              Cancel
                            </Button>
                          </>
                        ) : null}
                        {item.state === "dispatched" ? (
                          <Button
                            tone="secondary"
                            size="touch"
                            busy={act.isPending}
                            onClick={() => act.mutate({ id: item.id, step: "receive" })}
                          >
                            Receive
                          </Button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {raising ? <RaiseDialog onClose={() => setRaising(false)} /> : null}
    </div>
  );
}

interface Entry {
  key: number;
  product: Product | null;
  quantity: string;
}

function RaiseDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const warehouses = useWarehouses();
  const products = useStockedProducts();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [notes, setNotes] = useState("");
  const [entries, setEntries] = useState<Entry[]>([{ key: 1, product: null, quantity: "1" }]);

  const amend = (key: number, patch: Partial<Entry>) =>
    setEntries((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  const save = useMutation({
    mutationFn: () =>
      api.inventory.raiseTransfer({
        from_warehouse_id: from,
        to_warehouse_id: to,
        notes: notes || null,
        lines: entries.map((entry) => ({
          product_id: entry.product!.id,
          quantity: entry.quantity,
        })),
      }),
    onSuccess: (transfer) => {
      toast.show("success", `${transfer.number} prepared.`);
      void client.invalidateQueries({ queryKey: ["inventory", "transfers"] });
      onClose();
    },
  });

  const complete =
    from && to && entries.every((entry) => entry.product && Number(entry.quantity) > 0);

  return (
    <Dialog
      size="lg"
      title="Prepare a transfer"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => save.mutate()}
            busy={save.isPending}
            disabled={!complete}
          >
            Prepare the transfer
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <div className="form-grid">
          <WarehouseSelect
            label="From"
            value={from}
            onChange={setFrom}
            warehouses={warehouses.data ?? []}
          />
          <WarehouseSelect
            label="To"
            value={to}
            onChange={setTo}
            warehouses={warehouses.data ?? []}
            exclude={from}
          />
        </div>
        {(warehouses.data ?? []).length < 2 ? (
          <Alert
            tone="info"
            title="A second warehouse is required before stock can be transferred."
          />
        ) : null}
        {entries.map((entry) => (
          <div className="form-grid" key={entry.key}>
            <ProductSelect
              value={entry.product?.id ?? ""}
              products={products.data ?? []}
              onChange={(product) => amend(entry.key, { product })}
            />
            <Field label="Quantity">
              {(id) => (
                <Stepper
                  id={id}
                  min={0}
                  value={entry.quantity}
                  onChange={(value) => amend(entry.key, { quantity: value })}
                />
              )}
            </Field>
          </div>
        ))}
        <div className="row gap-3">
          <Button
            tone="secondary"
            onClick={() =>
              setEntries((rows) => [
                ...rows,
                { key: rows.length + 1, product: null, quantity: "1" },
              ])
            }
          >
            Add a line
          </Button>
        </div>
        <Field label="Notes" optional>
          {(id) => (
            <TextInput id={id} value={notes} onChange={(event) => setNotes(event.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
