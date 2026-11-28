/**
 * Inventory: what is held, where, in what state, and how it moves.
 *
 * Views follow the storekeeper's day: stock on hand, the order being assembled, goods
 * awaiting inspection, transfers, counts, expiry, the ledger itself, and valuation.
 */

import { useState } from "react";

import { Tabs } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { useSession } from "@/lib/session";

import { Assembly } from "./Assembly";
import { Counts } from "./Counts";
import { Expiry } from "./Expiry";
import { Inspection } from "./Inspection";
import { Movements } from "./Movements";
import { StockOnHand } from "./StockOnHand";
import { Transfers } from "./Transfers";
import { Valuation } from "./Valuation";

type View =
  | "stock"
  | "assembly"
  | "inspection"
  | "transfers"
  | "counts"
  | "expiry"
  | "ledger"
  | "valuation";

const VIEWS: { id: View; label: string; detail: string; permission?: string }[] = [
  { id: "stock", label: "Stock on hand", detail: "Quantities by state, warehouse and item." },
  {
    id: "assembly",
    label: "Order assembly",
    detail: "Reserve, stage and dispatch customer orders.",
  },
  {
    id: "inspection",
    label: "Inspection",
    detail: "Goods in quarantine and goods rejected for return.",
  },
  { id: "transfers", label: "Transfers", detail: "Stock moving between warehouses." },
  { id: "counts", label: "Stock counts", detail: "Physical counts and their variances." },
  { id: "expiry", label: "Expiry", detail: "Batches approaching or past their expiry date." },
  { id: "ledger", label: "Stock ledger", detail: "Every movement, in the order it was recorded." },
  {
    id: "valuation",
    label: "Valuation",
    detail: "Cost layers against the inventory control accounts.",
    permission: "costing.view",
  },
];

export function Inventory() {
  const { can } = useSession();
  const [view, setView] = useState<View>("stock");
  const visible = VIEWS.filter((item) => !item.permission || can(item.permission));
  const current = visible.find((item) => item.id === view) ?? visible[0]!;

  return (
    <>
      <ContextBar trail={["Inventory", current.label]} />
      <WorkSurface>
        <PageHeading title="Inventory" detail={current.detail} />
        <Tabs
          items={visible.map(({ id, label }) => ({ id, label }))}
          active={current.id}
          onChange={setView}
        />
        {current.id === "stock" ? (
          <StockOnHand
            canAdjust={can("stock.adjust")}
            canTransfer={can("stock.transfer")}
            showCost={can("costing.view")}
          />
        ) : null}
        {current.id === "assembly" ? <Assembly canAssemble={can("assembly.manage")} /> : null}
        {current.id === "inspection" ? <Inspection canInspect={can("inspection.manage")} /> : null}
        {current.id === "transfers" ? <Transfers canTransfer={can("stock.transfer")} /> : null}
        {current.id === "counts" ? (
          <Counts canCount={can("stock_count.manage")} canApprove={can("stock_count.approve")} />
        ) : null}
        {current.id === "expiry" ? <Expiry /> : null}
        {current.id === "ledger" ? (
          <Movements canReverse={can("stock.adjust")} showCost={can("costing.view")} />
        ) : null}
        {current.id === "valuation" ? <Valuation /> : null}
      </WorkSurface>
    </>
  );
}
