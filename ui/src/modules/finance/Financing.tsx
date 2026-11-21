/**
 * Debt, loans and financing.
 *
 * What is owed to the business and what it owes, each from the record that creates the
 * debt and never netted; the loans and advances behind them, with every version of their
 * schedules; and the money expected in and out.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  DebtLineRecord,
  DebtSplit,
  InterestModelName,
  LoanDetail,
  LoanInput,
  LoanSummary,
  RepaymentFrequencyName,
  RevisionKindName,
  ScheduleRow,
  ScheduleVersion,
} from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Details,
  Dialog,
  EmptyState,
  Field,
  RadioGroup,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, editable, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

type View = "overview" | "agreements" | "receivable" | "payable" | "calendar" | "cash";

const VIEWS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "agreements", label: "Loan agreements" },
  { id: "receivable", label: "Owed to us" },
  { id: "payable", label: "We owe" },
  { id: "calendar", label: "Calendar" },
  { id: "cash", label: "Cash flow" },
];

const STATE_TONE: Record<string, Tone> = {
  draft: "neutral",
  awaiting_approval: "warning",
  active: "info",
  settled: "ok",
  refinanced: "neutral",
  written_off: "error",
  rejected: "neutral",
};

const STANDING_TONE: Record<string, Tone> = {
  active: "info",
  part_paid: "info",
  due_soon: "warning",
  overdue: "error",
  disputed: "warning",
  restructured: "accent",
  settled: "ok",
  written_off: "neutral",
};

const ROW_TONE: Record<string, Tone> = {
  scheduled: "neutral",
  due_soon: "warning",
  due: "warning",
  overdue: "error",
  part_paid: "info",
  paid: "ok",
  closed: "neutral",
};

const MODELS: { value: InterestModelName; label: string }[] = [
  { value: "reducing_balance", label: "Reducing balance" },
  { value: "flat", label: "Flat rate" },
  { value: "fixed", label: "Fixed charge" },
  { value: "none", label: "No interest" },
  { value: "interest_only", label: "Interest only, principal at maturity" },
  { value: "balloon", label: "Reducing balance with balloon" },
];

const FREQUENCIES: { value: RepaymentFrequencyName; label: string }[] = [
  { value: "monthly", label: "Monthly" },
  { value: "weekly", label: "Weekly" },
  { value: "fortnightly", label: "Fortnightly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "semi_annual", label: "Half-yearly" },
  { value: "annual", label: "Annually" },
  { value: "daily", label: "Daily" },
];

const TYPES = [
  { value: "term_loan", label: "Term loan" },
  { value: "overdraft", label: "Overdraft" },
  { value: "revolving_credit", label: "Revolving credit" },
  { value: "asset_finance", label: "Asset finance" },
  { value: "staff_advance", label: "Staff advance" },
  { value: "related_party", label: "Related-party loan" },
  { value: "other", label: "Other" },
] as const;

const ORDERS = {
  charges: ["penalty", "fees", "interest", "principal"],
  principal: ["principal", "interest", "fees", "penalty"],
} as const;

function today(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function nextMonth(from: string): string {
  const [year, month, day] = from.split("-").map(Number) as [number, number, number];
  const target = new Date(year, month, Math.min(day, 28));
  const m = String(target.getMonth() + 1).padStart(2, "0");
  const d = String(target.getDate()).padStart(2, "0");
  return `${target.getFullYear()}-${m}-${d}`;
}

function agreements(count: number): string {
  return count === 1 ? "1 agreement" : `${count} agreements`;
}

function rate(value: string): string {
  return `${editable(value)}%`;
}

export function Financing() {
  const { can } = useSession();
  const [view, setView] = useState<View>("overview");
  const [openId, setOpenId] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordingOther, setRecordingOther] = useState(false);

  const open = (id: string) => {
    setOpenId(id);
    setView("agreements");
  };

  if (openId) {
    return <AgreementView loanId={openId} onBack={() => setOpenId(null)} onOpen={setOpenId} />;
  }

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Tabs items={VIEWS} active={view} onChange={setView} />
        <div className="row gap-2">
          {can("loan.manage") ? (
            <>
              <Button onClick={() => setRecordingOther(true)}>Record another debt</Button>
              <Button tone="primary" onClick={() => setRecording(true)}>
                Record an agreement
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {view === "overview" ? <Overview onShow={setView} /> : null}
      {view === "agreements" ? <Agreements onOpen={open} /> : null}
      {view === "receivable" ? <Register side="receivable" onOpen={open} /> : null}
      {view === "payable" ? <Register side="payable" onOpen={open} /> : null}
      {view === "calendar" ? <Calendar onOpen={open} /> : null}
      {view === "cash" ? <CashFlow /> : null}

      {recording ? (
        <AgreementDialog
          onClose={() => setRecording(false)}
          onSaved={(id) => {
            setRecording(false);
            open(id);
          }}
        />
      ) : null}
      {recordingOther ? <OtherDebtDialog onClose={() => setRecordingOther(false)} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------------- Overview */

function Tile({
  label,
  value,
  footer,
  onClick,
}: {
  label: string;
  value: string;
  footer?: string;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="measure__label">{label}</span>
      <span className="measure__value">{value}</span>
      {footer ? <span className="measure__footer">{footer}</span> : null}
    </>
  );
  return onClick ? (
    <button type="button" className="measure command__measure" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="measure">{body}</div>
  );
}

function Overview({ onShow }: { onShow: (view: View) => void }) {
  const currency = useCurrency();
  const [horizon, setHorizon] = useState("30");
  const summary = useQuery({
    queryKey: ["financing", "summary", horizon],
    queryFn: () => api.financing.summary(Number(horizon)),
  });
  const maturity = useQuery({
    queryKey: ["financing", "maturity"],
    queryFn: api.financing.maturity,
  });

  if (summary.isError) return <Alert tone="error" title={describe(summary.error).message} />;
  if (summary.isPending) return <Skeleton rows={4} height={96} />;
  const data = summary.data;

  return (
    <div className="stack gap-6">
      <div className="debt__sides">
        <section className="debt__side" aria-labelledby="owed-to-us">
          <h3 id="owed-to-us" className="debt__heading">
            Owed to the business
          </h3>
          <div className="measure-grid">
            <Tile
              label="Receivables"
              value={money(data.receivables, currency)}
              footer={`${money(data.receivable_overdue, currency)} overdue`}
              onClick={() => onShow("receivable")}
            />
            <Tile
              label="Loans and advances issued"
              value={money(data.loans_receivable.total, currency)}
              footer={`${agreements(data.loans_receivable.count)} · ${money(data.loans_receivable.charges, currency)} charges due`}
              onClick={() => onShow("agreements")}
            />
            <Tile
              label={`Expected within ${data.window_days} days`}
              value={money(data.receivable_due_soon, currency)}
              onClick={() => onShow("calendar")}
            />
            <Tile
              label="Collected this month"
              value={money(data.collected, currency)}
              footer={`${formatDate(data.period_start)} to ${formatDate(data.period_end)}`}
            />
          </div>
        </section>
        <section className="debt__side" aria-labelledby="we-owe">
          <h3 id="we-owe" className="debt__heading">
            Owed by the business
          </h3>
          <div className="measure-grid">
            <Tile
              label="Payables"
              value={money(data.payables, currency)}
              footer={`${money(data.payable_overdue, currency)} overdue`}
              onClick={() => onShow("payable")}
            />
            <Tile
              label="Borrowings"
              value={money(data.loans_payable.total, currency)}
              footer={`${agreements(data.loans_payable.count)} · ${money(data.loans_payable.charges, currency)} charges due`}
              onClick={() => onShow("agreements")}
            />
            <Tile
              label={`Due within ${data.window_days} days`}
              value={money(data.payable_due_soon, currency)}
              onClick={() => onShow("calendar")}
            />
            <Tile
              label="Paid this month"
              value={money(data.repaid, currency)}
              footer={`${formatDate(data.period_start)} to ${formatDate(data.period_end)}`}
            />
          </div>
        </section>
      </div>
      <p className="caption">
        Each side stands on its own. No figure here offsets what is owed to the business against
        what it owes.
      </p>

      <Card
        title="Debt service"
        subtitle="Loan instalments falling due, against the cash and bank balance today."
        actions={
          <Select
            aria-label="Window"
            value={horizon}
            onChange={(event) => setHorizon(event.target.value)}
          >
            <option value="30">Next 30 days</option>
            <option value="60">Next 60 days</option>
            <option value="90">Next 90 days</option>
          </Select>
        }
      >
        <div className="measure-grid">
          <Tile label="Instalments due" value={money(data.debt_service, currency)} />
          <Tile label="Cash and bank" value={money(data.cash_available, currency)} />
          <Tile
            label="Coverage"
            value={data.coverage ? `${editable(data.coverage)}×` : "—"}
            footer="Cash and bank against the instalments due"
          />
        </div>
      </Card>

      <div className="debt__pair">
        <Composition title="What is owed to us" rows={data.receivables_by_kind} />
        <Composition title="What we owe" rows={data.payables_by_kind} />
      </div>

      <Card title="Ageing" subtitle="By due date, each side apart." flush>
        <table className="table">
          <thead>
            <tr>
              <th>Side</th>
              {data.receivable_ageing.map((bucket) => (
                <th key={bucket.code} className="numeric">
                  {bucket.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[
              { label: "Owed to us", rows: data.receivable_ageing },
              { label: "We owe", rows: data.payable_ageing },
            ].map((side) => (
              <tr key={side.label}>
                <td>{side.label}</td>
                {side.rows.map((bucket) => (
                  <td key={bucket.code} className="numeric">
                    {bucket.count ? money(bucket.amount, currency) : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="debt__pair">
        <Card title="Largest debtors" flush>
          <People rows={data.largest_debtors} empty="No one owes the business." />
        </Card>
        <Card title="Largest creditors" flush>
          <People rows={data.largest_creditors} empty="The business owes no one." />
        </Card>
      </div>

      <div className="debt__pair">
        <Card
          title="Maturity of loans"
          subtitle="Principal still to be repaid, by when it falls due."
          flush
        >
          {maturity.isPending ? (
            <div style={{ padding: "var(--space-4)" }}>
              <Skeleton rows={3} />
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Falls due</th>
                  <th className="numeric">Borrowed</th>
                  <th className="numeric">Lent</th>
                </tr>
              </thead>
              <tbody>
                {(maturity.data ?? []).map((row) => (
                  <tr key={row.code}>
                    <td>{row.label}</td>
                    <td className="numeric">{money(row.borrowed, currency)}</td>
                    <td className="numeric">{money(row.lent, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card
          title="Six months"
          subtitle="Collected, paid, and loan balances at each month end."
          flush
        >
          <table className="table">
            <thead>
              <tr>
                <th>Month</th>
                <th className="numeric">Collected</th>
                <th className="numeric">Paid</th>
                <th className="numeric">Borrowed</th>
                <th className="numeric">Lent</th>
              </tr>
            </thead>
            <tbody>
              {data.trend.map((point) => (
                <tr key={point.month}>
                  <td>{formatDate(`${point.month}-01`).replace(/^\d+ /, "")}</td>
                  <td className="numeric">{money(point.collected, currency)}</td>
                  <td className="numeric">{money(point.settled, currency)}</td>
                  <td className="numeric">{money(point.borrowed, currency)}</td>
                  <td className="numeric">{money(point.lent, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

function Composition({ title, rows }: { title: string; rows: DebtSplit[] }) {
  const currency = useCurrency();
  return (
    <Card title={title} flush>
      {rows.length === 0 ? (
        <EmptyState title="Nothing outstanding." />
      ) : (
        <table className="table">
          <tbody>
            {rows.map((row) => (
              <tr key={row.code}>
                <td>
                  {row.label}
                  <div className="cell-note">
                    {row.count} {row.count === 1 ? "item" : "items"}
                  </div>
                </td>
                <td className="numeric">{money(row.amount, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function People({
  rows,
  empty,
}: {
  rows: { name: string; amount: string; count: number }[];
  empty: string;
}) {
  const currency = useCurrency();
  if (rows.length === 0) return <EmptyState title={empty} />;
  return (
    <table className="table">
      <tbody>
        {rows.map((row) => (
          <tr key={row.name}>
            <td>
              {row.name}
              <div className="cell-note">
                {row.count} {row.count === 1 ? "item" : "items"}
              </div>
            </td>
            <td className="numeric">{money(row.amount, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ----------------------------------------------------------------------- Agreements */

function Agreements({ onOpen }: { onOpen: (id: string) => void }) {
  const currency = useCurrency();
  const [direction, setDirection] = useState<"all" | "borrowing" | "lending">("all");
  const list = useQuery({
    queryKey: ["financing", "loans", direction],
    queryFn: () => api.financing.loans(direction === "all" ? undefined : direction),
  });

  return (
    <Card
      title="Loan agreements"
      actions={
        <Tabs
          items={[
            { id: "all", label: "All" },
            { id: "borrowing", label: "Borrowing" },
            { id: "lending", label: "Lending" },
          ]}
          active={direction}
          onChange={setDirection}
        />
      }
      flush
    >
      {list.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={4} />
        </div>
      ) : (list.data ?? []).length === 0 ? (
        <EmptyState title="No agreements recorded." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Agreement</th>
              <th>Counterparty</th>
              <th>Terms</th>
              <th>Next due</th>
              <th>Status</th>
              <th className="numeric">Principal</th>
              <th className="numeric">Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((item) => (
              <AgreementRow key={item.id} item={item} currency={currency} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function AgreementRow({
  item,
  currency,
  onOpen,
}: {
  item: LoanSummary;
  currency: ReturnType<typeof useCurrency>;
  onOpen: (id: string) => void;
}) {
  return (
    <tr>
      <td>
        <button type="button" className="text-button" onClick={() => onOpen(item.id)}>
          {item.number}
        </button>
        <div className="cell-note">{item.agreement_reference ?? item.loan_type_label}</div>
      </td>
      <td>
        {item.counterparty_name}
        <div className="cell-note">
          {item.direction_label} · {item.loan_type_label}
        </div>
      </td>
      <td>
        {item.model === "none" ? "No interest" : `${rate(item.annual_rate)} a year`}
        <div className="cell-note">
          {item.model_label} · {item.frequency_label.toLowerCase()}
        </div>
      </td>
      <td>
        {item.next_due_on ? formatDate(item.next_due_on) : "—"}
        {item.next_due_on ? (
          <div className="cell-note">{money(item.next_due_amount, currency)}</div>
        ) : null}
      </td>
      <td>
        <StatusChip tone={item.days_overdue > 0 ? "error" : (STATE_TONE[item.state] ?? "neutral")}>
          {item.days_overdue > 0 ? `${item.days_overdue} days overdue` : item.state_label}
        </StatusChip>
        {item.revision_pending ? <div className="cell-note">Revision awaiting decision</div> : null}
        {item.disputed ? <div className="cell-note">Disputed</div> : null}
      </td>
      <td className="numeric">{money(item.principal, currency)}</td>
      <td className="numeric">{money(item.outstanding, currency)}</td>
    </tr>
  );
}

/* -------------------------------------------------------------------- One agreement */

type Action =
  | "drawdown"
  | "repay"
  | "settle"
  | "revise"
  | "security"
  | "refinance"
  | "approve"
  | "reject"
  | "penalty"
  | "write_off"
  | "decline_revision"
  | "approve_revision"
  | "dispute";

function AgreementView({
  loanId,
  onBack,
  onOpen,
}: {
  loanId: string;
  onBack: () => void;
  onOpen: (id: string) => void;
}) {
  const { can } = useSession();
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const [action, setAction] = useState<Action | null>(null);
  const [shown, setShown] = useState<number | null>(null);
  const loan = useQuery({
    queryKey: ["financing", "loan", loanId],
    queryFn: () => api.financing.loan(loanId),
  });

  const done = (made: LoanDetail, message: string) => {
    queries.setQueryData(["financing", "loan", loanId], made);
    void queries.invalidateQueries({ queryKey: ["financing"] });
    void queries.invalidateQueries({ queryKey: ["treasury"] });
    setAction(null);
    toast.show("success", message);
  };
  const failed = (error: unknown) => toast.show("error", describe(error).message);

  const submit = useMutation({
    mutationFn: () => api.financing.submit(loanId),
    onSuccess: (made) => done(made, `${made.number}: ${made.state_label.toLowerCase()}.`),
    onError: failed,
  });
  const simple = useMutation({
    mutationFn: ({ kind, text }: { kind: Action; text: string }) => {
      switch (kind) {
        case "approve":
          return api.financing.approve(loanId, text);
        case "reject":
          return api.financing.reject(loanId, text);
        case "penalty":
          return api.financing.penalty(loanId, text);
        case "write_off":
          return api.financing.writeOff(loanId, text);
        case "approve_revision":
          return api.financing.decide(loanId, true, text || null);
        case "decline_revision":
          return api.financing.decide(loanId, false, text);
        default:
          return api.financing.dispute(loanId, !loan.data?.disputed, text);
      }
    },
    onSuccess: (made) => done(made, `${made.number} updated.`),
    onError: failed,
  });

  if (loan.isError) return <Alert tone="error" title={describe(loan.error).message} />;
  if (loan.isPending) return <Skeleton rows={6} height={40} />;
  const data = loan.data;
  const position = data.position;
  const active = data.state === "active";
  const versions = data.versions;
  const version: ScheduleVersion = versions.find((item) => item.version === shown) ?? data.current;

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Button tone="plain" onClick={onBack}>
          ← All agreements
        </Button>
        <div className="row gap-2 wrap">
          {data.state === "draft" && can("loan.manage") ? (
            <Button tone="primary" busy={submit.isPending} onClick={() => submit.mutate()}>
              Submit
            </Button>
          ) : null}
          {data.state === "awaiting_approval" && can("loan.approve") ? (
            <>
              <Button onClick={() => setAction("reject")}>Reject</Button>
              <Button tone="primary" onClick={() => setAction("approve")}>
                Approve
              </Button>
            </>
          ) : null}
          {active && can("loan.restructure") && !data.proposed ? (
            <Button onClick={() => setAction("revise")}>Propose a revision</Button>
          ) : null}
          {active && can("repayment.manage") ? (
            <>
              <Button onClick={() => setAction("settle")}>Settle early</Button>
              {Number(position.undrawn) > 0 ? (
                <Button onClick={() => setAction("drawdown")}>Record a drawdown</Button>
              ) : null}
              <Button
                tone="primary"
                size="transactional"
                onClick={() => setAction("repay")}
                disabled={!Number(position.principal_drawn)}
              >
                Record a repayment
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <Card
        title={`${data.number} · ${data.counterparty_name}`}
        subtitle={`${data.direction_label}, ${data.loan_type_label.toLowerCase()}${
          data.agreement_reference ? ` · ${data.agreement_reference}` : ""
        }`}
        actions={
          <div className="row gap-2">
            <StatusChip tone={STATE_TONE[data.state] ?? "neutral"}>{data.state_label}</StatusChip>
            {data.disputed ? <StatusChip tone="warning">Disputed</StatusChip> : null}
            {active ? (
              <StatusChip tone={position.agrees_with_ledger ? "ok" : "error"}>
                {position.agrees_with_ledger ? "Agrees with the ledger" : "Differs from the ledger"}
              </StatusChip>
            ) : null}
          </div>
        }
      >
        {data.state === "draft" ||
        data.state === "awaiting_approval" ||
        data.state === "rejected" ? (
          <div className="measure-grid">
            <Tile label="Principal" value={money(data.principal, currency)} />
            <Tile
              label="Interest over the term"
              value={money(data.current.summary.interest, currency)}
            />
            <Tile label="Repaid in all" value={money(data.current.summary.total, currency)} />
            <Tile
              label="Last instalment"
              value={data.maturity_date ? formatDate(data.maturity_date) : "—"}
              footer={`${data.current.summary.instalments} instalments`}
            />
          </div>
        ) : (
          <div className="measure-grid">
            <Tile
              label="Principal outstanding"
              value={money(position.principal_outstanding, currency)}
              footer={`${money(position.principal_drawn, currency)} drawn of ${money(position.principal_agreed, currency)}`}
            />
            <Tile
              label="Interest and charges due"
              value={money(data.charges, currency)}
              footer={`Interest ${money(position.interest_due, currency)} · fees ${money(position.fees_due, currency)} · penalties ${money(position.penalties_due, currency)}`}
            />
            <Tile
              label="Next instalment"
              value={position.next_due_on ? money(position.next_due_amount, currency) : "—"}
              footer={
                position.next_due_on
                  ? `Due ${formatDate(position.next_due_on)}`
                  : data.closed_on
                    ? `Closed ${formatDate(data.closed_on)}`
                    : undefined
              }
            />
            <Tile
              label={position.days_overdue > 0 ? "In arrears" : "To settle today"}
              value={
                position.days_overdue > 0
                  ? money(position.arrears, currency)
                  : money(position.settlement_today, currency)
              }
              footer={
                position.days_overdue > 0 ? `${position.days_overdue} days overdue` : undefined
              }
            />
          </div>
        )}
      </Card>

      {data.proposed ? (
        <Alert
          tone="warning"
          title={`Revision proposed: ${data.proposed.kind_label.toLowerCase()}, version ${data.proposed.version}`}
        >
          <p>
            {data.proposed.reason}
            {/[.!?]$/.test(data.proposed.reason ?? "") ? "" : "."} Built on{" "}
            {money(data.proposed.principal_basis, currency)} of principal,{" "}
            {data.proposed.instalments} instalments at {rate(data.proposed.annual_rate)}. Proposed
            by {data.proposed.proposed_by ?? "—"}.
          </p>
          {can("loan.restructure") ? (
            <div className="row gap-2">
              <Button size="compact" onClick={() => setShown(data.proposed!.version)}>
                View the proposed schedule
              </Button>
              <Button size="compact" onClick={() => setAction("decline_revision")}>
                Decline
              </Button>
              <Button size="compact" tone="primary" onClick={() => setAction("approve_revision")}>
                Approve
              </Button>
            </div>
          ) : null}
        </Alert>
      ) : null}

      <Card
        title="Schedule"
        subtitle={
          version.state === "current"
            ? `Version ${version.version}, ${version.kind_label.toLowerCase()}. Earlier versions are kept as they stood.`
            : `Version ${version.version}, ${version.kind_label.toLowerCase()}: ${version.state}.`
        }
        actions={
          versions.length > 1 ? (
            <Select
              aria-label="Version"
              value={String(version.version)}
              onChange={(event) => setShown(Number(event.target.value))}
            >
              {versions.map((item) => (
                <option key={item.id} value={item.version}>
                  Version {item.version} · {item.kind_label} · {item.state}
                </option>
              ))}
            </Select>
          ) : undefined
        }
        flush
      >
        {version.reason ? (
          <p className="caption" style={{ padding: "0 var(--space-4) var(--space-3)" }}>
            {version.reason}
            {version.decided_by ? ` Decided by ${version.decided_by}.` : ""}
            {version.decision_note ? ` ${version.decision_note}` : ""}
          </p>
        ) : null}
        <ScheduleTable rows={version.rows} summary={version.summary} />
      </Card>

      <div className="debt__pair">
        <Card title="Agreement">
          <Details
            rows={[
              ["Principal", money(data.principal, currency)],
              [
                "Interest",
                data.model === "none"
                  ? "None"
                  : `${rate(data.annual_rate)} a year, ${data.model_label.toLowerCase()}`,
              ],
              [
                "Repayment",
                `${data.instalments} instalments, ${data.frequency_label.toLowerCase()}, from ${formatDate(data.first_due_on)}`,
              ],
              ["Agreement date", formatDate(data.agreement_date)],
              ["Maturity", data.maturity_date ? formatDate(data.maturity_date) : null],
              [
                "Grace and interest-only periods",
                `${data.grace_periods} and ${data.interest_only_periods}`,
              ],
              ["Balloon", Number(data.balloon) ? money(data.balloon, currency) : "None"],
              [
                "Fees",
                `${money(data.upfront_fee, currency)} upfront, ${money(data.periodic_fee, currency)} an instalment`,
              ],
              [
                "Penalty on arrears",
                Number(data.penalty_rate)
                  ? `${rate(data.penalty_rate)} a year`
                  : "Not provided for",
              ],
              [
                "Early settlement",
                Number(data.early_settlement_percent)
                  ? `${rate(data.early_settlement_percent)} of principal`
                  : "No fee",
              ],
              [
                "Prepayment",
                data.prepayment_allowed ? "Allowed, schedule recalculated" : "Not allowed",
              ],
              ["Repayments settle", data.allocation_order.join(", then ")],
              ["Account", data.account],
              ["Purpose", data.purpose],
              [
                "Approved by",
                data.approved_by
                  ? `${data.approved_by}${data.approval_note ? `: ${data.approval_note}` : ""}`
                  : null,
              ],
              ...(data.refinances ? [["Refinances", data.refinances] as [string, string]] : []),
              ...(data.refinanced_by
                ? [["Refinanced by", data.refinanced_by] as [string, string]]
                : []),
              ...(data.closing_note ? [["Closed", data.closing_note] as [string, string]] : []),
            ]}
          />
          {active ? (
            <div className="row gap-2 wrap" style={{ marginTop: "var(--space-4)" }}>
              {can("loan.manage") && Number(data.penalty_rate) > 0 ? (
                <Button size="compact" tone="plain" onClick={() => setAction("penalty")}>
                  Charge a penalty
                </Button>
              ) : null}
              {can("loan.restructure") ? (
                <Button size="compact" tone="plain" onClick={() => setAction("refinance")}>
                  Refinance
                </Button>
              ) : null}
              {can("loan.manage") ? (
                <Button size="compact" tone="plain" onClick={() => setAction("dispute")}>
                  {data.disputed ? "Resolve the dispute" : "Record a dispute"}
                </Button>
              ) : null}
              {data.direction === "lending" && can("debt.write_off") ? (
                <Button size="compact" tone="plain" onClick={() => setAction("write_off")}>
                  Write off
                </Button>
              ) : null}
            </div>
          ) : null}
        </Card>

        <Card
          title="Security"
          actions={
            can("loan.manage") ? (
              <Button size="compact" onClick={() => setAction("security")}>
                Add
              </Button>
            ) : undefined
          }
          flush
        >
          {data.securities.length === 0 ? (
            <EmptyState title="No collateral, guarantor or covenant recorded." />
          ) : (
            <table className="table">
              <tbody>
                {data.securities.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.kind_label}
                      <div className="cell-note">{item.party_name ?? ""}</div>
                    </td>
                    <td className="table__cell--wrap">
                      {item.description}
                      {item.review_on ? (
                        <div className="cell-note">Review {formatDate(item.review_on)}</div>
                      ) : null}
                    </td>
                    <td className="numeric">{item.value ? money(item.value, currency) : "—"}</td>
                    <td>
                      {item.released_on ? `Released ${formatDate(item.released_on)}` : "Held"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Transactions" subtitle="Each repayment as it was split." flush>
        {data.transactions.length === 0 ? (
          <EmptyState title="Nothing recorded yet." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Transaction</th>
                <th className="numeric">Amount</th>
                <th className="numeric">Principal</th>
                <th className="numeric">Interest</th>
                <th className="numeric">Fees</th>
                <th className="numeric">Penalties</th>
              </tr>
            </thead>
            <tbody>
              {data.transactions.map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.occurred_on)}</td>
                  <td>
                    {item.kind_label}
                    <div className="cell-note">
                      {[item.account, item.reference, item.note, item.recorded_by]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  </td>
                  <td className="numeric">{money(item.amount, currency)}</td>
                  <td className="numeric">{money(item.principal, currency)}</td>
                  <td className="numeric">{money(item.interest, currency)}</td>
                  <td className="numeric">{money(item.fees, currency)}</td>
                  <td className="numeric">{money(item.penalty, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {action === "drawdown" || action === "repay" ? (
        <MovementDialog loan={data} kind={action} onClose={() => setAction(null)} onDone={done} />
      ) : null}
      {action === "settle" ? (
        <SettleDialog loan={data} onClose={() => setAction(null)} onDone={done} />
      ) : null}
      {action === "revise" ? (
        <RevisionDialog loan={data} onClose={() => setAction(null)} onDone={done} />
      ) : null}
      {action === "security" ? (
        <SecurityDialog loan={data} onClose={() => setAction(null)} onDone={done} />
      ) : null}
      {action === "refinance" ? (
        <RefinanceDialog
          loan={data}
          onClose={() => setAction(null)}
          onDone={(made, message) => {
            done(made, message);
            if (made.refinanced_by) toast.show("success", message);
          }}
          onOpen={onOpen}
        />
      ) : null}
      {action === "approve" || action === "approve_revision" ? (
        <NoteDialog
          title={action === "approve" ? `Approve ${data.number}` : "Approve the revision"}
          label="Authorisation"
          hint="Who authorised it and how: a board resolution, a signed approval."
          required={action === "approve"}
          confirmLabel="Approve"
          busy={simple.isPending}
          onConfirm={(text) => simple.mutate({ kind: action, text })}
          onClose={() => setAction(null)}
        />
      ) : null}
      {action === "dispute" ? (
        <NoteDialog
          title={data.disputed ? "Resolve the dispute" : "Record a dispute"}
          label={data.disputed ? "How it was resolved" : "What is disputed"}
          required
          confirmLabel={data.disputed ? "Resolve" : "Record dispute"}
          busy={simple.isPending}
          onConfirm={(text) => simple.mutate({ kind: "dispute", text })}
          onClose={() => setAction(null)}
        />
      ) : null}
      {action === "reject" ||
      action === "decline_revision" ||
      action === "penalty" ||
      action === "write_off" ? (
        <ConfirmDialog
          title={
            action === "reject"
              ? `Reject ${data.number}`
              : action === "decline_revision"
                ? "Decline the revision"
                : action === "penalty"
                  ? "Charge a penalty"
                  : `Write off ${data.number}`
          }
          record={`${data.counterparty_name}, ${money(position.total_outstanding, currency)} outstanding`}
          consequence={
            action === "reject"
              ? "Nothing is posted. The agreement stays on record as rejected."
              : action === "decline_revision"
                ? "The current schedule stays in force. The proposal is kept as declined."
                : action === "penalty"
                  ? "The penalty the agreement provides for is calculated on the arrears to today and recognised as a finance charge."
                  : "What remains is recognised as a bad debt. The agreement and its history are kept."
          }
          confirmLabel={
            action === "reject"
              ? "Reject agreement"
              : action === "decline_revision"
                ? "Decline revision"
                : action === "penalty"
                  ? "Charge penalty"
                  : "Write off"
          }
          requireReason
          busy={simple.isPending}
          onConfirm={(text) => simple.mutate({ kind: action, text })}
          onClose={() => setAction(null)}
        />
      ) : null}
    </div>
  );
}

function ScheduleTable({
  rows,
  summary,
  plan,
}: {
  rows: ScheduleRow[];
  summary: { principal: string; interest: string; fees: string; total: string };
  plan?: boolean;
}) {
  const currency = useCurrency();
  return (
    <div className="table-frame">
      <table className="table">
        <thead>
          <tr>
            <th>#</th>
            <th>Due</th>
            {plan ? null : <th className="numeric">Opening</th>}
            <th className="numeric">Interest</th>
            <th className="numeric">Fees</th>
            <th className="numeric">Principal</th>
            <th className="numeric">Instalment</th>
            <th className="numeric">Closing</th>
            {plan ? null : <th className="numeric">Paid</th>}
            {plan ? null : <th>Status</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id ?? row.sequence}>
              <td>{row.arrears ? "Arrears" : row.sequence}</td>
              <td className="nowrap">{formatDate(row.due_on)}</td>
              {plan ? null : <td className="numeric">{money(row.opening, currency)}</td>}
              <td className="numeric">
                {money(row.interest, currency)}
                {Number(row.capitalised) ? (
                  <div className="cell-note">{money(row.capitalised, currency)} added</div>
                ) : null}
              </td>
              <td className="numeric">
                {money(row.fees, currency)}
                {Number(row.penalty) ? (
                  <div className="cell-note">+ {money(row.penalty, currency)} penalty</div>
                ) : null}
              </td>
              <td className="numeric">{money(row.principal, currency)}</td>
              <td className="numeric">{money(row.total, currency)}</td>
              <td className="numeric">{money(row.closing, currency)}</td>
              {plan ? null : (
                <td className="numeric">{Number(row.paid) ? money(row.paid, currency) : "—"}</td>
              )}
              {plan ? null : (
                <td>
                  <StatusChip tone={ROW_TONE[row.state] ?? "neutral"}>{row.state_label}</StatusChip>
                </td>
              )}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th colSpan={plan ? 2 : 3}>Over the term</th>
            <th className="numeric">{money(summary.interest, currency)}</th>
            <th className="numeric">{money(summary.fees, currency)}</th>
            <th className="numeric">{money(summary.principal, currency)}</th>
            <th className="numeric">{money(summary.total, currency)}</th>
            <th colSpan={plan ? 1 : 3} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------------------- Registers */

function Register({
  side,
  onOpen,
}: {
  side: "receivable" | "payable";
  onOpen: (id: string) => void;
}) {
  const currency = useCurrency();
  const [kind, setKind] = useState("");
  const [standing, setStanding] = useState("");
  const [search, setSearch] = useState("");
  const [settled, setSettled] = useState(false);
  const lines = useQuery({
    queryKey: ["financing", "register", side, kind, standing, search, settled],
    queryFn: () =>
      api.financing.register({
        side,
        kind: kind || undefined,
        standing: standing || undefined,
        counterparty: search || undefined,
        include_settled: settled,
      }),
  });
  const kinds =
    side === "receivable"
      ? [
          ["trade_receivable", "Customer invoices"],
          ["loan_issued", "Loans and advances issued"],
          ["other_receivable", "Other receivables"],
        ]
      : [
          ["supplier", "Supplier invoices"],
          ["expense", "Unpaid expenses"],
          ["staff_claim", "Staff claims"],
          ["tax", "Tax payable"],
          ["commission", "Commissions payable"],
          ["refund", "Refunds due"],
          ["borrowing", "Borrowings"],
          ["other_payable", "Other payables"],
        ];

  return (
    <Card
      title={side === "receivable" ? "Owed to the business" : "Owed by the business"}
      subtitle="Each debt from the record that creates it, once."
      flush
    >
      <div className="filter-bar filter-bar--four">
        <SearchField
          label="Counterparty"
          value={search}
          onChange={setSearch}
          placeholder="Counterparty"
        />
        <Select aria-label="Kind" value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="">Every kind</option>
          {kinds.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Standing"
          value={standing}
          onChange={(event) => setStanding(event.target.value)}
        >
          <option value="">Any standing</option>
          <option value="overdue">Overdue</option>
          <option value="due_soon">Due soon</option>
          <option value="part_paid">Part paid</option>
          <option value="disputed">Disputed</option>
          <option value="restructured">Restructured</option>
          <option value="active">Active</option>
        </Select>
        <Checkbox label="Include settled" checked={settled} onChange={setSettled} />
      </div>
      {lines.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={5} />
        </div>
      ) : (lines.data ?? []).length === 0 ? (
        <EmptyState title="Nothing outstanding." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Counterparty</th>
              <th>Due</th>
              <th>Standing</th>
              <th className="numeric">Original</th>
              <th className="numeric">Settled</th>
              <th className="numeric">Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {(lines.data ?? []).map((line) => (
              <RegisterRow
                key={`${line.source_type}-${line.source_id}`}
                line={line}
                currency={currency}
                onOpen={onOpen}
              />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RegisterRow({
  line,
  currency,
  onOpen,
}: {
  line: DebtLineRecord;
  currency: ReturnType<typeof useCurrency>;
  onOpen: (id: string) => void;
}) {
  return (
    <tr>
      <td>
        {line.source_type === "loan_agreement" ? (
          <button type="button" className="text-button" onClick={() => onOpen(line.source_id)}>
            {line.reference}
          </button>
        ) : (
          line.reference
        )}
        <div className="cell-note">{line.kind_label}</div>
      </td>
      <td>
        {line.counterparty}
        <div className="cell-note">{line.description}</div>
      </td>
      <td className="nowrap">
        {line.due_on ? formatDate(line.due_on) : "—"}
        <div className="cell-note nowrap">Since {formatDate(line.issued_on)}</div>
      </td>
      <td>
        <StatusChip tone={STANDING_TONE[line.standing] ?? "neutral"}>
          {line.standing_label}
        </StatusChip>
      </td>
      <td className="numeric">{money(line.original, currency)}</td>
      <td className="numeric">{money(line.settled, currency)}</td>
      <td className="numeric">
        {money(line.outstanding, currency)}
        {Number(line.charges) ? (
          <div className="cell-note">incl. {money(line.charges, currency)} charges</div>
        ) : null}
      </td>
    </tr>
  );
}

/* ----------------------------------------------------------------------- Calendar */

function Calendar({ onOpen }: { onOpen: (id: string) => void }) {
  const currency = useCurrency();
  const [days, setDays] = useState("90");
  const lines = useQuery({
    queryKey: ["financing", "calendar", days],
    queryFn: () => api.financing.calendar(Number(days)),
  });
  return (
    <Card
      title="Money expected in and out"
      subtitle="Instalments, invoices, tax and other debts by the date they fall due. Anything already overdue is shown today."
      actions={
        <Select aria-label="Horizon" value={days} onChange={(event) => setDays(event.target.value)}>
          <option value="7">Next 7 days</option>
          <option value="30">Next 30 days</option>
          <option value="60">Next 60 days</option>
          <option value="90">Next 90 days</option>
          <option value="180">Next 180 days</option>
        </Select>
      }
      flush
    >
      {lines.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={5} />
        </div>
      ) : (lines.data ?? []).length === 0 ? (
        <EmptyState title="Nothing falls due in this window." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Reference</th>
              <th>Counterparty</th>
              <th className="numeric">In</th>
              <th className="numeric">Out</th>
            </tr>
          </thead>
          <tbody>
            {(lines.data ?? []).map((line, index) => (
              <tr key={`${line.source_id}-${line.reference}-${index}`}>
                <td>{formatDate(line.due_on)}</td>
                <td>
                  {line.source_type === "loan_agreement" ? (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => onOpen(line.source_id)}
                    >
                      {line.reference}
                    </button>
                  ) : (
                    line.reference
                  )}
                  <div className="cell-note">{line.kind_label}</div>
                </td>
                <td>{line.counterparty}</td>
                <td className="numeric">{line.inward ? money(line.amount, currency) : ""}</td>
                <td className="numeric">{line.inward ? "" : money(line.amount, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------------- Cash flow */

function CashFlow() {
  const currency = useCurrency();
  const [period, setPeriod] = useState<"month" | "quarter" | "year">("year");
  const { start, end } = useMemo(() => {
    const now = new Date();
    const year = now.getFullYear();
    const pad = (value: number) => String(value).padStart(2, "0");
    const iso = (day: Date) =>
      `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
    if (period === "year") return { start: `${year}-01-01`, end: iso(now) };
    if (period === "quarter") {
      return { start: iso(new Date(year, Math.floor(now.getMonth() / 3) * 3, 1)), end: iso(now) };
    }
    return { start: iso(new Date(year, now.getMonth(), 1)), end: iso(now) };
  }, [period]);
  const rows = useQuery({
    queryKey: ["financing", "cash", start, end],
    queryFn: () => api.financing.cashFlow(start, end),
  });

  return (
    <Card
      title="Cash by class"
      subtitle={`${formatDate(start)} to ${formatDate(end)}. Loan principal, interest, trade and transfers each on their own line.`}
      actions={
        <Tabs
          items={[
            { id: "month", label: "This month" },
            { id: "quarter", label: "This quarter" },
            { id: "year", label: "This year" },
          ]}
          active={period}
          onChange={setPeriod}
        />
      }
      flush
    >
      {rows.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={6} />
        </div>
      ) : (rows.data ?? []).length === 0 ? (
        <EmptyState title="No cash moved in this period." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Class</th>
              <th>Activity</th>
              <th className="numeric">In</th>
              <th className="numeric">Out</th>
              <th className="numeric">Net</th>
            </tr>
          </thead>
          <tbody>
            {(rows.data ?? []).map((row) => (
              <tr key={row.code}>
                <td>{row.label}</td>
                <td className="caption">{row.category}</td>
                <td className="numeric">{money(row.inflow, currency)}</td>
                <td className="numeric">{money(row.outflow, currency)}</td>
                <td className="numeric">{money(row.net, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------------ Dialogs */

function useAccounts() {
  return useQuery({ queryKey: ["treasury", "accounts"], queryFn: () => api.treasury.accounts() });
}

function AgreementDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const currency = useCurrency();
  const accounts = useAccounts();
  const queries = useQueryClient();
  const start = today();
  const [direction, setDirection] = useState<"borrowing" | "lending">("borrowing");
  const [type, setType] = useState<LoanInput["loan_type"]>("term_loan");
  const [counterparty, setCounterparty] = useState("");
  const [reference, setReference] = useState("");
  const [purpose, setPurpose] = useState("");
  const [principal, setPrincipal] = useState("");
  const [agreedOn, setAgreedOn] = useState(start);
  const [model, setModel] = useState<InterestModelName>("reducing_balance");
  const [annualRate, setAnnualRate] = useState("");
  const [frequency, setFrequency] = useState<RepaymentFrequencyName>("monthly");
  const [count, setCount] = useState("12");
  const [firstDue, setFirstDue] = useState(nextMonth(start));
  const [grace, setGrace] = useState("0");
  const [interestOnly, setInterestOnly] = useState("0");
  const [balloon, setBalloon] = useState("");
  const [fixedCharge, setFixedCharge] = useState("");
  const [periodicFee, setPeriodicFee] = useState("");
  const [upfrontFee, setUpfrontFee] = useState("");
  const [penalty, setPenalty] = useState("");
  const [earlyFee, setEarlyFee] = useState("");
  const [prepayment, setPrepayment] = useState(true);
  const [order, setOrder] = useState<keyof typeof ORDERS>("charges");
  const [account, setAccount] = useState("");

  const payload: LoanInput = {
    direction,
    loan_type: type,
    counterparty_name: counterparty,
    principal: principal || "0",
    agreement_date: agreedOn,
    model,
    frequency,
    instalments: Number(count) || 1,
    first_due_on: firstDue,
    annual_rate: model === "none" || model === "fixed" ? "0" : annualRate || "0",
    grace_periods: Number(grace) || 0,
    interest_only_periods: Number(interestOnly) || 0,
    balloon: model === "balloon" ? balloon || "0" : "0",
    fixed_interest: model === "fixed" ? fixedCharge || "0" : "0",
    periodic_fee: periodicFee || "0",
    upfront_fee: upfrontFee || "0",
    penalty_rate: penalty || "0",
    early_settlement_percent: earlyFee || "0",
    prepayment_allowed: prepayment,
    allocation_order: [...ORDERS[order]],
    agreement_reference: reference || null,
    purpose: purpose || null,
    financial_account_id:
      account || accounts.data?.find((item) => item.kind === "bank")?.id || null,
  };
  const ready =
    counterparty.trim().length > 0 &&
    Number(principal) > 0 &&
    Number(count) > 0 &&
    (model === "none" || model === "fixed" || Number(annualRate) > 0);
  const key = JSON.stringify(payload);
  const preview = useQuery({
    queryKey: ["financing", "preview", key],
    queryFn: () => api.financing.preview(payload),
    enabled: ready,
    retry: false,
  });

  const save = useMutation({
    mutationFn: async (andSubmit: boolean) => {
      const made = await api.financing.create(payload);
      return andSubmit ? api.financing.submit(made.id) : made;
    },
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["financing"] });
      onSaved(made.id);
    },
  });

  return (
    <Dialog
      title="Record an agreement"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={save.isPending} disabled={!ready} onClick={() => save.mutate(false)}>
            Save as draft
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={save.isPending}
            disabled={!ready || preview.isError}
            onClick={() => save.mutate(true)}
          >
            Save and submit
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <RadioGroup
          legend="Direction"
          inline
          value={direction}
          onChange={(value) => {
            setDirection(value);
            if (value === "borrowing" && type === "staff_advance") setType("term_loan");
          }}
          options={[
            { value: "borrowing", label: "The business borrows", hint: "A liability." },
            { value: "lending", label: "The business lends", hint: "An asset, never an expense." },
          ]}
        />
        <div className="form-grid">
          <Field label={direction === "borrowing" ? "Lender" : "Borrower"} required>
            {(id) => (
              <TextInput
                id={id}
                autoFocus
                value={counterparty}
                onChange={(event) => setCounterparty(event.target.value)}
                placeholder={direction === "borrowing" ? "GCB Bank" : "Kofi Mensah"}
              />
            )}
          </Field>
          <Field label="Type" required>
            {(id) => (
              <Select
                id={id}
                value={type}
                onChange={(event) => setType(event.target.value as LoanInput["loan_type"])}
              >
                {TYPES.filter(
                  (item) => direction === "lending" || item.value !== "staff_advance",
                ).map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Their agreement number" optional>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
              />
            )}
          </Field>
          <Field label={direction === "borrowing" ? "Money arrives in" : "Money leaves from"}>
            {(id) => (
              <Select
                id={id}
                value={payload.financial_account_id ?? ""}
                onChange={(event) => setAccount(event.target.value)}
              >
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Purpose" optional>
          {(id) => (
            <TextInput
              id={id}
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
            />
          )}
        </Field>

        <div className="form-grid">
          <Field label="Principal" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={principal}
                onChange={(event) => setPrincipal(event.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Agreement date" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={agreedOn}
                onChange={(event) => setAgreedOn(event.target.value)}
              />
            )}
          </Field>
          <Field label="Interest" required>
            {(id) => (
              <Select
                id={id}
                value={model}
                onChange={(event) => setModel(event.target.value as InterestModelName)}
              >
                {MODELS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {model === "fixed" ? (
            <Field
              label="Charge agreed"
              required
              hint="The whole interest, spread over the instalments."
            >
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={fixedCharge}
                  onChange={(event) => setFixedCharge(event.target.value)}
                  placeholder="0.00"
                />
              )}
            </Field>
          ) : model !== "none" ? (
            <Field label="Rate a year, percent" required>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={annualRate}
                  onChange={(event) => setAnnualRate(event.target.value)}
                  placeholder="24"
                />
              )}
            </Field>
          ) : null}
          <Field label="Repaid" required>
            {(id) => (
              <Select
                id={id}
                value={frequency}
                onChange={(event) => setFrequency(event.target.value as RepaymentFrequencyName)}
              >
                {FREQUENCIES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Instalments" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={count}
                onChange={(event) => setCount(event.target.value)}
              />
            )}
          </Field>
          <Field label="First instalment due" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={firstDue}
                onChange={(event) => setFirstDue(event.target.value)}
              />
            )}
          </Field>
          {model === "balloon" ? (
            <Field label="Balloon at maturity" required>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={balloon}
                  onChange={(event) => setBalloon(event.target.value)}
                  placeholder="0.00"
                />
              )}
            </Field>
          ) : null}
          <Field
            label="Grace periods"
            optional
            hint="Nothing paid; interest is added to the principal."
          >
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={grace}
                onChange={(event) => setGrace(event.target.value)}
              />
            )}
          </Field>
          <Field label="Interest-only periods" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={interestOnly}
                onChange={(event) => setInterestOnly(event.target.value)}
              />
            )}
          </Field>
          <Field label="Fee each instalment" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={periodicFee}
                onChange={(event) => setPeriodicFee(event.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field label="Arrangement fee" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={upfrontFee}
                onChange={(event) => setUpfrontFee(event.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>
          <Field
            label="Penalty on arrears, percent a year"
            optional
            hint="Only if the agreement provides for one."
          >
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={penalty}
                onChange={(event) => setPenalty(event.target.value)}
                placeholder="0"
              />
            )}
          </Field>
          <Field label="Early settlement fee, percent" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={earlyFee}
                onChange={(event) => setEarlyFee(event.target.value)}
                placeholder="0"
              />
            )}
          </Field>
          <Field label="Repayments settle">
            {(id) => (
              <Select
                id={id}
                value={order}
                onChange={(event) => setOrder(event.target.value as keyof typeof ORDERS)}
              >
                <option value="charges">Penalties, fees and interest, then principal</option>
                <option value="principal">Principal first</option>
              </Select>
            )}
          </Field>
        </div>
        <Checkbox
          label="Prepayment allowed"
          hint="Paying ahead reduces the principal and recalculates the schedule as a new version."
          checked={prepayment}
          onChange={setPrepayment}
        />

        <section className="stack gap-3" aria-live="polite">
          <h3 className="debt__heading">Schedule</h3>
          {!ready ? (
            <p className="caption">
              The schedule appears once the principal, rate and term are entered.
            </p>
          ) : preview.isError ? (
            <Alert tone="error" title={describe(preview.error).message} />
          ) : preview.isPending ? (
            <Skeleton rows={3} />
          ) : (
            <>
              <Details
                rows={[
                  [
                    "First instalment",
                    money(
                      preview.data.rows.find((row) => Number(row.total) > 0)?.total ?? "0",
                      currency,
                    ),
                  ],
                  ["Interest over the term", money(preview.data.summary.interest, currency)],
                  ["Fees over the term", money(preview.data.summary.fees, currency)],
                  ["Repaid in all", money(preview.data.summary.total, currency)],
                  [
                    "Last instalment",
                    preview.data.summary.final_due_on
                      ? formatDate(preview.data.summary.final_due_on)
                      : null,
                  ],
                ]}
              />
              <ScheduleTable rows={preview.data.rows} summary={preview.data.summary} plan />
            </>
          )}
        </section>
      </div>
    </Dialog>
  );
}

function MovementDialog({
  loan,
  kind,
  onClose,
  onDone,
}: {
  loan: LoanDetail;
  kind: "drawdown" | "repay";
  onClose: () => void;
  onDone: (made: LoanDetail, message: string) => void;
}) {
  const currency = useCurrency();
  const accounts = useAccounts();
  const drawing = kind === "drawdown";
  const [amount, setAmount] = useState(
    drawing ? editable(loan.position.undrawn) : editable(loan.position.next_due_amount),
  );
  const [fee, setFee] = useState(
    drawing && loan.transactions.length === 0 ? editable(loan.upfront_fee) : "",
  );
  const [on, setOn] = useState(today());
  const [account, setAccount] = useState(loan.financial_account_id ?? "");
  const [reference, setReference] = useState("");
  const move = useMutation({
    mutationFn: () => {
      const payload = {
        amount,
        on,
        financial_account_id: account || accounts.data?.[0]?.id || null,
        fee: fee || "0",
        reference: reference || null,
      };
      return drawing
        ? api.financing.drawdown(loan.id, payload)
        : api.financing.repay(loan.id, payload);
    },
    onSuccess: (made) =>
      onDone(
        made,
        drawing ? `Drawdown recorded on ${made.number}.` : `Repayment recorded on ${made.number}.`,
      ),
  });
  const borrowing = loan.direction === "borrowing";
  const title = drawing
    ? borrowing
      ? "Record money received from the lender"
      : "Record money paid to the borrower"
    : borrowing
      ? "Record a repayment made"
      : "Record a repayment received";

  return (
    <Dialog
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={move.isPending}
            disabled={!Number(amount)}
            onClick={() => move.mutate()}
          >
            {drawing ? "Record drawdown" : "Record repayment"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {move.isError ? <Alert tone="error" title={describe(move.error).message} /> : null}
        <p className="caption">
          {drawing
            ? `${money(loan.position.undrawn, currency)} remains to be drawn on ${loan.number}.`
            : `It settles what has fallen due, ${loan.allocation_order.join(", then ")}${
                loan.prepayment_allowed ? "; anything beyond prepays principal." : "."
              }`}
        </p>
        <div className="form-grid">
          <Field label="Amount" required>
            {(id) => (
              <TextInput
                id={id}
                autoFocus
                numeric
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            )}
          </Field>
          <Field label="Date" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={on}
                onChange={(event) => setOn(event.target.value)}
              />
            )}
          </Field>
          <Field label="Account" required>
            {(id) => (
              <Select id={id} value={account} onChange={(event) => setAccount(event.target.value)}>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {drawing ? (
            <Field label="Fee deducted" optional>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={fee}
                  onChange={(event) => setFee(event.target.value)}
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
                onChange={(event) => setReference(event.target.value)}
              />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

function SettleDialog({
  loan,
  onClose,
  onDone,
}: {
  loan: LoanDetail;
  onClose: () => void;
  onDone: (made: LoanDetail, message: string) => void;
}) {
  const currency = useCurrency();
  const accounts = useAccounts();
  const [on, setOn] = useState(today());
  const [account, setAccount] = useState(loan.financial_account_id ?? "");
  const quote = useQuery({
    queryKey: ["financing", "quote", loan.id, on],
    queryFn: () => api.financing.quote(loan.id, on),
    retry: false,
  });
  const settle = useMutation({
    mutationFn: () =>
      api.financing.settle(loan.id, {
        on,
        financial_account_id: account || accounts.data?.[0]?.id || null,
      }),
    onSuccess: (made) => onDone(made, `${made.number} settled.`),
  });
  return (
    <Dialog
      title={`Settle ${loan.number} early`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            busy={settle.isPending}
            disabled={!quote.data}
            onClick={() => settle.mutate()}
          >
            {quote.data ? `Settle for ${money(quote.data.total, currency)}` : "Settle"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {settle.isError ? <Alert tone="error" title={describe(settle.error).message} /> : null}
        {quote.isError ? <Alert tone="error" title={describe(quote.error).message} /> : null}
        <div className="form-grid">
          <Field label="Settlement date" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={on}
                onChange={(event) => setOn(event.target.value)}
              />
            )}
          </Field>
          <Field label="Account" required>
            {(id) => (
              <Select id={id} value={account} onChange={(event) => setAccount(event.target.value)}>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        {quote.data ? (
          <Details
            rows={[
              ["Principal outstanding", money(quote.data.principal, currency)],
              ["Interest due", money(quote.data.interest_due, currency)],
              ["Interest to the day", money(quote.data.interest_accrued, currency)],
              ["Fees due", money(quote.data.fees_due, currency)],
              ["Penalties", money(quote.data.penalties_due, currency)],
              ["Early settlement fee", money(quote.data.early_settlement_fee, currency)],
              ["To settle", <strong key="total">{money(quote.data.total, currency)}</strong>],
            ]}
          />
        ) : (
          <Skeleton rows={4} />
        )}
        <p className="caption">
          Instalments still to come are released. The agreement and its history remain.
        </p>
      </div>
    </Dialog>
  );
}

function RevisionDialog({
  loan,
  onClose,
  onDone,
}: {
  loan: LoanDetail;
  onClose: () => void;
  onDone: (made: LoanDetail, message: string) => void;
}) {
  const start = today();
  const [kind, setKind] = useState<RevisionKindName>("reschedule");
  const [reason, setReason] = useState("");
  const [effective, setEffective] = useState(start);
  const [firstDue, setFirstDue] = useState(nextMonth(start));
  const [count, setCount] = useState(String(loan.instalments));
  const [annualRate, setAnnualRate] = useState(editable(loan.annual_rate));
  const [grace, setGrace] = useState(kind === "payment_holiday" ? "1" : "0");
  const propose = useMutation({
    mutationFn: () =>
      api.financing.propose(loan.id, {
        kind,
        reason,
        effective_on: effective,
        first_due_on: firstDue,
        instalments: Number(count) || 1,
        annual_rate: annualRate || null,
        grace_periods: Number(grace) || 0,
      }),
    onSuccess: (made) => onDone(made, "Revision proposed. Nothing changes until it is approved."),
  });
  return (
    <Dialog
      title={`Propose a revision to ${loan.number}`}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={propose.isPending}
            disabled={!reason.trim()}
            onClick={() => propose.mutate()}
          >
            Propose revision
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {propose.isError ? <Alert tone="error" title={describe(propose.error).message} /> : null}
        <p className="caption">
          The revision is built on the principal outstanding when it takes effect. Unpaid interest
          and charges are carried into it. The current schedule is kept as it stands.
        </p>
        <RadioGroup
          legend="Kind"
          inline
          value={kind}
          onChange={(value) => {
            setKind(value);
            if (value === "payment_holiday" && grace === "0") setGrace("1");
          }}
          options={[
            { value: "reschedule", label: "Reschedule" },
            { value: "payment_holiday", label: "Payment holiday" },
            { value: "deferment", label: "Deferment" },
            { value: "interest_change", label: "Interest change" },
            { value: "restructure", label: "Restructure" },
          ]}
        />
        <Field label="Reason" required>
          {(id) => (
            <TextArea
              id={id}
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
        <div className="form-grid">
          <Field label="Takes effect" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={effective}
                onChange={(event) => setEffective(event.target.value)}
              />
            )}
          </Field>
          <Field label="First revised instalment" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={firstDue}
                onChange={(event) => setFirstDue(event.target.value)}
              />
            )}
          </Field>
          <Field label="Instalments" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={count}
                onChange={(event) => setCount(event.target.value)}
              />
            )}
          </Field>
          <Field label="Rate a year, percent">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={annualRate}
                onChange={(event) => setAnnualRate(event.target.value)}
              />
            )}
          </Field>
          <Field
            label="Periods with nothing to pay"
            optional
            hint="Interest is added to the principal."
          >
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={grace}
                onChange={(event) => setGrace(event.target.value)}
              />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

function SecurityDialog({
  loan,
  onClose,
  onDone,
}: {
  loan: LoanDetail;
  onClose: () => void;
  onDone: (made: LoanDetail, message: string) => void;
}) {
  const [kind, setKind] = useState<"collateral" | "guarantor" | "covenant">("collateral");
  const [description, setDescription] = useState("");
  const [party, setParty] = useState("");
  const [value, setValue] = useState("");
  const [review, setReview] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api.financing.addSecurity(loan.id, {
        kind,
        description,
        party_name: party || null,
        value: value || null,
        review_on: review || null,
      }),
    onSuccess: (made) => onDone(made, "Security recorded."),
  });
  return (
    <Dialog
      title="Add security"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={add.isPending}
            disabled={!description.trim()}
            onClick={() => add.mutate()}
          >
            Add security
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {add.isError ? <Alert tone="error" title={describe(add.error).message} /> : null}
        <RadioGroup
          legend="Kind"
          inline
          value={kind}
          onChange={setKind}
          options={[
            { value: "collateral", label: "Collateral" },
            { value: "guarantor", label: "Guarantor" },
            { value: "covenant", label: "Covenant" },
          ]}
        />
        <Field label="Description" required>
          {(id) => (
            <TextInput
              id={id}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          )}
        </Field>
        <div className="form-grid">
          <Field label={kind === "guarantor" ? "Guarantor" : "Held by"} optional>
            {(id) => (
              <TextInput id={id} value={party} onChange={(event) => setParty(event.target.value)} />
            )}
          </Field>
          {kind !== "covenant" ? (
            <Field label="Value" optional>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />
              )}
            </Field>
          ) : null}
          <Field label="Review or renewal date" optional>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={review}
                onChange={(event) => setReview(event.target.value)}
              />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

function RefinanceDialog({
  loan,
  onClose,
  onDone,
  onOpen,
}: {
  loan: LoanDetail;
  onClose: () => void;
  onDone: (made: LoanDetail, message: string) => void;
  onOpen: (id: string) => void;
}) {
  const candidates = useQuery({
    queryKey: ["financing", "loans", loan.direction, "active"],
    queryFn: () => api.financing.loans(loan.direction, "active"),
  });
  const [replacement, setReplacement] = useState("");
  const choices = (candidates.data ?? []).filter((item) => item.id !== loan.id);
  const refinance = useMutation({
    mutationFn: () => api.financing.refinance(loan.id, replacement || choices[0]!.id),
    onSuccess: (made) => onDone(made, `${made.number} refinanced by ${made.refinanced_by}.`),
  });
  return (
    <Dialog
      title={`Refinance ${loan.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={refinance.isPending}
            disabled={choices.length === 0}
            onClick={() => refinance.mutate()}
          >
            Refinance
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {refinance.isError ? (
          <Alert tone="error" title={describe(refinance.error).message} />
        ) : null}
        <p className="caption">
          What {loan.number} owes moves to the new agreement as its principal. No cash moves. Record
          and approve the new agreement first, undrawn.
        </p>
        {choices.length === 0 ? (
          <EmptyState title="No active agreement to refinance into." />
        ) : (
          <Field label="New agreement" required>
            {(id) => (
              <Select
                id={id}
                value={replacement}
                onChange={(event) => setReplacement(event.target.value)}
              >
                {choices.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.number} · {item.counterparty_name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        {choices.length === 0 ? null : (
          <Button tone="plain" size="compact" onClick={() => onOpen(replacement || choices[0]!.id)}>
            Open the new agreement
          </Button>
        )}
      </div>
    </Dialog>
  );
}

function NoteDialog({
  title,
  label,
  hint,
  required,
  confirmLabel,
  busy,
  onConfirm,
  onClose,
}: {
  title: string;
  label: string;
  hint?: string;
  required?: boolean;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: (text: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  return (
    <Dialog
      title={title}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={busy}
            disabled={Boolean(required) && !text.trim()}
            onClick={() => onConfirm(text.trim())}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <Field label={label} required={required} optional={!required} hint={hint}>
        {(id) => (
          <TextArea
            id={id}
            rows={3}
            autoFocus
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}

function OtherDebtDialog({ onClose }: { onClose: () => void }) {
  const accounts = useAccounts();
  const queries = useQueryClient();
  const toast = useToast();
  const [owedToUs, setOwedToUs] = useState<"yes" | "no">("yes");
  const [counterparty, setCounterparty] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const [account, setAccount] = useState("");
  const [reference, setReference] = useState("");
  const [due, setDue] = useState("");
  const record = useMutation({
    mutationFn: () =>
      api.financing.recordOther({
        owed_to_us: owedToUs === "yes",
        counterparty_name: counterparty,
        description,
        amount,
        financial_account_id: account || accounts.data![0]!.id,
        reference: reference || null,
        due_on: due || null,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["financing"] });
      void queries.invalidateQueries({ queryKey: ["treasury"] });
      toast.show("success", `${made.number} recorded.`);
      onClose();
    },
  });
  return (
    <Dialog
      title="Record another debt"
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
            disabled={!counterparty || !description || !Number(amount)}
            onClick={() => record.mutate()}
          >
            Record debt
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
        <p className="caption">
          For an amount no invoice, expense, tax obligation or agreement records: a refundable
          deposit paid or held, an advance to a supplier.
        </p>
        <RadioGroup
          legend="Who owes"
          inline
          value={owedToUs}
          onChange={setOwedToUs}
          options={[
            {
              value: "yes",
              label: "Owed to the business",
              hint: "Money paid out that comes back.",
            },
            { value: "no", label: "Owed by the business", hint: "Money held that goes back." },
          ]}
        />
        <div className="form-grid">
          <Field label="Counterparty" required>
            {(id) => (
              <TextInput
                id={id}
                autoFocus
                value={counterparty}
                onChange={(event) => setCounterparty(event.target.value)}
              />
            )}
          </Field>
          <Field label="Amount" required>
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
          <Field label={owedToUs === "yes" ? "Paid from" : "Received into"} required>
            {(id) => (
              <Select id={id} value={account} onChange={(event) => setAccount(event.target.value)}>
                {(accounts.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Due" optional>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={due}
                onChange={(event) => setDue(event.target.value)}
              />
            )}
          </Field>
          <Field label="Reference" optional>
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
              />
            )}
          </Field>
        </div>
        <Field label="What it is" required>
          {(id) => (
            <TextInput
              id={id}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Refundable deposit on the Osu shop"
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
