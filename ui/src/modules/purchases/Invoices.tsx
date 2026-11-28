/**
 * Supplier invoices: recorded as the supplier printed them, matched three ways, held
 * while a variance awaits a decision, then posted to payables.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { InvoiceLineInput, MatchVariance, PurchaseOrder, SupplierInvoice } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Details,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, humanise, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

type Tone = "neutral" | "info" | "ok" | "warning" | "error";

const INVOICE_TONE: Record<SupplierInvoice["state"], Tone> = {
  draft: "neutral",
  variance: "warning",
  matched: "info",
  posted: "ok",
  partially_paid: "info",
  paid: "ok",
  cancelled: "neutral",
};

export function Invoices({
  canManage,
  canApproveVariance,
}: {
  canManage: boolean;
  canApproveVariance: boolean;
}) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [deciding, setDeciding] = useState<MatchVariance | null>(null);

  const invoices = useQuery({
    queryKey: ["purchases", "invoices", state],
    queryFn: () => api.purchases.invoices({ state: state || undefined, limit: 50 }),
  });

  const post = useMutation({
    mutationFn: (invoiceId: string) => api.purchases.postInvoice(invoiceId),
    onSuccess: (updated) => {
      toast.show("success", `${updated.supplier_reference} posted as ${updated.number}.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      void client.invalidateQueries({ queryKey: ["sales"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  const open = (invoices.data ?? []).find((item) => item.id === openId) ?? null;

  return (
    <div className="stack gap-5">
      <Card
        title={`${invoices.data?.length ?? 0} supplier invoices`}
        subtitle="Matched against the order and the receipts. Posted only once every variance is answered."
        actions={
          <div className="row gap-2">
            <Select
              aria-label="Filter by state"
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="">Every state</option>
              <option value="variance">Awaiting a decision</option>
              <option value="matched">Matched</option>
              <option value="posted">Posted</option>
              <option value="partially_paid">Partially paid</option>
              <option value="paid">Paid</option>
              <option value="draft">Draft</option>
              <option value="cancelled">Cancelled</option>
            </Select>
            {canManage ? (
              <Button tone="primary" onClick={() => setRecording(true)}>
                Record an invoice
              </Button>
            ) : null}
          </div>
        }
        flush
      >
        {invoices.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (invoices.data ?? []).length === 0 ? (
          <EmptyState title="No supplier invoices yet." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "14%" }}>Their number</th>
                  <th>Supplier</th>
                  <th style={{ width: "12%" }}>Order</th>
                  <th style={{ width: "10%" }}>Dated</th>
                  <th style={{ width: "10%" }}>Due</th>
                  <th style={{ width: "13%" }} className="numeric">
                    Total
                  </th>
                  <th style={{ width: "14%" }}>Standing</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(invoices.data ?? []).map((item) => (
                  <tr key={item.id} className={item.id === openId ? "table__row--on" : undefined}>
                    <td className="numeric">
                      {item.supplier_reference}
                      {item.number ? <span className="cell-note">{item.number}</span> : null}
                    </td>
                    <td>{item.supplier_name ?? "—"}</td>
                    <td className="numeric">{item.purchase_order_number ?? "—"}</td>
                    <td>{formatDate(item.invoiced_on)}</td>
                    <td>{formatDate(item.due_on)}</td>
                    <td className="numeric">{money(item.base_total, currency)}</td>
                    <td>
                      <StatusChip tone={INVOICE_TONE[item.state]}>
                        {item.state === "variance"
                          ? `${item.variances.filter((v) => v.state === "pending").length} to decide`
                          : humanise(item.state)}
                      </StatusChip>
                    </td>
                    <td className="table__actions">
                      {canManage && item.state === "matched" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          busy={post.isPending && post.variables === item.id}
                          onClick={() => post.mutate(item.id)}
                        >
                          Post
                        </Button>
                      ) : null}
                      <Button
                        tone="plain"
                        size="compact"
                        onClick={() => setOpenId(item.id === openId ? null : item.id)}
                      >
                        {item.id === openId ? "Close" : "Open"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {open ? (
        <InvoiceDetail invoice={open} canApproveVariance={canApproveVariance} onDecide={setDeciding} />
      ) : null}
      {recording ? <RecordDialog onClose={() => setRecording(false)} /> : null}
      {deciding ? <DecideDialog variance={deciding} onClose={() => setDeciding(null)} /> : null}
    </div>
  );
}

function InvoiceDetail({
  invoice,
  canApproveVariance,
  onDecide,
}: {
  invoice: SupplierInvoice;
  canApproveVariance: boolean;
  onDecide: (variance: MatchVariance) => void;
}) {
  const currency = useCurrency();
  const pending = invoice.variances.filter((item) => item.state === "pending");
  return (
    <div className="stack gap-5">
      {invoice.variances.length > 0 ? (
        <Card
          title="Three-way match"
          subtitle={
            pending.length
              ? "A difference between order, receipt and invoice. The invoice posts once each is answered."
              : "Every variance has been answered."
          }
          flush
        >
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "12%" }}>Kind</th>
                  <th className="numeric">Expected</th>
                  <th className="numeric">Invoiced</th>
                  <th className="numeric">At stake</th>
                  <th style={{ width: "14%" }}>Standing</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {invoice.variances.map((item) => (
                  <tr key={item.id}>
                    <td>{humanise(item.kind)}</td>
                    <td className="numeric">
                      {item.kind === "price" ? money(item.expected, currency) : quantity(item.expected)}
                    </td>
                    <td className="numeric">
                      {item.kind === "price" ? money(item.actual, currency) : quantity(item.actual)}
                    </td>
                    <td className="numeric">{money(item.amount_at_stake, currency)}</td>
                    <td>
                      <StatusChip
                        tone={
                          item.state === "approved" ? "ok" : item.state === "rejected" ? "error" : "warning"
                        }
                      >
                        {humanise(item.state)}
                      </StatusChip>
                    </td>
                    <td className="caption">{item.reason ?? ""}</td>
                    <td className="table__actions">
                      {canApproveVariance && item.state === "pending" ? (
                        <Button tone="plain" size="compact" onClick={() => onDecide(item)}>
                          Decide
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <Card
        title={`${invoice.supplier_name ?? "Supplier"} · ${invoice.supplier_reference}`}
        subtitle={`Against ${invoice.purchase_order_number ?? "the order"} · ${invoice.currency_code}`}
        flush
      >
        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: "4%" }}>#</th>
                <th>Item</th>
                <th className="numeric">Quantity</th>
                <th className="numeric">Unit cost</th>
                <th className="numeric">Amount</th>
                <th className="numeric">Tax</th>
              </tr>
            </thead>
            <tbody>
              {invoice.lines.map((line) => (
                <tr key={line.id}>
                  <td className="numeric">{line.position}</td>
                  <td>{line.description}</td>
                  <td className="numeric">{quantity(line.quantity)}</td>
                  <td className="numeric">{money(line.unit_cost, currency)}</td>
                  <td className="numeric">{money(line.line_total, currency)}</td>
                  <td className="numeric">{money(line.recoverable_tax, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card__foot">
          <Details
            rows={[
              ["Net", money(invoice.net_total, currency)],
              ["Recoverable tax", money(invoice.recoverable_tax_total, currency)],
              ["Total", money(invoice.total, currency)],
              ["Paid", money(invoice.amount_paid, currency)],
              ["Credited", money(invoice.amount_credited, currency)],
            ]}
          />
        </div>
      </Card>
    </div>
  );
}

interface LineEntry extends InvoiceLineInput {
  description: string;
}

function RecordDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [orderId, setOrderId] = useState("");
  const [reference, setReference] = useState("");
  const [invoicedOn, setInvoicedOn] = useState("");
  const [entries, setEntries] = useState<LineEntry[]>([]);

  const orders = useQuery({
    queryKey: ["purchases", "orders", "invoiceable"],
    queryFn: async () => {
      const states = ["issued", "partially_received", "received", "closed"];
      const found = await Promise.all(states.map((state) => api.purchases.orders({ state, limit: 100 })));
      return found.flat();
    },
  });

  const choose = (id: string) => {
    setOrderId(id);
    const order = (orders.data ?? []).find((item: PurchaseOrder) => item.id === id);
    setEntries(
      (order?.lines ?? [])
        .filter((line) => Number(line.quantity_received) - Number(line.quantity_invoiced) > 0)
        .map((line) => ({
          purchase_order_line_id: line.id,
          description: line.description,
          quantity: String(Number(line.quantity_received) - Number(line.quantity_invoiced)),
          unit_cost: line.net_unit_cost,
          recoverable_tax: "0",
          non_recoverable_tax: "0",
        })),
    );
  };

  const record = useMutation({
    mutationFn: () =>
      api.purchases.recordInvoice({
        purchase_order_id: orderId,
        supplier_reference: reference,
        invoiced_on: invoicedOn,
        lines: entries
          .filter((entry) => Number(entry.quantity) > 0)
          .map(({ description: _description, ...entry }) => entry),
      }),
    onSuccess: (invoice) => {
      toast.show(
        invoice.state === "variance" ? "info" : "success",
        invoice.state === "variance"
          ? `${invoice.supplier_reference} recorded with ${invoice.variances.length} variance(s) to decide.`
          : `${invoice.supplier_reference} recorded and matched.`,
      );
      void client.invalidateQueries({ queryKey: ["purchases"] });
      onClose();
    },
  });

  const amend = (index: number, patch: Partial<LineEntry>) =>
    setEntries((rows) => rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));

  const ready = Boolean(orderId) && reference.trim().length > 0 && invoicedOn.length > 0 && entries.length > 0;

  return (
    <Dialog
      size="lg"
      title="Record a supplier invoice"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => record.mutate()} busy={record.isPending} disabled={!ready}>
            Record and match
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
        <div className="form-grid">
          <Field label="Purchase order" required>
            {(id) => (
              <Select id={id} value={orderId} onChange={(event) => choose(event.target.value)}>
                <option value="">Choose an order</option>
                {(orders.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.number} · {item.supplier_name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Their invoice number" required>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="GL-INV-7731"
              />
            )}
          </Field>
          <Field label="Dated" required hint="Due date follows the supplier's terms.">
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={invoicedOn}
                onChange={(event) => setInvoicedOn(event.target.value)}
              />
            )}
          </Field>
        </div>

        {orderId && entries.length === 0 ? (
          <Alert tone="info" title="Nothing received on this order is still to be invoiced." />
        ) : null}
        {entries.length > 0 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: "14%" }} className="numeric">
                    Quantity
                  </th>
                  <th style={{ width: "16%" }} className="numeric">
                    Unit cost
                  </th>
                  <th style={{ width: "16%" }} className="numeric">
                    Recoverable tax
                  </th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry, index) => (
                  <tr key={entry.purchase_order_line_id}>
                    <td>{entry.description}</td>
                    <td className="numeric">
                      <TextInput
                        numeric
                        inputMode="decimal"
                        aria-label={`Quantity for ${entry.description}`}
                        value={entry.quantity}
                        onChange={(event) => amend(index, { quantity: event.target.value })}
                      />
                    </td>
                    <td className="numeric">
                      <TextInput
                        numeric
                        inputMode="decimal"
                        aria-label={`Unit cost for ${entry.description}`}
                        value={entry.unit_cost}
                        onChange={(event) => amend(index, { unit_cost: event.target.value })}
                      />
                    </td>
                    <td className="numeric">
                      <TextInput
                        numeric
                        inputMode="decimal"
                        aria-label={`Tax for ${entry.description}`}
                        value={entry.recoverable_tax ?? "0"}
                        onChange={(event) => amend(index, { recoverable_tax: event.target.value })}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

function DecideDialog({ variance, onClose }: { variance: MatchVariance; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const currency = useCurrency();
  const [approve, setApprove] = useState(true);
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: () => api.purchases.decideVariance(variance.id, { approve, reason }),
    onSuccess: (invoice) => {
      toast.show("success", `Variance ${approve ? "approved" : "rejected"}; invoice ${humanise(invoice.state)}.`);
      void client.invalidateQueries({ queryKey: ["purchases"] });
      onClose();
    },
  });
  return (
    <Dialog
      size="sm"
      title={`${humanise(variance.kind)} variance`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone={approve ? "primary" : "destructive"}
            onClick={() => decide.mutate()}
            busy={decide.isPending}
            disabled={!reason.trim()}
          >
            {approve ? `Approve ${money(variance.amount_at_stake, currency)}` : "Reject"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {decide.isError ? <Alert tone="error" title={describe(decide.error).message} /> : null}
        <Details
          rows={[
            ["Expected", variance.kind === "price" ? money(variance.expected, currency) : quantity(variance.expected)],
            ["Invoiced", variance.kind === "price" ? money(variance.actual, currency) : quantity(variance.actual)],
            ["Amount at stake", money(variance.amount_at_stake, currency)],
          ]}
        />
        <Field label="Decision">
          {(id) => (
            <Select
              id={id}
              value={approve ? "approve" : "reject"}
              onChange={(event) => setApprove(event.target.value === "approve")}
            >
              <option value="approve">Approve: pay the supplier's figure</option>
              <option value="reject">Reject: send the invoice back for correction</option>
            </Select>
          )}
        </Field>
        <Field label="Reason" required hint="Recorded with the decision.">
          {(id) => (
            <TextArea id={id} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
