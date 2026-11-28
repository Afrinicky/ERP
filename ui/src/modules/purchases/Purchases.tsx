/**
 * Purchases: what is bought, what arrived, what the supplier billed, and what is owed.
 *
 * Four views in the order the work happens. Quantities at the receiving bench use the
 * transactional stepper; the payment screen offers suggested amounts and carries one
 * primary action, as the design system requires.
 */

import { useState } from "react";

import { Tabs } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { useSession } from "@/lib/session";

import { Invoices } from "./Invoices";
import { Payables } from "./Payables";
import { PurchaseOrders } from "./PurchaseOrders";
import { Receipts } from "./Receipts";

type View = "orders" | "receipts" | "invoices" | "payables";

const VIEWS: { id: View; label: string; detail: string }[] = [
  {
    id: "orders",
    label: "Orders",
    detail: "Purchase orders raised against customer orders or for the shelf.",
  },
  {
    id: "receipts",
    label: "Receipts",
    detail: "What arrived against each order, accepted or turned away.",
  },
  {
    id: "invoices",
    label: "Invoices",
    detail: "Supplier bills matched against the order and the receipts before they are posted.",
  },
  {
    id: "payables",
    label: "Payables",
    detail: "What is owed to each supplier, aged by due date, and the payments made.",
  },
];

export function Purchases() {
  const { can } = useSession();
  const [view, setView] = useState<View>("orders");
  const [focusedOrder, setFocusedOrder] = useState<string | null>(null);
  const current = VIEWS.find((item) => item.id === view) ?? VIEWS[0]!;

  return (
    <>
      <ContextBar trail={["Purchases", current.label]} />
      <WorkSurface>
        <PageHeading title="Purchases" detail={current.detail} />
        <Tabs
          items={VIEWS.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={(next) => {
            setFocusedOrder(null);
            setView(next);
          }}
        />
        {view === "orders" ? (
          <PurchaseOrders
            canManage={can("purchase_order.manage")}
            canApprove={can("purchase_order.approve")}
            canReceive={can("goods_receipt.manage")}
            onReceive={(orderId) => {
              setFocusedOrder(orderId);
              setView("receipts");
            }}
          />
        ) : null}
        {view === "receipts" ? (
          <Receipts canReceive={can("goods_receipt.manage")} focusOrderId={focusedOrder} />
        ) : null}
        {view === "invoices" ? (
          <Invoices
            canManage={can("supplier_invoice.manage")}
            canApproveVariance={can("match_variance.approve")}
          />
        ) : null}
        {view === "payables" ? <Payables canPay={can("payment.manage")} /> : null}
      </WorkSurface>
    </>
  );
}
