/**
 * Point of Sale: the counter register.
 *
 * One shift per cashier. Without an open shift the register offers to open one, and
 * lists the shifts waiting for a supervisor's sign-off. With one, the counter fills the
 * screen: the sale on the left, payment on the right, the drawer's position above.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Button, Skeleton } from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { dateTime, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

import { Counter } from "./Counter";
import { SalesDialog } from "./SalesDialog";
import { CashMovementDialog, CloseShiftDialog, OpenShift, ParkedDialog } from "./ShiftDialogs";

type Open = "cash" | "close" | "sales" | "parked" | null;

export function Register() {
  const { can } = useSession();
  const currency = useCurrency();
  const queries = useQueryClient();
  const [open, setOpen] = useState<Open>(null);

  const shift = useQuery({ queryKey: ["pos", "shift"], queryFn: () => api.pos.shift() });
  const parked = useQuery({
    queryKey: ["pos", "parked"],
    queryFn: () => api.pos.parked(),
    enabled: Boolean(shift.data),
  });
  const refresh = () => void queries.invalidateQueries({ queryKey: ["pos"] });

  if (shift.isPending) {
    return (
      <>
        <ContextBar trail={["Point of Sale"]} />
        <WorkSurface>
          <Skeleton rows={6} height={28} />
        </WorkSurface>
      </>
    );
  }

  const current = shift.data;
  if (!current) {
    return (
      <>
        <ContextBar trail={["Point of Sale", "Open a shift"]} />
        <WorkSurface>
          <PageHeading title="Point of Sale" detail="Open a shift to begin selling." />
          <OpenShift onOpened={refresh} />
        </WorkSurface>
      </>
    );
  }

  const waiting = parked.data?.length ?? 0;

  return (
    <>
      <ContextBar trail={["Point of Sale", current.shift.register_name]} />
      <WorkSurface>
        <div className="register__head">
          <PageHeading
            title="Point of Sale"
            detail={`Shift ${current.shift.number} · opened ${dateTime(current.shift.opened_at)}`}
          />
          <div className="register__drawer" aria-label="Drawer position">
            <span className="register__drawer-label">Drawer should hold</span>
            <span className="register__drawer-value">
              {money(current.drawer.expected, currency)}
            </span>
          </div>
        </div>
        <div className="register__toolbar" role="toolbar" aria-label="Register">
          <Button onClick={() => setOpen("parked")} disabled={waiting === 0}>
            {waiting > 0 ? `Parked sales · ${waiting}` : "No parked sales"}
          </Button>
          <Button onClick={() => setOpen("sales")}>Sales and refunds</Button>
          {can("pos.shift_manage") ? (
            <>
              <Button onClick={() => setOpen("cash")}>Cash in or out</Button>
              <Button onClick={() => setOpen("close")}>Close shift</Button>
            </>
          ) : null}
        </div>
        <Counter onChanged={refresh} />
      </WorkSurface>

      {open === "cash" ? (
        <CashMovementDialog
          expected={current.drawer.expected}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            refresh();
          }}
        />
      ) : null}
      {open === "close" ? (
        <CloseShiftDialog
          summary={current}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            refresh();
          }}
        />
      ) : null}
      {open === "sales" ? (
        <SalesDialog shiftId={current.shift.id} onClose={() => setOpen(null)} onChanged={refresh} />
      ) : null}
      {open === "parked" ? (
        <ParkedDialog
          onClose={() => setOpen(null)}
          onResumed={() => {
            setOpen(null);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}
