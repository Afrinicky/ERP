/**
 * Stock on hand: each item in each warehouse, by state.
 *
 * Available is usable stock in good batches less what is reserved. Opening stock and
 * adjustments are recorded from here; each posts to the ledger as it is saved.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Product, StockLineInput, StockPosition } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  RadioGroup,
  SearchField,
  Select,
  Stepper,
  TableSkeleton,
  TextArea,
  TextInput,
  TotalDisplay,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import {
  ProductSelect,
  serialList,
  useStockedProducts,
  useWarehouses,
  WarehouseSelect,
} from "./shared";

type Dialogue = "opening" | "adjustment" | null;

export function StockOnHand({
  canAdjust,
  canTransfer,
  showCost,
}: {
  canAdjust: boolean;
  canTransfer: boolean;
  showCost: boolean;
}) {
  const currency = useCurrency();
  const warehouses = useWarehouses();
  const [warehouseId, setWarehouseId] = useState("");
  const [term, setTerm] = useState("");
  const [dialogue, setDialogue] = useState<Dialogue>(null);
  const [relocating, setRelocating] = useState<StockPosition | null>(null);

  const positions = useQuery({
    queryKey: ["inventory", "positions", warehouseId, term],
    queryFn: () =>
      api.inventory.positions({ warehouse_id: warehouseId || undefined, q: term || undefined }),
  });
  const low = useQuery({ queryKey: ["inventory", "low"], queryFn: () => api.inventory.lowStock() });
  const expiring = useQuery({
    queryKey: ["inventory", "expiry", "default"],
    queryFn: () => api.inventory.expiry(),
  });
  const valuation = useQuery({
    queryKey: ["inventory", "valuation"],
    queryFn: () => api.inventory.valuation(),
    enabled: showCost,
  });

  const rows = positions.data ?? [];

  return (
    <div className="stack gap-5">
      <div className="measure-row">
        <TotalDisplay label="Items held" value={String(rows.length)} compact />
        <TotalDisplay
          label="At or below reorder level"
          value={String(low.data?.length ?? 0)}
          tone={(low.data?.length ?? 0) > 0 ? "due" : undefined}
          compact
        />
        <TotalDisplay
          label="Batches nearing expiry"
          value={String(expiring.data?.length ?? 0)}
          compact
        />
        {showCost ? (
          <TotalDisplay
            label="Stock at cost"
            value={money(valuation.data?.total ?? "0", currency)}
            caption={
              valuation.data
                ? valuation.data.agrees
                  ? "Agrees with the ledger"
                  : "Does not agree with the ledger"
                : undefined
            }
            compact
          />
        ) : null}
      </div>

      <Card
        title="Stock on hand"
        actions={
          canAdjust ? (
            <>
              <Button tone="secondary" onClick={() => setDialogue("adjustment")}>
                Record adjustment
              </Button>
              <Button tone="primary" onClick={() => setDialogue("opening")}>
                Record opening stock
              </Button>
            </>
          ) : undefined
        }
        flush
      >
        <div className="filter-bar">
          <SearchField
            label="Search items"
            placeholder="Search by code or name"
            value={term}
            onChange={setTerm}
          />
          <Select
            aria-label="Warehouse"
            value={warehouseId}
            onChange={(event) => setWarehouseId(event.target.value)}
          >
            <option value="">All warehouses</option>
            {(warehouses.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.code} · {item.name}
              </option>
            ))}
          </Select>
        </div>
        {positions.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <TableSkeleton columns={8} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title={term ? "No item matches that search." : "Nothing is held yet."}
            detail={
              term ? undefined : "Record opening stock, or receive goods against a purchase order."
            }
          />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "22%" }}>Item</th>
                  <th style={{ width: "11%" }}>Warehouse</th>
                  <th style={{ width: "8%" }} className="numeric">
                    Usable
                  </th>
                  <th style={{ width: "8%" }} className="numeric">
                    Reserved
                  </th>
                  <th style={{ width: "8%" }} className="numeric">
                    Available
                  </th>
                  <th style={{ width: "8%" }} className="numeric">
                    Quarantine
                  </th>
                  <th style={{ width: "8%" }} className="numeric">
                    In transit
                  </th>
                  <th style={{ width: "8%" }} className="numeric">
                    Incoming
                  </th>
                  {showCost ? (
                    <th style={{ width: "12%" }} className="numeric">
                      Value
                    </th>
                  ) : null}
                  {canTransfer ? <th style={{ width: "8%" }} /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.product_id}-${row.warehouse_id}`}>
                    <td>
                      <span className="cell-strong">{row.name}</span>
                      <span className="cell-note">
                        {row.sku}
                        {row.tracking !== "none"
                          ? ` · ${row.tracking === "batch" ? "Batch-tracked" : "Serialised"}`
                          : ""}
                      </span>
                    </td>
                    <td>{row.warehouse}</td>
                    <td className="numeric">{quantity(row.usable)}</td>
                    <td className="numeric">{quantity(row.reserved)}</td>
                    <td className="numeric cell-strong">{quantity(row.available)}</td>
                    <td className="numeric">{quantity(row.quarantine)}</td>
                    <td className="numeric">{quantity(row.in_transit)}</td>
                    <td className="numeric">{quantity(row.incoming)}</td>
                    {showCost ? <td className="numeric">{money(row.value, currency)}</td> : null}
                    {canTransfer ? (
                      <td className="table__actions">
                        <Button tone="plain" size="compact" onClick={() => setRelocating(row)}>
                          Relocate
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {dialogue === "opening" ? <OpeningDialog onClose={() => setDialogue(null)} /> : null}
      {dialogue === "adjustment" ? <AdjustmentDialog onClose={() => setDialogue(null)} /> : null}
      {relocating ? (
        <RelocateDialog position={relocating} onClose={() => setRelocating(null)} />
      ) : null}
    </div>
  );
}

// -- opening stock ----------------------------------------------------------------------

interface Entry extends StockLineInput {
  key: number;
  serialText: string;
  product: Product | null;
}

function blank(key: number): Entry {
  return {
    key,
    product_id: "",
    product: null,
    quantity: "1",
    unit_cost: "",
    batch: "",
    expiry: "",
    serialText: "",
  };
}

function OpeningDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const warehouses = useWarehouses();
  const products = useStockedProducts();
  const [warehouseId, setWarehouseId] = useState("");
  const [reference, setReference] = useState("");
  const [entries, setEntries] = useState<Entry[]>([blank(1)]);

  const amend = (key: number, patch: Partial<Entry>) =>
    setEntries((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  const save = useMutation({
    mutationFn: () =>
      api.inventory.opening({
        warehouse_id: warehouseId,
        reference: reference || null,
        lines: entries.map((entry) => ({
          product_id: entry.product_id,
          quantity: entry.quantity,
          unit_cost: entry.unit_cost || null,
          batch: entry.batch || null,
          expiry: entry.expiry || null,
          serials: serialList(entry.serialText),
        })),
      }),
    onSuccess: () => {
      toast.show("success", "Opening stock posted.");
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });

  const complete =
    warehouseId !== "" &&
    entries.every((entry) => entry.product_id && Number(entry.quantity) > 0 && entry.unit_cost);

  return (
    <Dialog
      size="lg"
      title="Record opening stock"
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
            Post opening stock
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <div className="form-grid">
          <WarehouseSelect
            value={warehouseId}
            onChange={setWarehouseId}
            warehouses={warehouses.data ?? []}
          />
          <Field label="Reference" optional>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="Stock sheet 31 Oct"
              />
            )}
          </Field>
        </div>
        {entries.map((entry) => (
          <div className="receive-line" key={entry.key}>
            <ProductSelect
              value={entry.product_id}
              products={products.data ?? []}
              onChange={(product) => amend(entry.key, { product, product_id: product?.id ?? "" })}
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
            <Field label="Unit cost" required>
              {(id) => (
                <TextInput
                  id={id}
                  inputMode="decimal"
                  value={entry.unit_cost ?? ""}
                  onChange={(event) => amend(entry.key, { unit_cost: event.target.value })}
                  placeholder="0.00"
                />
              )}
            </Field>
            {entry.product && entry.product.tracking_policy !== "serial" ? (
              <Field
                label="Batch"
                required={entry.product.tracking_policy === "batch"}
                optional={entry.product.tracking_policy !== "batch"}
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={entry.batch ?? ""}
                    onChange={(event) => amend(entry.key, { batch: event.target.value })}
                  />
                )}
              </Field>
            ) : null}
            {entry.product?.requires_expiry ? (
              <Field label="Expiry" required>
                {(id) => (
                  <TextInput
                    id={id}
                    type="date"
                    value={entry.expiry ?? ""}
                    onChange={(event) => amend(entry.key, { expiry: event.target.value })}
                  />
                )}
              </Field>
            ) : null}
            {entry.product?.tracking_policy === "serial" ? (
              <Field label="Serial numbers" required hint="One per line" full>
                {(id) => (
                  <TextArea
                    id={id}
                    rows={3}
                    value={entry.serialText}
                    onChange={(event) => amend(entry.key, { serialText: event.target.value })}
                  />
                )}
              </Field>
            ) : null}
          </div>
        ))}
        <div className="row gap-3">
          <Button
            tone="secondary"
            onClick={() => setEntries((rows) => [...rows, blank(rows.length + 1)])}
          >
            Add a line
          </Button>
          {entries.length > 1 ? (
            <Button tone="plain" onClick={() => setEntries((rows) => rows.slice(0, -1))}>
              Remove the last line
            </Button>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}

// -- adjustment -------------------------------------------------------------------------

type Kind = "damage" | "expiry" | "loss" | "gain";

function AdjustmentDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const warehouses = useWarehouses();
  const products = useStockedProducts();
  const [warehouseId, setWarehouseId] = useState("");
  const [kind, setKind] = useState<Kind>("damage");
  const [reason, setReason] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  const [amount, setAmount] = useState("1");
  const [batchId, setBatchId] = useState("");
  const [serialText, setSerialText] = useState("");

  const batches = useQuery({
    queryKey: ["inventory", "batches", product?.id],
    queryFn: () => api.inventory.batches(product!.id),
    enabled: product?.tracking_policy === "batch",
  });

  const save = useMutation({
    mutationFn: () =>
      api.inventory.adjust({
        warehouse_id: warehouseId,
        kind,
        reason,
        lines: [
          {
            product_id: product!.id,
            quantity: amount,
            batch_id: batchId || null,
            serials: serialList(serialText),
          },
        ],
      }),
    onSuccess: () => {
      toast.show("success", "Adjustment posted.");
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });

  return (
    <Dialog
      title="Record adjustment"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="transactional"
            tone={kind === "gain" ? "primary" : "destructive"}
            onClick={() => save.mutate()}
            busy={save.isPending}
            disabled={!warehouseId || !product || !reason.trim() || Number(amount) <= 0}
          >
            {kind === "gain" ? "Post the gain" : "Write off"}
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <RadioGroup
          legend="Nature of the adjustment"
          inline
          value={kind}
          onChange={setKind}
          options={[
            { value: "damage", label: "Damage" },
            { value: "expiry", label: "Expiry" },
            { value: "loss", label: "Loss" },
            { value: "gain", label: "Gain" },
          ]}
        />
        <div className="form-grid">
          <WarehouseSelect
            value={warehouseId}
            onChange={setWarehouseId}
            warehouses={warehouses.data ?? []}
          />
          <ProductSelect
            value={product?.id ?? ""}
            products={products.data ?? []}
            onChange={(next) => {
              setProduct(next);
              setBatchId("");
            }}
          />
          <Field label="Quantity">
            {(id) => <Stepper id={id} min={0} value={amount} onChange={setAmount} />}
          </Field>
          {product?.tracking_policy === "batch" ? (
            <Field label="Batch" required={kind !== "gain"}>
              {(id) => (
                <Select
                  id={id}
                  value={batchId}
                  onChange={(event) => setBatchId(event.target.value)}
                >
                  <option value="">Earliest expiry first</option>
                  {(batches.data ?? []).map((item) => (
                    <option key={item.batch_id} value={item.batch_id}>
                      {item.batch} · {quantity(item.quantity)} held
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          ) : null}
          {product?.tracking_policy === "serial" ? (
            <Field label="Serial numbers" required hint="One per line" full>
              {(id) => (
                <TextArea
                  id={id}
                  rows={3}
                  value={serialText}
                  onChange={(event) => setSerialText(event.target.value)}
                />
              )}
            </Field>
          ) : null}
          <Field label="Reason" required full>
            {(id) => (
              <TextInput
                id={id}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Cartons crushed in the store"
              />
            )}
          </Field>
        </div>
        {kind === "gain" ? (
          <Alert tone="info" title="A gain is valued at the item's current average cost." />
        ) : null}
      </div>
    </Dialog>
  );
}

// -- relocation -------------------------------------------------------------------------

function RelocateDialog({ position, onClose }: { position: StockPosition; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const warehouses = useWarehouses();
  const warehouse = (warehouses.data ?? []).find((item) => item.id === position.warehouse_id);
  const places = (warehouse?.locations ?? []).filter((item) => item.kind !== "quarantine");
  const [toLocation, setToLocation] = useState("");
  const [toBin, setToBin] = useState("");
  const [amount, setAmount] = useState("1");
  const bins = places.find((item) => item.id === toLocation)?.bins ?? [];

  const save = useMutation({
    mutationFn: () =>
      api.inventory.relocate({
        product_id: position.product_id,
        warehouse_id: position.warehouse_id,
        quantity: amount,
        to_location_id: toLocation,
        to_bin_id: toBin || null,
      }),
    onSuccess: () => {
      toast.show("success", `${position.sku} relocated.`);
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });

  return (
    <Dialog
      size="sm"
      title={`Relocate ${position.sku}`}
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
            disabled={!toLocation || Number(amount) <= 0}
          >
            Relocate
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <Field label="Quantity">
          {(id) => (
            <Stepper
              id={id}
              min={0}
              max={Number(position.usable)}
              value={amount}
              onChange={setAmount}
            />
          )}
        </Field>
        <Field label="To location" required>
          {(id) => (
            <Select
              id={id}
              value={toLocation}
              onChange={(event) => setToLocation(event.target.value)}
            >
              <option value="">Select a location</option>
              {places.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.code} · {item.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {bins.length ? (
          <Field label="Bin" optional>
            {(id) => (
              <Select id={id} value={toBin} onChange={(event) => setToBin(event.target.value)}>
                <option value="">No bin</option>
                {bins.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.code}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
      </div>
    </Dialog>
  );
}
