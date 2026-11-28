/**
 * Reports: the management pack and the operational reports, the runs kept and delivered,
 * schedules, and the month-end close.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Tabs } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { useSession } from "@/lib/session";

import { Inbox, Saved } from "./Archive";
import { PeriodClose } from "./PeriodClose";
import { Runner } from "./Runner";
import { Schedules } from "./Schedules";

type View = "reports" | "saved" | "inbox" | "schedules" | "close";

const DETAIL: Record<View, string> = {
  reports: "Every figure states its period, currency and basis, and exports exactly as shown.",
  saved: "Reports kept as they were generated, with the notes written beneath them.",
  inbox: "Scheduled reports delivered to you.",
  schedules: "Reports generated on a cadence and delivered to the people named.",
  close: "Month-end close: the checks, the confirmations and the lock.",
};

export function Reports() {
  const { can } = useSession();
  const [view, setView] = useState<View>("reports");
  const inbox = useQuery({ queryKey: ["reports", "inbox"], queryFn: api.reports.inbox });
  const unread = (inbox.data ?? []).filter((item) => !item.read_at).length;
  const items: { id: View; label: string }[] = [
    { id: "reports", label: "Reports" },
    { id: "saved", label: "Saved" },
    { id: "inbox", label: unread ? `Inbox (${unread})` : "Inbox" },
    { id: "schedules", label: "Schedules" },
    ...(can("account.view") ? [{ id: "close" as View, label: "Period close" }] : []),
  ];
  const current = items.find((item) => item.id === view) ?? items[0]!;

  return (
    <>
      <ContextBar trail={["Reports", current.label.replace(/ \(\d+\)$/, "")]} />
      <WorkSurface>
        <PageHeading title="Reports" detail={DETAIL[view]} />
        <Tabs items={items} active={view} onChange={setView} />
        {view === "reports" ? <Runner /> : null}
        {view === "saved" ? <Saved /> : null}
        {view === "inbox" ? <Inbox /> : null}
        {view === "schedules" ? <Schedules /> : null}
        {view === "close" ? <PeriodClose /> : null}
      </WorkSurface>
    </>
  );
}
