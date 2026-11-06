/**
 * Sales: what customers have asked for, what they were offered, and on what terms.
 *
 * Five views over one sequence. A request becomes a comparison, a comparison becomes
 * a quotation, and the quotation carries its own cost build-up behind a permission —
 * so the screen a sales officer works on and the screen the proprietor checks margin
 * on are the same screen, read with different rights.
 */

import { useState } from "react";

import { Tabs } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { useSession } from "@/lib/session";

import { PricingPolicy } from "./PricingPolicy";
import { Quotations } from "./Quotations";
import { Requests } from "./Requests";
import { SalesOverview } from "./SalesOverview";
import { Sourcing } from "./Sourcing";

type View = "overview" | "requests" | "sourcing" | "quotations" | "policy";

const VIEWS: { id: View; label: string; detail: string }[] = [
  {
    id: "overview",
    label: "Position",
    detail:
      "What is out with customers, what has been won, and what is waiting on you.",
  },
  {
    id: "requests",
    label: "Requests",
    detail: "Enquiries received, and what each one is still waiting for.",
  },
  {
    id: "sourcing",
    label: "Sourcing",
    detail:
      "Supplier offers against a request, compared on landed cost rather than list price.",
  },
  {
    id: "quotations",
    label: "Quotations",
    detail:
      "Offers made, their cost build-up, and the terms the customer was given.",
  },
  {
    id: "policy",
    label: "Pricing policy",
    detail:
      "The margin, discount limits and instalment terms every quotation is priced by.",
  },
];

export function Sales() {
  const { can } = useSession();
  const [view, setView] = useState<View>("overview");
  const [focused, setFocused] = useState<string | null>(null);
  const current = VIEWS.find((item) => item.id === view) ?? VIEWS[0]!;

  const open = (next: View, requestId?: string) => {
    setFocused(requestId ?? null);
    setView(next);
  };

  return (
    <>
      <ContextBar trail={["Sales", current.label]} />
      <WorkSurface>
        <PageHeading title="Sales" detail={current.detail} />
        <Tabs
          items={VIEWS.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={(next) => open(next)}
        />
        {view === "overview" ? (
          <SalesOverview onOpen={(next) => open(next as View)} />
        ) : null}
        {view === "requests" ? (
          <Requests
            canManage={can("rfq.manage")}
            onSource={(requestId) => open("sourcing", requestId)}
          />
        ) : null}
        {view === "sourcing" ? (
          <Sourcing
            requestId={focused}
            canManage={can("supplier_quotation.manage")}
            onSelect={setFocused}
          />
        ) : null}
        {view === "quotations" ? (
          <Quotations
            canSeeCosting={can("costing.view")}
            canManage={can("quotation.manage")}
            canIssue={can("quotation.issue")}
            canApprove={can("quotation.approve")}
          />
        ) : null}
        {view === "policy" ? (
          <PricingPolicy canManage={can("price_list.manage")} />
        ) : null}
      </WorkSurface>
    </>
  );
}
