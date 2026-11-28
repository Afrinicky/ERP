/**
 * The affiliate commission register.
 *
 * A referrer's entitlement moves with the customer's money, not with the invoice:
 * accrued when the sale is raised, payable when the customer pays, and paid when the
 * business settles it. The register shows all three, because the gap between them is
 * the business's working capital.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { CommissionAccrual } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Measure,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { ContextBar, PageHeading, WorkSurface } from "@/components/shell";
import { describe } from "@/lib/errors";
import { date as formatDate, humanise, money, percent } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

const STATE_TONE: Record<string, Tone> = {
  accrued: "neutral",
  approved: "info",
  payable: "warning",
  paid: "ok",
  cancelled: "error",
};

export function Affiliates() {
  const currency = useCurrency();
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();
  const canManage = can("affiliate.manage");
  const [state, setState] = useState("");
  const [paying, setPaying] = useState<CommissionAccrual | null>(null);
  const [overriding, setOverriding] = useState<CommissionAccrual | null>(null);
  const [cancelling, setCancelling] = useState<CommissionAccrual | null>(null);

  const totals = useQuery({
    queryKey: ["commissions", "totals"],
    queryFn: api.commissions.totals,
  });
  const register = useQuery({
    queryKey: ["commissions", "register", state],
    queryFn: () =>
      api.commissions.register({ state: state || undefined, limit: 200 }),
  });

  const approve = useMutation({
    mutationFn: (accrualId: string) => api.commissions.approve(accrualId),
    onSuccess: () => {
      toast.show("success", "Entitlement approved.");
      void client.invalidateQueries({ queryKey: ["commissions"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <>
      <ContextBar trail={["Affiliates", "Commission register"]} />
      <WorkSurface>
        <PageHeading
          title="Affiliates"
          detail="What referrers have earned, what is owed to them, and what has been settled."
        />

        <div className="stack gap-6">
          {totals.isPending ? (
            <Skeleton rows={4} height={64} />
          ) : totals.data ? (
            <div className="measure-grid">
              <Measure
                label="Owed"
                value={money(totals.data.outstanding, currency)}
                period="Approved or payable, less what has been settled"
              />
              <Measure
                label="Payable now"
                value={money(totals.data.payable, currency)}
                period="The customer has paid; the referrer has not"
              />
              <Measure
                label="Awaiting approval"
                value={money(totals.data.accrued, currency)}
                period="Accrued but not yet reviewed"
              />
              <Measure
                label="Settled"
                value={money(totals.data.settled, currency)}
                period="Paid to referrers to date"
              />
            </div>
          ) : null}

          <Card
            title="Commission register"
            subtitle="One entitlement per quotation line within a referrer’s arrangement."
            actions={
              <Select
                aria-label="Filter by state"
                value={state}
                onChange={(event) => setState(event.target.value)}
              >
                <option value="">Every state</option>
                <option value="accrued">Accrued</option>
                <option value="approved">Approved</option>
                <option value="payable">Payable</option>
                <option value="paid">Paid</option>
                <option value="cancelled">Cancelled</option>
              </Select>
            }
            flush
          >
            {register.isPending ? (
              <div style={{ padding: "var(--space-4)" }}>
                <Skeleton rows={5} />
              </div>
            ) : (register.data ?? []).length === 0 ? (
              <EmptyState
                title="No commission has accrued."
                detail="An entitlement arises when a sale is raised against a quotation carrying a referrer."
              />
            ) : (
              <div className="table-frame">
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>Referrer</th>
                      <th style={{ width: "11%" }}>Quotation</th>
                      <th className="numeric" style={{ width: "6%" }}>
                        Rate
                      </th>
                      <th className="numeric" style={{ width: "12%" }}>
                        Accrued
                      </th>
                      <th className="numeric" style={{ width: "12%" }}>
                        Entitled
                      </th>
                      <th className="numeric" style={{ width: "11%" }}>
                        Settled
                      </th>
                      <th style={{ width: "11%" }}>Standing</th>
                      <th style={{ width: "19%" }} />
                    </tr>
                  </thead>
                  <tbody>
                    {(register.data ?? []).map((row) => (
                      <tr key={row.id}>
                        <td className="table__cell--wrap">
                          {row.affiliate_name ?? "—"}
                          <span className="cell-note">
                            {[
                              formatDate(row.accrued_on),
                              row.source_reference,
                              row.prior_rate
                                ? `was ${percent(row.prior_rate)}`
                                : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </td>
                        <td>{row.quotation_number ?? "—"}</td>
                        <td className="numeric">{percent(row.rate)}</td>
                        <td className="numeric">
                          {money(row.accrued_amount, currency)}
                        </td>
                        <td className="numeric">
                          {money(row.entitlement, currency)}
                          {row.paid_fraction !== "1.000000" ? (
                            <span className="cell-note">
                              {percent(row.paid_fraction)} collected
                            </span>
                          ) : null}
                        </td>
                        <td className="numeric">
                          {money(row.settled_amount, currency)}
                        </td>
                        <td>
                          <StatusChip tone={STATE_TONE[row.state] ?? "neutral"}>
                            {humanise(row.state)}
                          </StatusChip>
                        </td>
                        <td className="table__actions">
                          {canManage && row.state === "accrued" ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => approve.mutate(row.id)}
                            >
                              Approve
                            </Button>
                          ) : null}
                          {canManage && row.state === "payable" ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => setPaying(row)}
                            >
                              Pay
                            </Button>
                          ) : null}
                          {canManage &&
                          !["paid", "cancelled"].includes(row.state) ? (
                            <>
                              <Button
                                tone="plain"
                                size="compact"
                                onClick={() => setOverriding(row)}
                              >
                                Re-rate
                              </Button>
                              <Button
                                tone="plain"
                                size="compact"
                                onClick={() => setCancelling(row)}
                              >
                                Cancel
                              </Button>
                            </>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        {paying ? (
          <PayDialog accrual={paying} onClose={() => setPaying(null)} />
        ) : null}
        {overriding ? (
          <OverrideDialog
            accrual={overriding}
            onClose={() => setOverriding(null)}
          />
        ) : null}
        {cancelling ? (
          <CancelDialog
            accrual={cancelling}
            onClose={() => setCancelling(null)}
          />
        ) : null}
      </WorkSurface>
    </>
  );
}

function PayDialog({
  accrual,
  onClose,
}: {
  accrual: CommissionAccrual;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [amount, setAmount] = useState(accrual.entitlement);
  const [reference, setReference] = useState("");

  const pay = useMutation({
    mutationFn: () =>
      api.commissions.pay(accrual.id, { amount, reference: reference || null }),
    onSuccess: () => {
      toast.show("success", "Payment recorded.");
      void client.invalidateQueries({ queryKey: ["commissions"] });
      onClose();
    },
  });

  return (
    <Dialog
      title={`Pay ${accrual.affiliate_name ?? "the referrer"}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => pay.mutate()} busy={pay.isPending}>
            Record payment
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {pay.isError ? (
          <Alert tone="error" title={describe(pay.error).message} />
        ) : null}
        <p className="muted">
          Entitled to {money(accrual.entitlement, currency)}, of which{" "}
          {money(accrual.settled_amount, currency)} has been settled.
        </p>
        <Field label="Amount" required>
          {(id) => (
            <TextInput
              id={id}
              numeric
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          )}
        </Field>
        <Field
          label="Reference"
          optional
          hint="The transfer or mobile money reference."
        >
          {(id) => (
            <TextInput
              id={id}
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function OverrideDialog({
  accrual,
  onClose,
}: {
  accrual: CommissionAccrual;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [rate, setRate] = useState(accrual.rate);
  const [reason, setReason] = useState("");

  const override = useMutation({
    mutationFn: () => api.commissions.override(accrual.id, { rate, reason }),
    onSuccess: () => {
      toast.show(
        "success",
        "Rate amended. The previous rate stays on the record.",
      );
      void client.invalidateQueries({ queryKey: ["commissions"] });
      onClose();
    },
  });

  return (
    <Dialog
      title="Amend the commission rate"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => override.mutate()}
            busy={override.isPending}
            disabled={!reason.trim()}
          >
            Amend
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {override.isError ? (
          <Alert tone="error" title={describe(override.error).message} />
        ) : null}
        <p className="muted">
          The current rate of {percent(accrual.rate)} is kept beside the new
          one, with who changed it and why.
        </p>
        <Field
          label="New rate"
          required
          hint="A fraction, so 0.05 is five per cent."
        >
          {(id) => (
            <TextInput
              id={id}
              numeric
              inputMode="decimal"
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          )}
        </Field>
        <Field label="Reason" required>
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function CancelDialog({
  accrual,
  onClose,
}: {
  accrual: CommissionAccrual;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");

  const cancel = useMutation({
    mutationFn: () => api.commissions.cancel(accrual.id, reason),
    onSuccess: () => {
      toast.show("success", "Entitlement cancelled.");
      void client.invalidateQueries({ queryKey: ["commissions"] });
      onClose();
    },
  });

  return (
    <Dialog
      title="Cancel this entitlement"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Keep it
          </Button>
          <Button
            tone="destructive"
            onClick={() => cancel.mutate()}
            busy={cancel.isPending}
            disabled={!reason.trim()}
          >
            Cancel entitlement
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {cancel.isError ? (
          <Alert tone="error" title={describe(cancel.error).message} />
        ) : null}
        <p className="muted">
          A cancellation is recorded, not removed: the entitlement stays in the
          register with the reason it was withdrawn.
        </p>
        <Field label="Reason" required>
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
