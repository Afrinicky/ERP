/**
 * Treasury: the business's cash, bank and Mobile Money accounts, money moved between them,
 * and each account's cashbook.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  FinancialAccount,
  FinancialAccountKindName,
  TreasuryMovementKindName,
} from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  RadioGroup,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
} from "@/components/primitives";
import { isZero } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { date as formatDate, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

export function Treasury() {
  const { can } = useSession();
  const currency = useCurrency();
  const [adding, setAdding] = useState(false);
  const [moving, setMoving] = useState(false);
  const [book, setBook] = useState<FinancialAccount | null>(null);

  const accounts = useQuery({
    queryKey: ["treasury", "accounts"],
    queryFn: () => api.treasury.accounts(),
  });
  const movements = useQuery({
    queryKey: ["treasury", "movements"],
    queryFn: api.treasury.movements,
  });

  return (
    <div className="stack gap-6">
      <Card
        title="Accounts"
        subtitle="Each has its own ledger account, so each has its own balance and cashbook."
        actions={
          <>
            {can("transfer.manage") ? (
              <Button onClick={() => setMoving(true)}>Record a movement</Button>
            ) : null}
            {can("financial_account.manage") ? (
              <Button tone="primary" onClick={() => setAdding(true)}>
                Add an account
              </Button>
            ) : null}
          </>
        }
        flush
      >
        {accounts.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Kind</th>
                <th>Ledger</th>
                <th>Custodian</th>
                <th className="numeric">Balance</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(accounts.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>
                    <div>{item.name}</div>
                    <div className="cell-note">
                      {[item.institution, item.number, item.branch_name]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </div>
                  </td>
                  <td>{item.kind_label}</td>
                  <td>{item.ledger_code}</td>
                  <td>{item.custodian ?? "—"}</td>
                  <td className="numeric">{money(item.balance, currency)}</td>
                  <td>
                    <div className="table__actions">
                      <Button size="compact" tone="plain" onClick={() => setBook(item)}>
                        Cashbook
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Movements" subtitle="Transfers, charges, interest and adjustments." flush>
        {(movements.data ?? []).length === 0 ? (
          <EmptyState title="No money has been moved between accounts." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Number</th>
                <th>Movement</th>
                <th>From</th>
                <th>To</th>
                <th className="numeric">Amount</th>
              </tr>
            </thead>
            <tbody>
              {(movements.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.occurred_on)}</td>
                  <td>{item.number}</td>
                  <td>
                    <div>{item.kind_label}</div>
                    <div className="cell-note">{item.reason}</div>
                  </td>
                  <td>{item.from_account ?? "—"}</td>
                  <td>{item.to_account ?? "—"}</td>
                  <td className="numeric">
                    {money(item.amount, currency)}
                    {!isZero(item.charge) ? (
                      <div className="cell-note">Charge {money(item.charge, currency)}</div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {adding ? <AddAccountDialog onClose={() => setAdding(false)} /> : null}
      {moving ? (
        <MovementDialog accounts={accounts.data ?? []} onClose={() => setMoving(false)} />
      ) : null}
      {book ? <CashbookDialog account={book} onClose={() => setBook(null)} /> : null}
    </div>
  );
}

function AddAccountDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState<FinancialAccountKindName>("bank");
  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const [number, setNumber] = useState("");
  const [branch, setBranch] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.treasury.createAccount({
        kind,
        name,
        institution: institution || null,
        number: number || null,
        branch_name: branch || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.name} added on ledger account ${made.ledger_code}.`);
      onClose();
    },
  });

  return (
    <Dialog
      title="Add an account"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={name.trim().length < 2}
            onClick={() => create.mutate()}
          >
            Add account
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {create.isError ? <Alert tone="error" title={describe(create.error).message} /> : null}
        <RadioGroup
          legend="Kind"
          inline
          value={kind}
          onChange={setKind}
          options={[
            { value: "bank", label: "Bank account" },
            { value: "mobile_money", label: "Mobile Money wallet" },
            { value: "cash_drawer", label: "Cash" },
            { value: "petty_cash", label: "Petty cash" },
          ]}
        />
        <Field label="Name" required>
          {(id) => (
            <TextInput
              id={id}
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="GCB Ring Road"
            />
          )}
        </Field>
        <div className="form-grid">
          <Field label={kind === "mobile_money" ? "Provider" : "Institution"} optional>
            {(id) => (
              <TextInput
                id={id}
                value={institution}
                onChange={(e) => setInstitution(e.target.value)}
                placeholder={kind === "mobile_money" ? "MTN MoMo" : "GCB Bank"}
              />
            )}
          </Field>
          <Field label={kind === "mobile_money" ? "Wallet number" : "Account number"} optional>
            {(id) => (
              <TextInput id={id} value={number} onChange={(e) => setNumber(e.target.value)} />
            )}
          </Field>
          {kind === "bank" ? (
            <Field label="Branch" optional>
              {(id) => (
                <TextInput id={id} value={branch} onChange={(e) => setBranch(e.target.value)} />
              )}
            </Field>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}

const MOVEMENTS: { value: TreasuryMovementKindName; label: string }[] = [
  { value: "transfer", label: "Transfer" },
  { value: "bank_charge", label: "Bank charge" },
  { value: "interest", label: "Interest received" },
  { value: "adjustment_in", label: "Adjustment in" },
  { value: "adjustment_out", label: "Adjustment out" },
];

function MovementDialog({
  accounts,
  onClose,
}: {
  accounts: FinancialAccount[];
  onClose: () => void;
}) {
  const queries = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState<TreasuryMovementKindName>("transfer");
  const [from, setFrom] = useState(accounts[0]?.id ?? "");
  const [to, setTo] = useState(accounts[1]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [charge, setCharge] = useState("");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const outward = kind === "transfer" || kind === "bank_charge" || kind === "adjustment_out";
  const inward = kind === "transfer" || kind === "interest" || kind === "adjustment_in";

  const record = useMutation({
    mutationFn: () =>
      api.treasury.record({
        kind,
        amount,
        reason,
        from_account_id: outward ? from : null,
        to_account_id: inward ? to : null,
        charge: kind === "transfer" && charge ? charge : "0",
        reference: reference || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.kind_label} ${made.number} recorded.`);
      onClose();
    },
  });

  const chooser = (label: string, value: string, set: (value: string) => void) => (
    <Field label={label} required>
      {(id) => (
        <Select id={id} value={value} onChange={(e) => set(e.target.value)}>
          {accounts.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} — {money(item.balance, { symbol: "", precision: 2, code: "" })}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );

  return (
    <Dialog
      title="Record a movement"
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
            disabled={!amount || !reason.trim()}
            onClick={() => record.mutate()}
          >
            Record
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
        <RadioGroup legend="Movement" inline value={kind} onChange={setKind} options={MOVEMENTS} />
        <div className="form-grid">
          {outward ? chooser(kind === "transfer" ? "From" : "Account", from, setFrom) : null}
          {inward ? chooser(kind === "transfer" ? "To" : "Account", to, setTo) : null}
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
          {kind === "transfer" ? (
            <Field label="Charge" optional hint="Borne by the sending account.">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={charge}
                  onChange={(e) => setCharge(e.target.value)}
                  placeholder="0.00"
                />
              )}
            </Field>
          ) : null}
          <Field label="Reference" optional>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="CHQ-004512"
              />
            )}
          </Field>
        </div>
        <Field label="Reason" required>
          {(id) => (
            <TextInput
              id={id}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Fund the supplier account"
            />
          )}
        </Field>
        {kind === "transfer" ? (
          <p className="cell-note">
            A transfer moves a balance between two accounts. It is neither income nor expense.
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function CashbookDialog({ account, onClose }: { account: FinancialAccount; onClose: () => void }) {
  const currency = useCurrency();
  const book = useQuery({
    queryKey: ["treasury", "cashbook", account.id],
    queryFn: () => api.treasury.cashbook(account.id),
  });
  return (
    <Dialog title={`${account.name}: cashbook`} size="lg" onClose={onClose}>
      {!book.data ? (
        <Skeleton rows={6} />
      ) : book.data.lines.length === 0 ? (
        <EmptyState title="Nothing has moved on this account." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Entry</th>
                <th>Narration</th>
                <th className="numeric">In</th>
                <th className="numeric">Out</th>
                <th className="numeric">Balance</th>
              </tr>
            </thead>
            <tbody>
              {book.data.lines.map((line, index) => (
                <tr key={`${line.entry_id}-${index}`}>
                  <td>{formatDate(line.posting_date)}</td>
                  <td>
                    {line.number}
                    <div className="cell-note">
                      <StatusChip tone="neutral">{line.source.replace("_", " ")}</StatusChip>
                    </div>
                  </td>
                  <td className="table__cell--wrap">{line.narration}</td>
                  <td className="numeric">
                    {isZero(line.debit) ? "" : money(line.debit, currency)}
                  </td>
                  <td className="numeric">
                    {isZero(line.credit) ? "" : money(line.credit, currency)}
                  </td>
                  <td className="numeric">{money(line.running_balance, currency)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th colSpan={3}>Closing balance</th>
                <th className="numeric">{money(book.data.total_in, currency)}</th>
                <th className="numeric">{money(book.data.total_out, currency)}</th>
                <th className="numeric">{money(book.data.closing_balance, currency)}</th>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </Dialog>
  );
}
