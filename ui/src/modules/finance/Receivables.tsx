/**
 * Receivables: what customers owe, what they have paid, what they have paid in advance,
 * and the work of collecting the rest.
 *
 * An invoice earns revenue; a receipt settles it; a deposit is held as owed back until its
 * condition is met. The screen shows the same distinction in its words.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { CustomerDeposit, SalesInvoice } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Dialog,
  Details,
  EmptyState,
  Field,
  RadioGroup,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { isZero } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { date as formatDate, money, quantity } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

type View = "invoices" | "collections" | "ageing" | "receipts" | "deposits";

const STATE_TONE: Record<SalesInvoice["state"], Tone> = {
  open: "info",
  part_settled: "warning",
  settled: "ok",
  written_off: "neutral",
};

export function Receivables() {
  const { can } = useSession();
  const [view, setView] = useState<View>("invoices");
  const [issuing, setIssuing] = useState(false);
  const [receiving, setReceiving] = useState(false);
  const [depositing, setDepositing] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [statementFor, setStatementFor] = useState<{ id: string; name: string } | null>(null);

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Tabs
          items={[
            { id: "invoices", label: "Invoices" },
            { id: "collections", label: "Collections" },
            { id: "ageing", label: "Ageing" },
            { id: "receipts", label: "Receipts" },
            { id: "deposits", label: "Deposits" },
          ]}
          active={view}
          onChange={setView}
        />
        <div className="row gap-2">
          {can("receipt.manage") ? (
            <>
              <Button onClick={() => setDepositing(true)}>Take a deposit</Button>
              <Button onClick={() => setReceiving(true)}>Record a receipt</Button>
            </>
          ) : null}
          {can("sales_invoice.issue") ? (
            <Button tone="primary" onClick={() => setIssuing(true)}>
              Issue an invoice
            </Button>
          ) : null}
        </div>
      </div>

      {view === "invoices" ? <InvoiceList onOpen={setOpenId} /> : null}
      {view === "collections" ? <Collections onOpen={setOpenId} /> : null}
      {view === "ageing" ? <Ageing onStatement={setStatementFor} /> : null}
      {view === "receipts" ? <ReceiptList /> : null}
      {view === "deposits" ? <DepositList /> : null}

      {issuing ? (
        <IssueDialog
          onClose={() => setIssuing(false)}
          onIssued={(id) => {
            setIssuing(false);
            setOpenId(id);
          }}
        />
      ) : null}
      {receiving ? <ReceiptDialog onClose={() => setReceiving(false)} /> : null}
      {depositing ? <DepositDialog onClose={() => setDepositing(false)} /> : null}
      {openId ? <InvoiceDialog invoiceId={openId} onClose={() => setOpenId(null)} /> : null}
      {statementFor ? (
        <StatementDialog customer={statementFor} onClose={() => setStatementFor(null)} />
      ) : null}
    </div>
  );
}

// -- lists --------------------------------------------------------------------------------

function InvoiceList({ onOpen }: { onOpen: (id: string) => void }) {
  const currency = useCurrency();
  const [openOnly, setOpenOnly] = useState(true);
  const invoices = useQuery({
    queryKey: ["receivables", "invoices", openOnly],
    queryFn: () => api.receivables.invoices({ open_only: openOnly }),
  });
  return (
    <Card
      title="Invoices"
      actions={<Checkbox label="Outstanding only" checked={openOnly} onChange={setOpenOnly} />}
      flush
    >
      {invoices.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={4} />
        </div>
      ) : (invoices.data ?? []).length === 0 ? (
        <EmptyState title={openOnly ? "Nothing is outstanding." : "No invoice has been issued."} />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Customer</th>
              <th>Due</th>
              <th>Status</th>
              <th className="numeric">Total</th>
              <th className="numeric">Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {(invoices.data ?? []).map((item) => (
              <tr key={item.id}>
                <td>
                  <button type="button" className="text-button" onClick={() => onOpen(item.id)}>
                    {item.number}
                  </button>
                  <div className="cell-note">
                    {item.origin === "pos_sale" ? "Register sale on account" : item.order_number}
                  </div>
                </td>
                <td>{item.customer}</td>
                <td>
                  {formatDate(item.due_on)}
                  {item.days_overdue > 0 ? (
                    <div className="cell-note">{item.days_overdue} days overdue</div>
                  ) : null}
                </td>
                <td>
                  <StatusChip tone={STATE_TONE[item.state]}>{item.state_label}</StatusChip>
                  {item.disputed ? <div className="cell-note">Disputed</div> : null}
                </td>
                <td className="numeric">{money(item.total, currency)}</td>
                <td className="numeric">{money(item.outstanding, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Collections({ onOpen }: { onOpen: (id: string) => void }) {
  const currency = useCurrency();
  const list = useQuery({
    queryKey: ["receivables", "worklist"],
    queryFn: api.receivables.worklist,
  });
  return (
    <Card title="Collection worklist" subtitle="Overdue invoices, most overdue first." flush>
      {(list.data ?? []).length === 0 ? (
        <EmptyState title="Nothing is overdue." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Customer</th>
              <th className="numeric">Days overdue</th>
              <th>Latest</th>
              <th>Reminder</th>
              <th className="numeric">Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((item) => (
              <tr key={item.invoice.id}>
                <td>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => onOpen(item.invoice.id)}
                  >
                    {item.invoice.number}
                  </button>
                </td>
                <td>{item.invoice.customer}</td>
                <td className="numeric">{item.days_overdue}</td>
                <td className="table__cell--wrap">
                  {item.latest_kind ? `${item.latest_kind}: ${item.latest_note}` : "No contact yet"}
                  {item.promised_on ? (
                    <div className="cell-note">Promised by {formatDate(item.promised_on)}</div>
                  ) : null}
                </td>
                <td>
                  {item.invoice.disputed ? (
                    <StatusChip tone="neutral">Held: disputed</StatusChip>
                  ) : item.reminder_due ? (
                    <StatusChip tone="warning">Due</StatusChip>
                  ) : (
                    <StatusChip tone="ok">Promised</StatusChip>
                  )}
                </td>
                <td className="numeric">{money(item.outstanding, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Ageing({
  onStatement,
}: {
  onStatement: (customer: { id: string; name: string }) => void;
}) {
  const currency = useCurrency();
  const rows = useQuery({ queryKey: ["receivables", "ageing"], queryFn: api.receivables.ageing });
  const cell = (value: string) => (isZero(value) ? "—" : money(value, currency));
  return (
    <Card
      title="Ageing"
      subtitle="By due date. Unapplied credit is shown beside, not netted."
      flush
    >
      {(rows.data ?? []).length === 0 ? (
        <EmptyState title="No customer owes anything." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Customer</th>
                <th className="numeric">Not yet due</th>
                <th className="numeric">1–30</th>
                <th className="numeric">31–60</th>
                <th className="numeric">61–90</th>
                <th className="numeric">Over 90</th>
                <th className="numeric">Total</th>
                <th className="numeric">Unapplied</th>
              </tr>
            </thead>
            <tbody>
              {(rows.data ?? []).map((row) => (
                <tr key={row.customer_id}>
                  <td>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => onStatement({ id: row.customer_id, name: row.customer })}
                    >
                      {row.customer}
                    </button>
                    {row.credit_limit ? (
                      <div className="cell-note">Limit {money(row.credit_limit, currency)}</div>
                    ) : null}
                    {row.on_hold ? <div className="cell-note">Credit on hold</div> : null}
                  </td>
                  <td className="numeric">{cell(row.current)}</td>
                  <td className="numeric">{cell(row.days_1_30)}</td>
                  <td className="numeric">{cell(row.days_31_60)}</td>
                  <td className="numeric">{cell(row.days_61_90)}</td>
                  <td className="numeric">{cell(row.over_90)}</td>
                  <td className="numeric">{money(row.total, currency)}</td>
                  <td className="numeric">{cell(row.unapplied)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ReceiptList() {
  const currency = useCurrency();
  const receipts = useQuery({
    queryKey: ["receivables", "receipts"],
    queryFn: () => api.receivables.receipts(),
  });
  return (
    <Card
      title="Receipts"
      subtitle="Money received from customers. Not revenue: it settles what was invoiced."
      flush
    >
      {(receipts.data ?? []).length === 0 ? (
        <EmptyState title="No receipt has been recorded." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Receipt</th>
              <th>Customer</th>
              <th>Into</th>
              <th className="numeric">Amount</th>
              <th className="numeric">Unapplied</th>
            </tr>
          </thead>
          <tbody>
            {(receipts.data ?? []).map((item) => (
              <tr key={item.id}>
                <td>
                  {item.number}
                  <div className="cell-note">
                    {formatDate(item.received_on)}
                    {item.reference ? ` · ${item.reference}` : ""}
                  </div>
                </td>
                <td>{item.customer}</td>
                <td>{item.account}</td>
                <td className="numeric">{money(item.amount, currency)}</td>
                <td className="numeric">
                  {isZero(item.unapplied) ? "—" : money(item.unapplied, currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function DepositList() {
  const currency = useCurrency();
  const deposits = useQuery({
    queryKey: ["receivables", "deposits"],
    queryFn: () => api.receivables.deposits(),
  });
  return (
    <Card
      title="Deposits"
      subtitle="Advances held as owed to the customer until their condition is met. Never revenue."
      flush
    >
      {(deposits.data ?? []).length === 0 ? (
        <EmptyState title="No deposit is held." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Deposit</th>
              <th>Customer</th>
              <th>Condition</th>
              <th>Status</th>
              <th className="numeric">Amount</th>
              <th className="numeric">Still held</th>
            </tr>
          </thead>
          <tbody>
            {(deposits.data ?? []).map((item) => (
              <tr key={item.id}>
                <td>
                  {item.number}
                  <div className="cell-note">
                    {formatDate(item.received_on)}
                    {item.order_number ? ` · ${item.order_number}` : ""}
                  </div>
                </td>
                <td>{item.customer}</td>
                <td>{item.condition_label}</td>
                <td>
                  <StatusChip tone={depositTone(item)}>{item.state.replace("_", " ")}</StatusChip>
                </td>
                <td className="numeric">{money(item.amount, currency)}</td>
                <td className="numeric">{money(item.held, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function depositTone(item: CustomerDeposit): Tone {
  return item.state === "applied" ? "ok" : item.state === "refunded" ? "neutral" : "info";
}

// -- dialogs ------------------------------------------------------------------------------

function useAccounts() {
  return useQuery({ queryKey: ["treasury", "accounts"], queryFn: () => api.treasury.accounts() });
}

function useCustomers(term: string) {
  return useQuery({
    queryKey: ["receivables", "customers", term],
    queryFn: () => api.parties.search({ term, kind: "customer", limit: 8 }),
    enabled: term.trim().length >= 2,
  });
}

function CustomerField({
  value,
  onChoose,
}: {
  value: { id: string; name: string } | null;
  onChoose: (customer: { id: string; name: string }) => void;
}) {
  const [term, setTerm] = useState("");
  const found = useCustomers(term);
  if (value) {
    return (
      <Field label="Customer" required>
        {(id) => (
          <div className="row gap-2" id={id}>
            <strong>{value.name}</strong>
            <Button size="compact" tone="plain" onClick={() => onChoose({ id: "", name: "" })}>
              Change
            </Button>
          </div>
        )}
      </Field>
    );
  }
  return (
    <div className="stack gap-2">
      <Field label="Customer" required>
        {(id) => (
          <TextInput
            id={id}
            autoFocus
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Name or code"
          />
        )}
      </Field>
      <div className="register__matches">
        {(found.data?.items ?? []).map((party) => (
          <button
            key={party.id}
            type="button"
            className="register__match"
            onClick={() => onChoose({ id: party.id, name: party.name })}
          >
            <span>{party.name}</span>
            <span className="cell-note">{party.code}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ReceiptDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const accounts = useAccounts();
  const [customer, setCustomer] = useState<{ id: string; name: string } | null>(null);
  const [amount, setAmount] = useState("");
  const [account, setAccount] = useState("");
  const [reference, setReference] = useState("");
  const chosen = customer && customer.id ? customer : null;

  const save = useMutation({
    mutationFn: () =>
      api.receivables.receive({
        customer_id: chosen!.id,
        amount,
        financial_account_id: account || null,
        reference: reference || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["receivables"] });
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.number}: allocated to the oldest invoices first.`);
      onClose();
    },
  });

  return (
    <Dialog
      title="Record a receipt"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={save.isPending}
            disabled={!chosen || !amount}
            onClick={() => save.mutate()}
          >
            Record receipt
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <CustomerField value={chosen} onChoose={(next) => setCustomer(next.id ? next : null)} />
        <div className="form-grid">
          <Field label="Amount received" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Into" required>
            {(id) => (
              <Select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">Main bank account</option>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Reference" optional>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="MoMo 26112012345"
              />
            )}
          </Field>
        </div>
        <p className="cell-note">
          Settles the customer's oldest invoices first. Anything over stays as their credit.
        </p>
      </div>
    </Dialog>
  );
}

function DepositDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const accounts = useAccounts();
  const orders = useQuery({
    queryKey: ["orders", "open"],
    queryFn: () => api.orders.list(undefined, 100),
  });
  const [customer, setCustomer] = useState<{ id: string; name: string } | null>(null);
  const [amount, setAmount] = useState("");
  const [account, setAccount] = useState("");
  const [order, setOrder] = useState("");
  const [condition, setCondition] = useState<"on_invoice" | "on_delivery">("on_invoice");
  const chosen = customer && customer.id ? customer : null;
  const theirs = (orders.data ?? []).filter((item) => item.customer_fk === chosen?.id);

  const save = useMutation({
    mutationFn: () =>
      api.receivables.takeDeposit({
        customer_id: chosen!.id,
        amount,
        financial_account_id: account || null,
        sales_order_id: order || null,
        condition,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["receivables"] });
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.number}: held in customer deposits.`);
      onClose();
    },
  });

  return (
    <Dialog
      title="Take a deposit"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={save.isPending}
            disabled={!chosen || !amount}
            onClick={() => save.mutate()}
          >
            Take deposit
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <CustomerField value={chosen} onChoose={(next) => setCustomer(next.id ? next : null)} />
        <div className="form-grid">
          <Field label="Amount" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Into" required>
            {(id) => (
              <Select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">Main bank account</option>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Against order" optional>
            {(id) => (
              <Select
                id={id}
                value={order}
                onChange={(e) => setOrder(e.target.value)}
                disabled={!chosen}
              >
                <option value="">On account</option>
                {theirs.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.number}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <RadioGroup
          legend="Applied to the invoice"
          inline
          value={condition}
          onChange={setCondition}
          options={[
            { value: "on_invoice", label: "When invoiced" },
            { value: "on_delivery", label: "When delivered and invoiced" },
          ]}
        />
      </div>
    </Dialog>
  );
}

function IssueDialog({
  onClose,
  onIssued,
}: {
  onClose: () => void;
  onIssued: (id: string) => void;
}) {
  const queries = useQueryClient();
  const currency = useCurrency();
  const orders = useQuery({
    queryKey: ["orders", "open"],
    queryFn: () => api.orders.list(undefined, 100),
  });
  const [orderId, setOrderId] = useState("");
  const [basis, setBasis] = useState<"delivered" | "ordered">("delivered");
  const [override, setOverride] = useState("");
  const lines = useQuery({
    queryKey: ["receivables", "invoiceable", orderId],
    queryFn: () => api.receivables.invoiceable(orderId),
    enabled: Boolean(orderId),
  });

  const issue = useMutation({
    mutationFn: () =>
      api.receivables.issueInvoice({
        sales_order_id: orderId,
        basis,
        credit_override_reason: override || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["receivables"] });
      onIssued(made.id);
    },
  });
  const overLimit = issue.isError && describe(issue.error).message.includes("credit");

  return (
    <Dialog
      title="Issue an invoice"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={issue.isPending}
            disabled={!orderId}
            onClick={() => issue.mutate()}
          >
            Issue invoice
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {issue.isError ? <Alert tone="error" title={describe(issue.error).message} /> : null}
        <div className="form-grid">
          <Field label="Sales order" required>
            {(id) => (
              <Select id={id} value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                <option value="">Choose an order</option>
                {(orders.data ?? [])
                  .filter((item) => item.state !== "cancelled")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.number} · {item.customer_name}
                    </option>
                  ))}
              </Select>
            )}
          </Field>
          <RadioGroup
            legend="Bill"
            inline
            value={basis}
            onChange={setBasis}
            options={[
              { value: "delivered", label: "What was delivered" },
              { value: "ordered", label: "Ahead of delivery" },
            ]}
          />
        </div>
        {lines.data ? (
          <table className="table">
            <thead>
              <tr>
                <th>Line</th>
                <th className="numeric">Ordered</th>
                <th className="numeric">Delivered</th>
                <th className="numeric">Invoiced</th>
                <th className="numeric">Unit price</th>
              </tr>
            </thead>
            <tbody>
              {lines.data.map((line) => (
                <tr key={line.line_id}>
                  <td>{line.description}</td>
                  <td className="numeric">{quantity(line.ordered)}</td>
                  <td className="numeric">{quantity(line.delivered)}</td>
                  <td className="numeric">{quantity(line.invoiced)}</td>
                  <td className="numeric">{money(line.unit_price, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {overLimit ? (
          <Field
            label="Reason for exceeding the credit limit"
            required
            hint="Needs payment approval."
          >
            {(id) => (
              <TextInput id={id} value={override} onChange={(e) => setOverride(e.target.value)} />
            )}
          </Field>
        ) : null}
      </div>
    </Dialog>
  );
}

function InvoiceDialog({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const { can } = useSession();
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const invoice = useQuery({
    queryKey: ["receivables", "invoice", invoiceId],
    queryFn: () => api.receivables.invoice(invoiceId),
  });
  const [action, setAction] = useState<"none" | "note" | "credit" | "writeoff">("none");
  const [text, setText] = useState("");
  const [kind, setKind] = useState<"contact" | "promise" | "dispute" | "resolved">("contact");
  const [promisedOn, setPromisedOn] = useState("");
  const [creditQuantities, setCreditQuantities] = useState<Record<string, string>>({});

  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["receivables"] });
    setAction("none");
    setText("");
  };
  const note = useMutation({
    mutationFn: () =>
      api.receivables.addNote(invoice.data!.customer_id, {
        kind,
        note: text,
        invoice_id: invoiceId,
        promised_on: kind === "promise" ? promisedOn || null : null,
      }),
    onSuccess: () => {
      refresh();
      toast.show("success", "Recorded.");
    },
  });
  const credit = useMutation({
    mutationFn: () =>
      api.receivables.credit(invoiceId, {
        lines: Object.entries(creditQuantities)
          .filter(([, value]) => value && !isZero(value))
          .map(([invoice_line_id, value]) => ({ invoice_line_id, quantity: value })),
        reason: text,
      }),
    onSuccess: (made) => {
      refresh();
      toast.show("success", `Credit note ${made.number} issued.`);
    },
  });
  const writeOff = useMutation({
    mutationFn: () => api.receivables.writeOff(invoiceId, text),
    onSuccess: () => {
      refresh();
      toast.show("success", "Written off.");
    },
  });
  const failure = note.error ?? credit.error ?? writeOff.error;
  const data = invoice.data;

  return (
    <Dialog title={data ? `Invoice ${data.number}` : "Invoice"} size="lg" onClose={onClose}>
      {!data ? (
        <Skeleton rows={6} />
      ) : (
        <div className="stack gap-5">
          {failure ? <Alert tone="error" title={describe(failure).message} /> : null}
          <Details
            rows={[
              ["Customer", data.customer],
              ["Issued", formatDate(data.issued_on)],
              [
                "Due",
                `${formatDate(data.due_on)}${data.days_overdue ? ` · ${data.days_overdue} days overdue` : ""}`,
              ],
              ["Status", `${data.state_label}${data.disputed ? " · disputed" : ""}`],
              ["Total", money(data.total, currency)],
              ["Received", money(data.amount_received, currency)],
              [
                "Credited",
                isZero(data.amount_credited) ? null : money(data.amount_credited, currency),
              ],
              ["Outstanding", money(data.outstanding, currency)],
            ]}
          />
          <table className="table">
            <thead>
              <tr>
                <th>Line</th>
                <th className="numeric">Quantity</th>
                <th className="numeric">Net</th>
                <th className="numeric">Tax</th>
                <th className="numeric">Total</th>
                {action === "credit" ? <th>Credit</th> : null}
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td className="numeric">{quantity(line.quantity)}</td>
                  <td className="numeric">{money(line.net, currency)}</td>
                  <td className="numeric">{money(line.tax, currency)}</td>
                  <td className="numeric">{money(line.total, currency)}</td>
                  {action === "credit" ? (
                    <td>
                      <TextInput
                        numeric
                        inputMode="decimal"
                        aria-label={`Quantity to credit on ${line.description}`}
                        value={creditQuantities[line.id] ?? ""}
                        onChange={(e) =>
                          setCreditQuantities((all) => ({ ...all, [line.id]: e.target.value }))
                        }
                        placeholder="0"
                      />
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>

          {action === "none" && !isZero(data.outstanding) ? (
            <div className="row gap-2 wrap">
              {can("receipt.manage") ? (
                <Button onClick={() => setAction("note")}>Record contact or promise</Button>
              ) : null}
              {can("sales_return.manage") && data.origin === "sales_order" ? (
                <Button onClick={() => setAction("credit")}>Credit lines</Button>
              ) : null}
              {can("debt.write_off") ? (
                <Button onClick={() => setAction("writeoff")}>Write off</Button>
              ) : null}
            </div>
          ) : null}

          {action !== "none" ? (
            <div className="stack gap-3">
              {action === "note" ? (
                <div className="form-grid">
                  <Select
                    aria-label="Kind"
                    value={kind}
                    onChange={(e) => setKind(e.target.value as typeof kind)}
                  >
                    <option value="contact">Contact</option>
                    <option value="promise">Promise to pay</option>
                    <option value="dispute">Dispute raised</option>
                    <option value="resolved">Dispute resolved</option>
                  </Select>
                  {kind === "promise" ? (
                    <TextInput
                      type="date"
                      aria-label="Promised by"
                      value={promisedOn}
                      onChange={(e) => setPromisedOn(e.target.value)}
                    />
                  ) : null}
                </div>
              ) : null}
              <Field label={action === "note" ? "What was said or agreed" : "Reason"} required>
                {(id) => (
                  <TextInput id={id} value={text} onChange={(e) => setText(e.target.value)} />
                )}
              </Field>
              <div className="row gap-2">
                <Button tone="plain" onClick={() => setAction("none")}>
                  Cancel
                </Button>
                <Button
                  tone="primary"
                  disabled={!text.trim()}
                  busy={note.isPending || credit.isPending || writeOff.isPending}
                  onClick={() =>
                    action === "note"
                      ? note.mutate()
                      : action === "credit"
                        ? credit.mutate()
                        : writeOff.mutate()
                  }
                >
                  {action === "note"
                    ? "Record"
                    : action === "credit"
                      ? "Issue credit note"
                      : `Write off ${money(data.outstanding, currency)}`}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}

function StatementDialog({
  customer,
  onClose,
}: {
  customer: { id: string; name: string };
  onClose: () => void;
}) {
  const currency = useCurrency();
  const start = `${new Date().getFullYear()}-01-01`;
  const statement = useQuery({
    queryKey: ["receivables", "statement", customer.id, start],
    queryFn: () => api.receivables.statement(customer.id, start),
  });
  return (
    <Dialog title={`Statement: ${customer.name}`} size="lg" onClose={onClose}>
      {!statement.data ? (
        <Skeleton rows={6} />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Reference</th>
              <th>Detail</th>
              <th className="numeric">Charged</th>
              <th className="numeric">Paid or credited</th>
              <th className="numeric">Balance</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={5}>Brought forward from {formatDate(statement.data.starts_on)}</td>
              <td className="numeric">{money(statement.data.opening, currency)}</td>
            </tr>
            {statement.data.entries.map((item, index) => (
              <tr key={index}>
                <td>{formatDate(item.posted_on)}</td>
                <td>{item.reference}</td>
                <td className="table__cell--wrap">{item.narration}</td>
                <td className="numeric">{isZero(item.debit) ? "" : money(item.debit, currency)}</td>
                <td className="numeric">
                  {isZero(item.credit) ? "" : money(item.credit, currency)}
                </td>
                <td className="numeric">{money(item.balance, currency)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th colSpan={5}>Owed at {formatDate(statement.data.ends_on)}</th>
              <th className="numeric">{money(statement.data.closing, currency)}</th>
            </tr>
          </tfoot>
        </table>
      )}
    </Dialog>
  );
}
