/**
 * Planning and Analysis: what was posted, drawn; profitability and margin erosion; budgets
 * against actual; the cash forecast and the alerts that watch the business.
 *
 * Four kinds of figure meet here — actual, budget, forecast and quotation estimate — and
 * each is labelled as what it is wherever it appears.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Alert, StatusChip, Tabs } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

import { Analytics } from "./Analytics";
import { Budgets } from "./Budgets";
import { Erosion } from "./Erosion";
import { Forecast } from "./Forecast";
import { Profitability } from "./Profitability";

type View = "analytics" | "profitability" | "erosion" | "budgets" | "forecast";

const VIEWS: { id: View; label: string; detail: string }[] = [
  {
    id: "analytics",
    label: "Analytics",
    detail: "The ledger drawn: select any bar or point to open the records it is made of.",
  },
  {
    id: "profitability",
    label: "Profitability",
    detail: "Contribution on what was realised, with the actual cost of the goods.",
  },
  {
    id: "erosion",
    label: "Margin erosion",
    detail: "Quoted margin against realised margin, and each cause of the difference.",
  },
  {
    id: "budgets",
    label: "Budgets",
    detail: "Plans by account, category and analysis code, approved, versioned and compared.",
  },
  {
    id: "forecast",
    label: "Cash forecast",
    detail: "Cash today, then what the records say will come in and go out.",
  },
];

export function Planning() {
  const { can } = useSession();
  const currency = useCurrency();
  const [view, setView] = useState<View>("analytics");
  const alerts = useQuery({
    queryKey: ["planning", "alerts"],
    queryFn: api.planning.alerts,
    enabled: can("report.view"),
  });
  const current = VIEWS.find((item) => item.id === view) ?? VIEWS[0]!;
  const pressing = (alerts.data ?? []).filter((item) => item.severity !== "notice");

  return (
    <>
      <ContextBar trail={["Planning and Analysis", current.label]} />
      <WorkSurface>
        <PageHeading title="Planning and Analysis" detail={current.detail} />
        {pressing.length > 0 ? (
          <section className="alerts" aria-label="Needs attention">
            {pressing.slice(0, 4).map((item) => (
              <div key={item.kind} className={`alerts__item alerts__item--${item.severity}`}>
                <StatusChip tone={item.severity === "critical" ? "error" : "warning"}>
                  {item.severity === "critical" ? "Critical" : "Attention"}
                </StatusChip>
                <div>
                  <strong>{item.title}</strong>
                  <div className="cell-note">
                    {item.detail}
                    {item.amount ? ` ${money(item.amount, currency)}` : ""}
                    {item.count > 1 ? ` · ${item.count} items` : ""}
                  </div>
                </div>
              </div>
            ))}
          </section>
        ) : alerts.isError ? (
          <Alert tone="error" title="Alerts could not be read." />
        ) : null}
        <Tabs
          items={VIEWS.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={setView}
        />
        {view === "analytics" ? <Analytics /> : null}
        {view === "profitability" ? <Profitability /> : null}
        {view === "erosion" ? <Erosion /> : null}
        {view === "budgets" ? <Budgets /> : null}
        {view === "forecast" ? <Forecast /> : null}
      </WorkSurface>
    </>
  );
}
