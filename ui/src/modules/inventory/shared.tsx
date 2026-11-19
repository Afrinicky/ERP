/**
 * What every inventory view reads: the warehouses with their places, and the stocked
 * items a storekeeper picks from.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Product, StockStateName, StockWarehouse } from "@/api/types";
import { Field, Select, StatusChip, type Tone } from "@/components/primitives";

export function useWarehouses() {
  return useQuery({
    queryKey: ["inventory", "warehouses"],
    queryFn: () => api.inventory.warehouses(),
    staleTime: 60_000,
  });
}

export function useStockedProducts() {
  return useQuery({
    queryKey: ["inventory", "products"],
    queryFn: async () => (await api.catalogue.search({ item_class: "stocked", limit: 500 })).items,
    staleTime: 60_000,
  });
}

export function WarehouseSelect({
  label = "Warehouse",
  value,
  onChange,
  warehouses,
  exclude,
}: {
  label?: string;
  value: string;
  onChange: (id: string) => void;
  warehouses: StockWarehouse[];
  exclude?: string;
}) {
  return (
    <Field label={label} required>
      {(id) => (
        <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Select a warehouse</option>
          {warehouses
            .filter((item) => item.id !== exclude)
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.code} · {item.name}
              </option>
            ))}
        </Select>
      )}
    </Field>
  );
}

export function ProductSelect({
  value,
  onChange,
  products,
  label = "Item",
}: {
  value: string;
  onChange: (product: Product | null) => void;
  products: Product[];
  label?: string;
}) {
  return (
    <Field label={label} required>
      {(id) => (
        <Select
          id={id}
          value={value}
          onChange={(event) =>
            onChange(products.find((item) => item.id === event.target.value) ?? null)
          }
        >
          <option value="">Select an item</option>
          {products.map((item) => (
            <option key={item.id} value={item.id}>
              {item.sku} · {item.name}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );
}

const STATE_LABEL: Record<StockStateName, [string, Tone]> = {
  usable: ["Usable", "ok"],
  quarantine: ["Quarantine", "warning"],
  rejected: ["Rejected", "error"],
  in_transit: ["In transit", "info"],
  reserved: ["Reserved", "accent"],
};

export function StateChip({ state }: { state: StockStateName }) {
  const [label, tone] = STATE_LABEL[state];
  return <StatusChip tone={tone}>{label}</StatusChip>;
}

export const MOVEMENT_LABEL: Record<string, string> = {
  opening: "Opening stock",
  receipt: "Goods received",
  inspection_pass: "Inspection passed",
  inspection_fail: "Inspection failed",
  return_to_supplier: "Returned to supplier",
  relocation: "Relocation",
  transfer_out: "Transfer dispatched",
  transfer_in: "Transfer received",
  reservation: "Reserved",
  release: "Released",
  issue: "Issued",
  customer_return: "Customer return",
  damage: "Damage",
  expiry: "Expiry write-off",
  count_gain: "Count gain",
  count_loss: "Count loss",
  adjustment_gain: "Adjustment gain",
  adjustment_loss: "Adjustment loss",
  revaluation: "Revaluation",
  reversal: "Reversal",
};

/** Serial numbers typed one per line or separated by commas. */
export function serialList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
