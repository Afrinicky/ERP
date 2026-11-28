/**
 * The shift: opening float, cash in and out, the closing count and its sign-off, and
 * sales parked for later.
 *
 * The count is totalled by the service as the cashier enters it, so the variance on
 * screen is the variance that will be recorded.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PosShiftSummary } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Details,
  Dialog,
  EmptyState,
  Field,
  Keypad,
  RadioGroup,
  Stepper,
  TextArea,
  TextInput,
  TotalDisplay,
  useToast,
} from "@/components/primitives";
import { isNegative, isZero } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { dateTime, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

// -- opening ----------------------------------------------------------------------------

export function OpenShift({ onOpened }: { onOpened: () => void }) {
  const { can } = useSession();
  const [float, setFloat] = useState("");
  const [register, setRegister] = useState("Register 1");

  const open = useMutation({
    mutationFn: () => api.pos.openShift(float || "0", register.trim() || "Register 1"),
    onSuccess: onOpened,
  });

  return (
    <div className="register register--opening">
      <Card title="Open a shift" subtitle="Count the float into the drawer before you begin.">
        {can("pos.shift_manage") ? (
          <div className="tender">
            <div className="tender__figures">
              {open.isError ? <Alert tone="error" title={describe(open.error).message} /> : null}
              <Field label="Opening float" required hint="The cash in the drawer now.">
                {(id) => (
                  <TextInput
                    id={id}
                    numeric
                    inputMode="decimal"
                    autoFocus
                    value={float}
                    placeholder="0.00"
                    onChange={(event) => setFloat(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") open.mutate();
                    }}
                  />
                )}
              </Field>
              <Field label="Register">
                {(id) => (
                  <TextInput
                    id={id}
                    value={register}
                    onChange={(event) => setRegister(event.target.value)}
                  />
                )}
              </Field>
              <Button
                tone="primary"
                size="counter"
                block
                busy={open.isPending}
                onClick={() => open.mutate()}
              >
                Open shift
              </Button>
            </div>
            <div className="tender__keypad">
              <Keypad
                value={float}
                onChange={setFloat}
                completeLabel="Open"
                onComplete={() => open.mutate()}
              />
            </div>
          </div>
        ) : (
          <EmptyState
            title="You may not open a shift."
            detail="Ask the proprietor to grant shift management to your account."
          />
        )}
      </Card>
      {can("pos.shift_approve") ? <AwaitingSignOff /> : null}
    </div>
  );
}

function AwaitingSignOff() {
  const toast = useToast();
  const queries = useQueryClient();
  const currency = useCurrency();
  const shifts = useQuery({ queryKey: ["pos", "shifts"], queryFn: () => api.pos.shifts() });
  const waiting = (shifts.data ?? []).filter((item) => item.state === "submitted");

  const approve = useMutation({
    mutationFn: (shiftId: string) => api.pos.approveShift(shiftId),
    onSuccess: (done) => {
      toast.show("success", `Shift ${done.shift.number} signed off and closed.`);
      void queries.invalidateQueries({ queryKey: ["pos"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <Card
      title="Awaiting sign-off"
      subtitle="Counts with a variance or an abandoned payment."
      flush
    >
      {waiting.length === 0 ? (
        <EmptyState title="No shift is awaiting sign-off." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Shift</th>
              <th scope="col" className="numeric">
                Expected
              </th>
              <th scope="col" className="numeric">
                Counted
              </th>
              <th scope="col" className="numeric">
                Variance
              </th>
              <th scope="col">
                <span className="visually-hidden">Action</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {waiting.map((item) => (
              <tr key={item.id}>
                <td>
                  <div>{item.number}</div>
                  <div className="cell-note">
                    {item.cashier} · {item.register_name} · counted {dateTime(item.submitted_at)}
                  </div>
                  {item.variance_reason ? (
                    <div className="cell-note">{item.variance_reason}</div>
                  ) : null}
                </td>
                <td className="numeric">{money(item.expected_cash, currency)}</td>
                <td className="numeric">{money(item.counted_cash, currency)}</td>
                <td className="numeric">{money(item.variance, currency)}</td>
                <td className="numeric">
                  <Button
                    tone="primary"
                    busy={approve.isPending && approve.variables === item.id}
                    onClick={() => approve.mutate(item.id)}
                  >
                    Sign off
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// -- cash in and out --------------------------------------------------------------------

export function CashMovementDialog({
  expected,
  onClose,
  onDone,
}: {
  expected: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const currency = useCurrency();
  const toast = useToast();
  const [kind, setKind] = useState<"cash_in" | "cash_out">("cash_out");
  const [counter, setCounter] = useState<"bank" | "petty_cash">("bank");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");

  const record = useMutation({
    mutationFn: () => api.pos.cashMovement({ kind, amount, reason, counter }),
    onSuccess: () => {
      toast.show("success", kind === "cash_in" ? "Cash paid in." : "Cash taken out.");
      onDone();
    },
  });

  return (
    <Dialog
      title="Cash in or out"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            size="transactional"
            busy={record.isPending}
            disabled={!amount || !reason.trim()}
            onClick={() => record.mutate()}
          >
            {kind === "cash_in" ? "Pay in" : "Take out"}
          </Button>
        </>
      }
    >
      <div className="tender">
        <div className="tender__figures">
          {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
          <p className="cell-note">The drawer should hold {money(expected, currency)}.</p>
          <RadioGroup
            legend="Movement"
            inline
            value={kind}
            onChange={setKind}
            options={[
              { value: "cash_out", label: "Cash out" },
              { value: "cash_in", label: "Cash in" },
            ]}
          />
          <RadioGroup
            legend={kind === "cash_in" ? "Drawn from" : "Paid to"}
            inline
            value={counter}
            onChange={setCounter}
            options={[
              { value: "bank", label: "Bank" },
              { value: "petty_cash", label: "Petty cash" },
            ]}
          />
          <Field label="Amount" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={amount}
                placeholder="0.00"
                onChange={(event) => setAmount(event.target.value)}
              />
            )}
          </Field>
          <Field label="Reason" required>
            {(id) => (
              <TextInput
                id={id}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={kind === "cash_in" ? "Float top-up" : "Banked at GCB Ring Road"}
              />
            )}
          </Field>
        </div>
        <div className="tender__keypad">
          <Keypad value={amount} onChange={setAmount} compact />
        </div>
      </div>
    </Dialog>
  );
}

// -- closing ----------------------------------------------------------------------------

export function CloseShiftDialog({
  summary,
  onClose,
  onDone,
}: {
  summary: PosShiftSummary;
  onClose: () => void;
  onDone: () => void;
}) {
  const currency = useCurrency();
  const toast = useToast();
  const [pieces, setPieces] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const shiftId = summary.shift.id;

  const counted: Record<string, number> = {};
  for (const denomination of summary.denominations) {
    const value = Number.parseInt(pieces[denomination] ?? "0", 10);
    if (Number.isFinite(value) && value > 0) counted[denomination] = value;
  }
  const key = JSON.stringify(counted);

  const preview = useQuery({
    queryKey: ["pos", "count", shiftId, key],
    queryFn: () => api.pos.previewCount(shiftId, counted),
    placeholderData: (previous) => previous,
  });

  const submit = useMutation({
    mutationFn: () => api.pos.submitCount(shiftId, counted, reason.trim() || null),
    onSuccess: (done) => {
      toast.show(
        "success",
        done.shift.state === "closed"
          ? `Shift ${done.shift.number} closed. The drawer balanced.`
          : `Shift ${done.shift.number} counted and awaiting sign-off.`,
      );
      onDone();
    },
  });

  const variance = preview.data?.variance ?? "0";
  const balanced = isZero(variance);
  const needsReason = !balanced || summary.pending_tenders > 0;

  return (
    <Dialog
      title={`Close shift ${summary.shift.number}`}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            size="transactional"
            busy={submit.isPending}
            disabled={needsReason && !reason.trim()}
            onClick={() => submit.mutate()}
          >
            {needsReason ? "Submit for sign-off" : "Close shift"}
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {submit.isError ? <Alert tone="error" title={describe(submit.error).message} /> : null}
        {summary.pending_tenders > 0 ? (
          <Alert
            tone="warning"
            title={`${summary.pending_tenders} payment is still awaiting confirmation.`}
          >
            Confirm it or mark it not received before closing, or record why it is being abandoned.
          </Alert>
        ) : null}
        <div className="count">
          <div className="count__sheet" role="group" aria-label="Count by denomination">
            {summary.denominations.map((denomination) => (
              <div className="count__row" key={denomination}>
                <span className="count__denomination">{money(denomination, currency)}</span>
                <Stepper
                  value={pieces[denomination] ?? "0"}
                  onChange={(value) => setPieces((all) => ({ ...all, [denomination]: value }))}
                  min={0}
                  size="standard"
                  label={`Pieces of ${money(denomination, currency)}`}
                />
              </div>
            ))}
          </div>
          <div className="count__result">
            <div className="stack gap-4">
              <TotalDisplay
                label="Counted"
                value={money(preview.data?.counted ?? "0", currency)}
                compact
              />
              <TotalDisplay
                label={balanced ? "Balanced" : isNegative(variance) ? "Short" : "Over"}
                value={money(variance, currency)}
                tone={balanced ? "settled" : "due"}
                compact
              />
            </div>
            <div className="stack gap-2">
              <h3 className="section-label">The drawer should hold</h3>
              <Details
                rows={[
                  ["Opening float", money(summary.drawer.opening_float, currency)],
                  ["Cash sales", money(summary.drawer.cash_sales, currency)],
                  ["Cash in", money(summary.drawer.cash_in, currency)],
                  ["Cash out", money(summary.drawer.cash_out, currency)],
                  ["Cash refunds", money(summary.drawer.cash_refunds, currency)],
                  ["Expected", money(summary.drawer.expected, currency)],
                ]}
              />
            </div>
            <div className="stack gap-2">
              <h3 className="section-label">Taken this shift</h3>
              {summary.by_method.length > 0 ? (
                <Details
                  rows={summary.by_method.map((item) => [item.label, money(item.amount, currency)])}
                />
              ) : (
                <p className="cell-note">No sale has been completed.</p>
              )}
            </div>
          </div>
        </div>
        {needsReason ? (
          <Field label="Reason" required hint="Recorded with the count; a supervisor signs it off.">
            {(id) => (
              <TextArea
                id={id}
                rows={2}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="GH₵2.00 change given short on a Mobile Money top-up"
              />
            )}
          </Field>
        ) : null}
      </div>
    </Dialog>
  );
}

// -- parked sales -----------------------------------------------------------------------

export function ParkedDialog({
  onClose,
  onResumed,
}: {
  onClose: () => void;
  onResumed: () => void;
}) {
  const currency = useCurrency();
  const parked = useQuery({ queryKey: ["pos", "parked"], queryFn: () => api.pos.parked() });
  const resume = useMutation({
    mutationFn: (saleId: string) => api.pos.resume(saleId),
    onSuccess: onResumed,
  });

  return (
    <Dialog title="Parked sales" onClose={onClose}>
      <div className="stack gap-4">
        {resume.isError ? <Alert tone="error" title={describe(resume.error).message} /> : null}
        {(parked.data ?? []).length === 0 ? (
          <EmptyState title="No sale is parked." />
        ) : (
          <div className="register__matches">
            {(parked.data ?? []).map((sale) => (
              <button
                key={sale.id}
                type="button"
                className="register__match"
                onClick={() => resume.mutate(sale.id)}
              >
                <span>
                  {sale.parked_label ?? sale.customer ?? "Walk-in customer"}
                  <span className="cell-note"> · {sale.items} item(s)</span>
                </span>
                <span className="numeric">{money(sale.total, currency)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
