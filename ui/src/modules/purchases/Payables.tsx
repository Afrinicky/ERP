/**
 * Payables: what is owed to each supplier, aged by due date, and paying it.
 *
 * The payment dialog follows the design system's tender rules: suggested amounts
 * derived from what is due, a keypad as a secondary path, and one primary action
 * labelled for what it does.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PayableRow, PayablesRegister } from "@/api/types";
import {
  Alert,
  AmountShortcuts,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Keypad,
  RadioGroup,
  Skeleton,
  StatusChip,
  TextInput,
  TotalDisplay,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency } from "@/lib/session";

const BUCKETS: { key: PayableRow["bucket"]; label: string }[] = [
  { key: "current", label: "Current" },
  { key: "1_30", label: "1–30 days" },
  { key: "31_60", label: "31–60 days" },
  { key: "61_90", label: "61–90 days" },
  { key: "over_90", label: "Over 90" },
];

const BUCKET_TONE: Record<PayableRow["bucket"], "neutral" | "info" | "warning" | "error"> = {
  current: "info",
  "1_30": "neutral",
  "31_60": "warning",
  "61_90": "warning",
  over_90: "error",
};

export function Payables({ canPay }: { canPay: boolean }) {
  const currency = useCurrency();
  const [paying, setPaying] = useState<{ supplierId: string; name: string } | null>(null);

  const register = useQuery({
    queryKey: ["purchases", "payables"],
    queryFn: () => api.purchases.payables(),
  });
  const payments = useQuery({
    queryKey: ["purchases", "payments"],
    queryFn: () => api.purchases.payments({ limit: 20 }),
  });

  const suppliers = Object.entries(register.data?.by_supplier ?? {});

  return (
    <div className="stack gap-5">
      <div className="measure-row">
        <TotalDisplay
          label="Owed to suppliers"
          value={money(register.data?.total_outstanding ?? "0", currency)}
          caption={register.data ? `as at ${formatDate(register.data.as_at)}` : undefined}
          tone="due"
          compact
        />
        {BUCKETS.map((bucket) => (
          <TotalDisplay
            key={bucket.key}
            label={bucket.label}
            value={money(register.data?.by_bucket[bucket.key] ?? "0", currency)}
            compact
          />
        ))}
      </div>

      <Card
        title="By supplier"
        subtitle="Reconciles to trade payables in the ledger: every movement on the one is a posting to the other."
        flush
      >
        {register.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : suppliers.length === 0 ? (
          <EmptyState title="Nothing is owed." detail="A posted supplier invoice appears here until it is settled." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Invoices
                  </th>
                  <th style={{ width: "18%" }} className="numeric">
                    Outstanding
                  </th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {suppliers.map(([name, owed]) => {
                  const rows = (register.data?.rows ?? []).filter((row) => row.supplier_name === name);
                  const supplierId = rows[0]?.supplier_id ?? "";
                  return (
                    <tr key={name}>
                      <td>{name}</td>
                      <td className="numeric">{rows.length}</td>
                      <td className="numeric">{money(owed, currency)}</td>
                      <td className="table__actions">
                        {canPay ? (
                          <Button
                            tone="plain"
                            size="compact"
                            onClick={() => setPaying({ supplierId, name })}
                          >
                            Pay
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Invoices outstanding" subtitle="Oldest due date first." flush>
        {(register.data?.rows ?? []).length === 0 ? (
          <EmptyState title="No invoice is outstanding." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th style={{ width: "14%" }}>Their number</th>
                  <th style={{ width: "10%" }}>Dated</th>
                  <th style={{ width: "10%" }}>Due</th>
                  <th style={{ width: "13%" }} className="numeric">
                    Total
                  </th>
                  <th style={{ width: "13%" }} className="numeric">
                    Outstanding
                  </th>
                  <th style={{ width: "12%" }}>Age</th>
                </tr>
              </thead>
              <tbody>
                {(register.data?.rows ?? []).map((row) => (
                  <tr key={row.invoice_id}>
                    <td>{row.supplier_name}</td>
                    <td className="numeric">{row.supplier_reference}</td>
                    <td>{formatDate(row.invoiced_on)}</td>
                    <td>{formatDate(row.due_on)}</td>
                    <td className="numeric">{money(row.total, currency)}</td>
                    <td className="numeric">{money(row.outstanding, currency)}</td>
                    <td>
                      <StatusChip tone={BUCKET_TONE[row.bucket]}>
                        {row.days_overdue ? `${row.days_overdue} days over` : "Current"}
                      </StatusChip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent payments" subtitle="Each reduced the payable and the cash, and posted no expense." flush>
        {payments.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={2} />
          </div>
        ) : (payments.data ?? []).length === 0 ? (
          <EmptyState title="No payment recorded yet." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "14%" }}>Number</th>
                  <th>Supplier</th>
                  <th style={{ width: "10%" }}>Paid</th>
                  <th style={{ width: "12%" }}>Method</th>
                  <th style={{ width: "16%" }}>Reference</th>
                  <th style={{ width: "13%" }} className="numeric">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {(payments.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td className="numeric">{item.number ?? "—"}</td>
                    <td>{item.supplier_name ?? "—"}</td>
                    <td>{formatDate(item.paid_on)}</td>
                    <td className="caption">{item.method.replace("_", " ")}</td>
                    <td className="caption">{item.reference ?? ""}</td>
                    <td className="numeric">{money(item.amount, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {paying ? (
        <PaymentDialog supplierId={paying.supplierId} name={paying.name} onClose={() => setPaying(null)} />
      ) : null}
    </div>
  );
}

function PaymentDialog({
  supplierId,
  name,
  onClose,
}: {
  supplierId: string;
  name: string;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const currency = useCurrency();
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<"cash" | "bank" | "mobile_money">("bank");
  const [reference, setReference] = useState("");

  const due = useQuery({
    queryKey: ["purchases", "payables", supplierId],
    queryFn: () => api.purchases.payables({ supplier_id: supplierId }),
  });

  const pay = useMutation({
    mutationFn: () =>
      api.purchases.pay({ supplier_id: supplierId, amount, method, reference: reference || null }),
    onSuccess: (payment) => {
      toast.show("success", `${payment.number} recorded for ${name}.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      onClose();
    },
  });

  const register: PayablesRegister | undefined = due.data;
  const ready = Number(amount) > 0;
  const label = `Pay ${amount ? money(amount, currency) : ""} by ${method.replace("_", " ")}`.replace(
    "  ",
    " ",
  );

  return (
    <Dialog
      size="lg"
      title={`Pay ${name}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => pay.mutate()}
            busy={pay.isPending}
            disabled={!ready}
          >
            {label}
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {pay.isError ? <Alert tone="error" title={describe(pay.error).message} /> : null}
        <div className="tender">
          <div className="tender__figures">
            <TotalDisplay
              label="Due"
              value={money(register?.total_outstanding ?? "0", currency)}
              caption={`${register?.rows.length ?? 0} invoice(s), oldest settled first`}
              tone="due"
            />
            <Field label="Amount" required hint="Typed, or taken from the keypad or a suggestion.">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                  placeholder="0.00"
                />
              )}
            </Field>
            <AmountShortcuts
              suggestions={register?.suggested_amounts ?? []}
              onChoose={setAmount}
              format={(value) => money(value, currency)}
            />
            <RadioGroup
              legend="Paid from"
              inline
              value={method}
              onChange={setMethod}
              options={[
                { value: "bank", label: "Bank" },
                { value: "mobile_money", label: "Mobile Money" },
                { value: "cash", label: "Cash" },
              ]}
            />
            <Field label="Reference" optional hint="The bank or Mobile Money transaction reference.">
              {(id) => (
                <TextInput
                  id={id}
                  value={reference}
                  onChange={(event) => setReference(event.target.value)}
                  placeholder="GCB-TRF-558812"
                />
              )}
            </Field>
          </div>
          <div className="tender__keypad">
            <Keypad value={amount} onChange={setAmount} compact completeLabel="Exact" onComplete={() => {
              const exact = register?.suggested_amounts.find((item) => item.exact);
              if (exact) setAmount(exact.amount);
            }} />
          </div>
        </div>
      </div>
    </Dialog>
  );
}
