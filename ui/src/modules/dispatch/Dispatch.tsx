/**
 * Dispatch: consignments for customer orders, from the bay to the customer's signature.
 *
 * Three views in the order the work happens: what is being prepared, what is on the road,
 * and what has been closed. Each shipment opens to its own record, where the next step is
 * the one large action.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Shipment, ShipmentStateName } from "@/api/types";
import {
  Button,
  Card,
  EmptyState,
  Skeleton,
  StatusChip,
  Tabs,
  TotalDisplay,
  type Tone,
} from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { date as formatDate, quantity } from "@/lib/format";
import { useSession } from "@/lib/session";

import { PrepareDialog } from "./PrepareDialog";
import { ShipmentDialog } from "./ShipmentDialog";

type View = "preparing" | "transit" | "closed";

const VIEWS: { id: View; label: string; detail: string; states: ShipmentStateName[] }[] = [
  {
    id: "preparing",
    label: "Awaiting dispatch",
    detail: "Consignments being picked and packed.",
    states: ["prepared", "packed"],
  },
  {
    id: "transit",
    label: "In transit",
    detail: "Goods on the road, awaiting the customer's signature.",
    states: ["dispatched"],
  },
  {
    id: "closed",
    label: "Completed",
    detail: "Delivered, part-delivered, failed and withdrawn consignments.",
    states: ["delivered", "partially_delivered", "failed", "cancelled"],
  },
];

export const STATE_TONE: Record<ShipmentStateName, Tone> = {
  prepared: "neutral",
  packed: "info",
  dispatched: "accent",
  delivered: "ok",
  partially_delivered: "warning",
  failed: "error",
  cancelled: "neutral",
};

export function Dispatch() {
  const { can } = useSession();
  const [view, setView] = useState<View>("preparing");
  const [preparing, setPreparing] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const current = VIEWS.find((item) => item.id === view) ?? VIEWS[0]!;

  const shipments = useQuery({
    queryKey: ["dispatch", "shipments"],
    queryFn: () => api.dispatch.shipments(),
  });
  const all = shipments.data ?? [];
  const rows = all.filter((item) => current.states.includes(item.state));
  const count = (states: ShipmentStateName[]) =>
    String(all.filter((item) => states.includes(item.state)).length);

  return (
    <>
      <ContextBar trail={["Dispatch", current.label]} />
      <WorkSurface>
        <PageHeading title="Dispatch" detail={current.detail} />
        <div className="measure-row">
          <TotalDisplay label="Awaiting dispatch" value={count(["prepared", "packed"])} compact />
          <TotalDisplay label="In transit" value={count(["dispatched"])} compact />
          <TotalDisplay label="Delivered" value={count(["delivered"])} compact />
          <TotalDisplay
            label="With exceptions"
            value={count(["partially_delivered", "failed"])}
            tone={all.some((item) => item.state === "failed") ? "due" : undefined}
            compact
          />
        </div>
        <Tabs
          items={VIEWS.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={setView}
        />
        <Card
          title={current.label}
          actions={
            can("dispatch.manage") ? (
              <Button tone="primary" onClick={() => setPreparing(true)}>
                Prepare a shipment
              </Button>
            ) : undefined
          }
          flush
        >
          {shipments.isPending ? (
            <div style={{ padding: "var(--space-4)" }}>
              <Skeleton rows={4} />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              title={
                view === "preparing"
                  ? "Nothing is being prepared."
                  : view === "transit"
                    ? "Nothing is on the road."
                    : "No consignment has been closed yet."
              }
            />
          ) : (
            <ShipmentTable rows={rows} onOpen={setOpenId} />
          )}
        </Card>
      </WorkSurface>
      {preparing ? (
        <PrepareDialog
          onClose={() => setPreparing(false)}
          onPrepared={(id) => {
            setPreparing(false);
            setOpenId(id);
          }}
        />
      ) : null}
      {openId ? <ShipmentDialog shipmentId={openId} onClose={() => setOpenId(null)} /> : null}
    </>
  );
}

function ShipmentTable({ rows, onOpen }: { rows: Shipment[]; onOpen: (id: string) => void }) {
  return (
    <div className="table-frame">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: "15%" }}>Shipment</th>
            <th style={{ width: "20%" }}>Customer</th>
            <th>Goods</th>
            <th style={{ width: "16%" }}>Carriage</th>
            <th style={{ width: "11%" }}>Dispatched</th>
            <th style={{ width: "13%" }}>Standing</th>
            <th style={{ width: "7%" }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td className="numeric">
                {item.number}
                {item.waybill ? (
                  <span className="cell-note">Waybill {item.waybill.number}</span>
                ) : null}
              </td>
              <td>{item.customer ?? "—"}</td>
              <td>
                {item.lines.map((line) => `${quantity(line.quantity)} × ${line.sku}`).join(", ")}
                {item.exceptions.length ? (
                  <span className="cell-note">
                    {item.exceptions.map((entry) => entry.kind_label).join(", ")}
                  </span>
                ) : null}
              </td>
              <td>
                {item.dispatched_on ? item.mode_label : "—"}
                {item.carrier || item.driver ? (
                  <span className="cell-note">{item.carrier ?? item.driver}</span>
                ) : null}
              </td>
              <td>{item.dispatched_on ? formatDate(item.dispatched_on) : "—"}</td>
              <td>
                <StatusChip tone={STATE_TONE[item.state]}>{item.state_label}</StatusChip>
              </td>
              <td className="table__actions">
                <Button tone="plain" size="compact" onClick={() => onOpen(item.id)}>
                  Open
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
