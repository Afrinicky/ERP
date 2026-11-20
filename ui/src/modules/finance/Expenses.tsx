/**
 * Expenses: costs incurred, the approvals they wait for, the debts they leave, and the
 * ones that recur.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ExpenseRecord } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
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
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

type View = "all" | "awaiting_approval" | "recognised" | "recurring";

const TONE: Record<ExpenseRecord["state"], Tone> = {
  awaiting_approval: "warning",
  recognised: "info",
  settled: "ok",
  rejected: "neutral",
};

export function Expenses() {
  const { can } = useSession();
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const [view, setView] = useState<View>("all");
  const [recording, setRecording] = useState(false);
  const [settling, setSettling] = useState<ExpenseRecord | null>(null);
  const [rejecting, setRejecting] = useState<ExpenseRecord | null>(null);

  const state = view === "all" || view === "recurring" ? undefined : view;
  const list = useQuery({
    queryKey: ["expenses", state ?? "all"],
    queryFn: () => api.expenses.list(state),
    enabled: view !== "recurring",
  });
  const threshold = useQuery({
    queryKey: ["expenses", "threshold"],
    queryFn: api.expenses.threshold,
  });

  const decide = useMutation({
    mutationFn: ({ id, approve, reason }: { id: string; approve: boolean; reason?: string }) =>
      approve ? api.expenses.approve(id) : api.expenses.reject(id, reason ?? ""),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["expenses"] });
      toast.show("success", `${made.number}: ${made.state_label.toLowerCase()}.`);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Tabs
          items={[
            { id: "all", label: "All" },
            { id: "awaiting_approval", label: "Awaiting approval" },
            { id: "recognised", label: "Owed" },
            { id: "recurring", label: "Recurring" },
          ]}
          active={view}
          onChange={setView}
        />
        <div className="row gap-2">
          <span className="caption">
            {threshold.data?.amount
              ? `Approval from ${money(threshold.data.amount, currency)}`
              : "No approval threshold"}
          </span>
          {can("expense.manage") ? (
            <Button tone="primary" onClick={() => setRecording(true)}>
              Record an expense
            </Button>
          ) : null}
        </div>
      </div>

      {view === "recurring" ? (
        <Recurring />
      ) : (
        <Card title="Expenses" flush>
          {list.isPending ? (
            <div style={{ padding: "var(--space-4)" }}>
              <Skeleton rows={4} />
            </div>
          ) : (list.data ?? []).length === 0 ? (
            <EmptyState title="Nothing here." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Expense</th>
                  <th>Payee</th>
                  <th>Account</th>
                  <th>Settlement</th>
                  <th>Status</th>
                  <th className="numeric">Total</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(list.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.number}
                      <div className="cell-note">
                        {formatDate(item.incurred_on)} · {item.description}
                      </div>
                    </td>
                    <td>
                      {item.payee_name}
                      {item.payee_reference ? (
                        <div className="cell-note">{item.payee_reference}</div>
                      ) : null}
                    </td>
                    <td>
                      {item.category_code}
                      <div className="cell-note">
                        {item.category}
                        {item.classification === "capital" ? " · capital" : ""}
                      </div>
                    </td>
                    <td>
                      {item.settlement_label}
                      <div className="cell-note">{item.employee ?? item.account ?? ""}</div>
                    </td>
                    <td>
                      <StatusChip tone={TONE[item.state]}>{item.state_label}</StatusChip>
                      {item.duplicate_reason ? (
                        <div className="cell-note">Confirmed not a duplicate</div>
                      ) : null}
                    </td>
                    <td className="numeric">{money(item.total, currency)}</td>
                    <td>
                      <div className="table__actions">
                        {item.state === "awaiting_approval" && can("expense.approve") ? (
                          <>
                            <Button
                              size="compact"
                              tone="plain"
                              onClick={() => decide.mutate({ id: item.id, approve: true })}
                            >
                              Approve
                            </Button>
                            <Button size="compact" tone="plain" onClick={() => setRejecting(item)}>
                              Reject
                            </Button>
                          </>
                        ) : null}
                        {item.state === "recognised" && can("expense.manage") ? (
                          <Button size="compact" tone="plain" onClick={() => setSettling(item)}>
                            Pay
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {recording ? <ExpenseDialog onClose={() => setRecording(false)} /> : null}
      {settling ? <SettleDialog expense={settling} onClose={() => setSettling(null)} /> : null}
      {rejecting ? (
        <ConfirmDialog
          title={`Reject ${rejecting.number}`}
          record={`${rejecting.payee_name}, ${money(rejecting.total, currency)}`}
          consequence="Nothing is posted. The expense stays on record as rejected."
          confirmLabel="Reject expense"
          requireReason
          busy={decide.isPending}
          onConfirm={(reason) => {
            decide.mutate({ id: rejecting.id, approve: false, reason });
            setRejecting(null);
          }}
          onClose={() => setRejecting(null)}
        />
      ) : null}
    </div>
  );
}

function useCategories() {
  return useQuery({
    queryKey: ["ledger", "accounts", "all"],
    queryFn: () => api.ledger.accounts(),
  });
}

function ExpenseDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const { actor } = useSession();
  const accounts = useQuery({
    queryKey: ["treasury", "accounts"],
    queryFn: () => api.treasury.accounts(),
  });
  const categories = useCategories();
  const [payee, setPayee] = useState("");
  const [classification, setClassification] = useState<"operating" | "capital">("operating");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [net, setNet] = useState("");
  const [tax, setTax] = useState("");
  const [settlement, setSettlement] = useState<"paid" | "on_credit" | "staff_claim">("paid");
  const [account, setAccount] = useState("");
  const [reference, setReference] = useState("");
  const [duplicateReason, setDuplicateReason] = useState("");

  const choices = (categories.data ?? []).filter((item) =>
    classification === "capital"
      ? item.code.startsWith("15")
      : /^[567]/.test(item.code) && item.code !== "70000",
  );

  const record = useMutation({
    mutationFn: () =>
      api.expenses.record({
        payee_name: payee,
        category_code: category,
        description,
        net,
        input_tax: tax || "0",
        classification,
        settlement,
        financial_account_id: settlement === "paid" ? account || accounts.data?.[0]?.id : null,
        employee_id: settlement === "staff_claim" ? actor?.user_id : null,
        payee_reference: reference || null,
        duplicate_reason: duplicateReason || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["expenses"] });
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.number}: ${made.state_label.toLowerCase()}.`);
      onClose();
    },
  });
  const duplicate = record.isError && describe(record.error).message.includes("looks like");

  return (
    <Dialog
      title="Record an expense"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={record.isPending}
            disabled={!payee || !category || !description || !net}
            onClick={() => record.mutate()}
          >
            Record expense
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
        <RadioGroup
          legend="Classification"
          inline
          value={classification}
          onChange={(value) => {
            setClassification(value);
            setCategory("");
          }}
          options={[
            { value: "operating", label: "Operating expense" },
            { value: "capital", label: "Capital purchase", hint: "An asset, not an expense." },
          ]}
        />
        <div className="form-grid">
          <Field label="Payee" required>
            {(id) => (
              <TextInput
                id={id}
                autoFocus
                value={payee}
                onChange={(e) => setPayee(e.target.value)}
                placeholder="ECG"
              />
            )}
          </Field>
          <Field label="Account" required>
            {(id) => (
              <Select id={id} value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">Choose an account</option>
                {choices.map((item) => (
                  <option key={item.id} value={item.code}>
                    {item.code} {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Amount, before tax" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={net}
                onChange={(e) => setNet(e.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Recoverable input tax" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={tax}
                onChange={(e) => setTax(e.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Payee's reference" optional hint="Their invoice or receipt number.">
            {(id) => (
              <TextInput id={id} value={reference} onChange={(e) => setReference(e.target.value)} />
            )}
          </Field>
        </div>
        <Field label="What it was for" required>
          {(id) => (
            <TextInput
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Electricity, October"
            />
          )}
        </Field>
        <RadioGroup
          legend="Settled"
          inline
          value={settlement}
          onChange={setSettlement}
          options={[
            { value: "paid", label: "Paid now" },
            { value: "on_credit", label: "On credit" },
            { value: "staff_claim", label: "Staff claim (mine)" },
          ]}
        />
        {settlement === "paid" ? (
          <Field label="Paid from" required>
            {(id) => (
              <Select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        {duplicate ? (
          <Field label="Why this is not a duplicate" required>
            {(id) => (
              <TextInput
                id={id}
                value={duplicateReason}
                onChange={(e) => setDuplicateReason(e.target.value)}
              />
            )}
          </Field>
        ) : null}
      </div>
    </Dialog>
  );
}

function SettleDialog({ expense, onClose }: { expense: ExpenseRecord; onClose: () => void }) {
  const queries = useQueryClient();
  const currency = useCurrency();
  const accounts = useQuery({
    queryKey: ["treasury", "accounts"],
    queryFn: () => api.treasury.accounts(),
  });
  const [account, setAccount] = useState("");
  const settle = useMutation({
    mutationFn: () => api.expenses.settle(expense.id, account || accounts.data![0]!.id),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["expenses"] });
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      onClose();
    },
  });
  return (
    <Dialog
      title={`Pay ${expense.number}`}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button tone="primary" busy={settle.isPending} onClick={() => settle.mutate()}>
            Pay {money(expense.total, currency)}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {settle.isError ? <Alert tone="error" title={describe(settle.error).message} /> : null}
        <p className="caption">
          Owed to {expense.employee ?? expense.payee_name}. Paying clears the debt; the cost was
          recognised when it was incurred.
        </p>
        <Field label="Paid from" required>
          {(id) => (
            <Select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
              {(accounts.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function Recurring() {
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const { can } = useSession();
  const list = useQuery({ queryKey: ["expenses", "recurring"], queryFn: api.expenses.recurring });
  const raise = useMutation({
    mutationFn: api.expenses.raiseDue,
    onSuccess: (raised) => {
      void queries.invalidateQueries({ queryKey: ["expenses"] });
      toast.show(
        "success",
        raised.length ? `${raised.length} expense(s) raised.` : "Nothing is due.",
      );
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  return (
    <Card
      title="Recurring expenses"
      subtitle="Raised once per period when due."
      actions={
        can("expense.manage") ? (
          <Button tone="primary" busy={raise.isPending} onClick={() => raise.mutate()}>
            Raise what is due
          </Button>
        ) : undefined
      }
      flush
    >
      {(list.data ?? []).length === 0 ? (
        <EmptyState title="No recurring expense is scheduled." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Payee</th>
              <th>Frequency</th>
              <th>Next due</th>
              <th className="numeric">Amount</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((item) => (
              <tr key={item.id}>
                <td>
                  {item.name}
                  <div className="cell-note">{item.category_code}</div>
                </td>
                <td>{item.payee_name}</td>
                <td style={{ textTransform: "capitalize" }}>{item.frequency}</td>
                <td>{formatDate(item.next_due)}</td>
                <td className="numeric">{money(item.net, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
