/** Budgets: drawn, approved, versioned, and compared with what was posted. */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { BudgetDetail, BudgetInput, ComparisonCell } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  Details,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  Switch,
  Tabs,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, dateTime, editable, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

import { PostingsDialog } from "./shared";

const TONE: Record<string, Tone> = {
  draft: "neutral",
  in_review: "warning",
  approved: "ok",
  superseded: "neutral",
  rejected: "error",
};

export function Budgets() {
  const { can } = useSession();
  const currency = useCurrency();
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ["planning", "budgets"], queryFn: api.planning.budgets });

  if (openId)
    return <BudgetView budgetId={openId} onBack={() => setOpenId(null)} onOpen={setOpenId} />;

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <span />
        <div className="row gap-2">
          <ControlSwitch />
          {can("budget.manage") ? (
            <Button tone="primary" onClick={() => setCreating(true)}>
              Draw up a budget
            </Button>
          ) : null}
        </div>
      </div>
      <Card title="Budgets" flush>
        {list.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={4} />
          </div>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState title="No budget has been drawn up." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Budget</th>
                <th>Period</th>
                <th>Status</th>
                <th>Owner</th>
                <th className="numeric">Planned</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setOpenId(item.id)}
                    >
                      {item.name}
                    </button>
                    <div className="cell-note">
                      Version {item.version} · {item.lines} lines
                    </div>
                  </td>
                  <td className="nowrap">
                    {formatDate(item.starts_on)} to {formatDate(item.ends_on)}
                    <div className="cell-note">{item.period_kind}</div>
                  </td>
                  <td>
                    <StatusChip tone={TONE[item.state] ?? "neutral"}>{item.state_label}</StatusChip>
                  </td>
                  <td>{item.owner ?? "—"}</td>
                  <td className="numeric">{money(item.total, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {creating ? (
        <BudgetDialog
          onClose={() => setCreating(false)}
          onSaved={(id) => {
            setCreating(false);
            setOpenId(id);
          }}
        />
      ) : null}
    </div>
  );
}

function ControlSwitch() {
  const { can } = useSession();
  const queries = useQueryClient();
  const control = useQuery({ queryKey: ["planning", "control"], queryFn: api.planning.control });
  const set = useMutation({
    mutationFn: api.planning.setControl,
    onSuccess: (made) => queries.setQueryData(["planning", "control"], made),
  });
  if (!control.data) return null;
  return (
    <Switch
      label="Hold expenses that exceed an approved budget"
      checked={control.data.enabled}
      disabled={!can("budget.approve") || set.isPending}
      onChange={(next) => set.mutate(next)}
    />
  );
}

function BudgetDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const year = new Date().getFullYear();
  const [name, setName] = useState(`Operating budget ${year}`);
  const [kind, setKind] = useState<BudgetInput["period_kind"]>("monthly");
  const [start, setStart] = useState(`${year}-01-01`);
  const [end, setEnd] = useState(`${year}-12-31`);
  const [assumptions, setAssumptions] = useState("");
  const queries = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      api.planning.createBudget({
        name,
        period_kind: kind,
        starts_on: start,
        ends_on: end,
        assumptions,
      }),
    onSuccess: (made) => {
      void queries.invalidateQueries({ queryKey: ["planning", "budgets"] });
      onSaved(made.id);
    },
  });
  return (
    <Dialog
      title="Draw up a budget"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={!name.trim()}
            onClick={() => create.mutate()}
          >
            Create budget
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {create.isError ? <Alert tone="error" title={describe(create.error).message} /> : null}
        <div className="form-grid">
          <Field label="Name" required>
            {(id) => (
              <TextInput id={id} autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            )}
          </Field>
          <Field label="Columns" required>
            {(id) => (
              <Select
                id={id}
                value={kind}
                onChange={(e) => setKind(e.target.value as BudgetInput["period_kind"])}
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annual">Annual</option>
                <option value="custom">Custom period</option>
              </Select>
            )}
          </Field>
          <Field label="From" required>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            )}
          </Field>
          <Field label="To" required>
            {(id) => (
              <TextInput id={id} type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            )}
          </Field>
        </div>
        <Field
          label="Assumptions"
          optional
          hint="What the plan supposes, so it can be argued with later."
        >
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={assumptions}
              onChange={(e) => setAssumptions(e.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

type Pane = "plan" | "comparison" | "history";

function BudgetView({
  budgetId,
  onBack,
  onOpen,
}: {
  budgetId: string;
  onBack: () => void;
  onOpen: (id: string) => void;
}) {
  const { can } = useSession();
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const [pane, setPane] = useState<Pane>("comparison");
  const [returning, setReturning] = useState(false);
  const budget = useQuery({
    queryKey: ["planning", "budget", budgetId],
    queryFn: () => api.planning.budget(budgetId),
  });
  const done = (made: BudgetDetail, message: string) => {
    queries.setQueryData(["planning", "budget", budgetId], made);
    void queries.invalidateQueries({ queryKey: ["planning"] });
    toast.show("success", message);
  };
  const act = useMutation({
    mutationFn: (step: "submit" | "approve" | "revise") =>
      step === "submit"
        ? api.planning.submit(budgetId)
        : step === "approve"
          ? api.planning.approve(budgetId, null)
          : api.planning.revise(budgetId),
    onSuccess: (made, step) => {
      if (step === "revise") {
        onOpen(made.id);
        toast.show(
          "success",
          `Version ${made.version} drafted. Version ${made.version - 1} stands until it is approved.`,
        );
        return;
      }
      done(made, `${made.name}: ${made.state_label.toLowerCase()}.`);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const reject = useMutation({
    mutationFn: (reason: string) => api.planning.reject(budgetId, reason),
    onSuccess: (made) => {
      setReturning(false);
      done(made, "Returned for changes.");
    },
  });

  if (budget.isError) return <Alert tone="error" title={describe(budget.error).message} />;
  if (budget.isPending) return <Skeleton rows={6} height={40} />;
  const data = budget.data;
  const editable = data.state === "draft" || data.state === "rejected";

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Button tone="plain" onClick={onBack}>
          ← All budgets
        </Button>
        <div className="row gap-2">
          {editable && can("budget.manage") ? (
            <Button
              tone="primary"
              busy={act.isPending}
              disabled={data.budget_lines.length === 0}
              onClick={() => act.mutate("submit")}
            >
              Submit for approval
            </Button>
          ) : null}
          {data.state === "in_review" && can("budget.approve") ? (
            <>
              <Button onClick={() => setReturning(true)}>Return</Button>
              <Button tone="primary" busy={act.isPending} onClick={() => act.mutate("approve")}>
                Approve
              </Button>
            </>
          ) : null}
          {data.state === "approved" && can("budget.manage") ? (
            <Button busy={act.isPending} onClick={() => act.mutate("revise")}>
              Revise as a new version
            </Button>
          ) : null}
        </div>
      </div>
      <Card
        title={`${data.name} · version ${data.version}`}
        subtitle={`${formatDate(data.starts_on)} to ${formatDate(data.ends_on)}, ${data.period_kind}`}
        actions={<StatusChip tone={TONE[data.state] ?? "neutral"}>{data.state_label}</StatusChip>}
      >
        <Details
          rows={[
            ["Planned in all", money(data.total, currency)],
            ["Owner", data.owner],
            ["Approved by", data.approved_by],
            ["Assumptions", data.assumptions],
          ]}
        />
      </Card>
      <Tabs
        items={[
          { id: "comparison", label: "Against actual" },
          { id: "plan", label: editable ? "Plan" : "Plan as approved" },
          { id: "history", label: "Versions and decisions" },
        ]}
        active={pane}
        onChange={setPane}
      />
      {pane === "plan" ? (
        <PlanEditor budget={data} editable={editable && can("budget.manage")} onSaved={done} />
      ) : null}
      {pane === "comparison" ? <ComparisonView budget={data} /> : null}
      {pane === "history" ? <History budget={data} onOpen={onOpen} /> : null}
      {returning ? (
        <ConfirmDialog
          title={`Return ${data.name}`}
          record={`Version ${data.version}, ${money(data.total, currency)}`}
          consequence="It goes back to draft for changes. Nothing else is affected."
          confirmLabel="Return budget"
          requireReason
          busy={reject.isPending}
          onConfirm={(reason) => reject.mutate(reason)}
          onClose={() => setReturning(false)}
        />
      ) : null}
    </div>
  );
}

function PlanEditor({
  budget,
  editable: canEdit,
  onSaved,
}: {
  budget: BudgetDetail;
  editable: boolean;
  onSaved: (made: BudgetDetail, message: string) => void;
}) {
  const currency = useCurrency();
  const [rows, setRows] = useState(() => budget.budget_lines.map(toDraft));
  const [adding, setAdding] = useState(false);
  useEffect(() => setRows(budget.budget_lines.map(toDraft)), [budget]);
  const save = useMutation({
    mutationFn: () =>
      api.planning.setLines(
        budget.id,
        rows.map((row) => ({
          account_code: row.account_code,
          group_code: row.group_code,
          analysis_code_id: row.analysis_code_id,
          amounts: row.amounts,
          note: row.note,
        })),
      ),
    onSuccess: (made) => onSaved(made, "Plan saved."),
  });

  return (
    <Card
      title="Plan"
      subtitle="One line for each account or category, column by column."
      actions={
        canEdit ? (
          <div className="row gap-2">
            <Button onClick={() => setAdding(true)}>Add a line</Button>
            <Button tone="primary" busy={save.isPending} onClick={() => save.mutate()}>
              Save plan
            </Button>
          </div>
        ) : undefined
      }
      flush
    >
      {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
      {rows.length === 0 ? (
        <EmptyState title="Nothing planned yet." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Line</th>
                {budget.periods.map((period) => (
                  <th key={period.key} className="numeric nowrap">
                    {period.label}
                  </th>
                ))}
                <th className="numeric">Planned</th>
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.key}>
                  <td>
                    {row.label}
                    {row.analysis ? <div className="cell-note">{row.analysis}</div> : null}
                  </td>
                  {budget.periods.map((period) => (
                    <td key={period.key} className="numeric">
                      {canEdit ? (
                        <TextInput
                          aria-label={`${row.label}, ${period.label}`}
                          numeric
                          inputMode="decimal"
                          className="budget__cell"
                          value={row.amounts[period.key] ?? "0"}
                          onChange={(event) =>
                            setRows(
                              rows.map((item, at) =>
                                at === index
                                  ? {
                                      ...item,
                                      amounts: {
                                        ...item.amounts,
                                        [period.key]: event.target.value,
                                      },
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                      ) : (
                        money(row.amounts[period.key] ?? "0", currency)
                      )}
                    </td>
                  ))}
                  <td className="numeric">{row.saved ? money(row.saved, currency) : "—"}</td>
                  {canEdit ? (
                    <td>
                      <Button
                        size="compact"
                        tone="plain"
                        onClick={() => setRows(rows.filter((_, at) => at !== index))}
                      >
                        Remove
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding ? (
        <LineDialog
          budget={budget}
          onClose={() => setAdding(false)}
          onAdd={(row) => {
            setRows([...rows, row]);
            setAdding(false);
          }}
        />
      ) : null}
    </Card>
  );
}

interface DraftRow {
  key: string;
  label: string;
  account_code: string | null;
  group_code: string | null;
  analysis_code_id: string | null;
  analysis: string | null;
  amounts: Record<string, string>;
  note: string | null;
  saved: string | null;
}

function toDraft(line: BudgetDetail["budget_lines"][number]): DraftRow {
  return {
    key: line.id,
    label: line.label,
    account_code: line.account_code,
    group_code: line.group_code,
    analysis_code_id: line.analysis_code_id,
    analysis: line.analysis,
    amounts: Object.fromEntries(Object.entries(line.amounts).map(([key, v]) => [key, editable(v)])),
    note: line.note,
    saved: line.total,
  };
}

function LineDialog({
  budget,
  onClose,
  onAdd,
}: {
  budget: BudgetDetail;
  onClose: () => void;
  onAdd: (row: DraftRow) => void;
}) {
  const accounts = useQuery({
    queryKey: ["ledger", "accounts", "all"],
    queryFn: () => api.ledger.accounts(),
  });
  const codes = useQuery({ queryKey: ["planning", "codes"], queryFn: () => api.planning.codes() });
  const [code, setCode] = useState("");
  const [analysis, setAnalysis] = useState("");
  const [each, setEach] = useState("");
  const choices = (accounts.data ?? []).filter(
    (item) => item.kind === "revenue" || item.kind === "expense" || item.code.startsWith("15"),
  );
  const account = choices.find((item) => item.code === code);
  const tag = (codes.data ?? []).find((item) => item.id === analysis);
  return (
    <Dialog
      title="Add a line"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            disabled={!account}
            onClick={() =>
              account &&
              onAdd({
                key: `new-${account.code}-${analysis}`,
                label: `${account.code} ${account.name}`,
                account_code: account.code,
                group_code: null,
                analysis_code_id: analysis || null,
                analysis: tag ? `${tag.code} ${tag.name}` : null,
                amounts: Object.fromEntries(
                  budget.periods.map((period) => [period.key, each || "0"]),
                ),
                note: null,
                saved: null,
              })
            }
          >
            Add line
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <Field label="Account" required>
          {(id) => (
            <Select id={id} value={code} onChange={(e) => setCode(e.target.value)}>
              <option value="">Choose an account</option>
              {choices.map((item) => (
                <option key={item.id} value={item.code}>
                  {item.code} {item.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Department, project or cost centre" optional>
          {(id) => (
            <Select id={id} value={analysis} onChange={(e) => setAnalysis(e.target.value)}>
              <option value="">Any</option>
              {(codes.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.kind_label}: {item.code} {item.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field
          label="Amount in each column"
          optional
          hint="Fill every column alike; adjust them after."
        >
          {(id) => (
            <TextInput
              id={id}
              numeric
              inputMode="decimal"
              value={each}
              onChange={(e) => setEach(e.target.value)}
              placeholder="0.00"
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function ComparisonView({ budget }: { budget: BudgetDetail }) {
  const currency = useCurrency();
  const [period, setPeriod] = useState("total");
  const [drill, setDrill] = useState<{ lineId: string; label: string; key: string } | null>(null);
  const comparison = useQuery({
    queryKey: ["planning", "comparison", budget.id],
    queryFn: () => api.planning.comparison(budget.id),
  });
  if (comparison.isPending) return <Skeleton rows={6} />;
  if (comparison.isError) return <Alert tone="error" title={describe(comparison.error).message} />;
  const data = comparison.data;
  const pick = (cells: ComparisonCell[], total: ComparisonCell) =>
    period === "total" ? total : (cells.find((cell) => cell.period_key === period) ?? total);
  const toDate = period === "total";

  return (
    <Card
      title="Against actual"
      subtitle={`To ${formatDate(data.as_of)}. ${data.bases.forecast}.`}
      actions={
        <Select aria-label="Period" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="total">To date</option>
          {data.periods.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </Select>
      }
      flush
    >
      {data.lines.length === 0 ? (
        <EmptyState title="Nothing is planned yet." />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Line</th>
                <th className="numeric">
                  Budget<div className="cell-note">Plan</div>
                </th>
                <th className="numeric">
                  Actual<div className="cell-note">Posted</div>
                </th>
                <th className="numeric">Variance</th>
                <th className="numeric">Used</th>
                <th className="numeric">
                  A year before<div className="cell-note">Posted</div>
                </th>
                {period === "total" ? (
                  <th className="numeric">
                    Outturn<div className="cell-note">Forecast</div>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => {
                const cell = pick(line.cells, line.to_date);
                const moved = cell.budget !== cell.original;
                return (
                  <tr key={line.line_id}>
                    <td>
                      {line.label}
                      <div className="cell-note">
                        {line.nature === "income" ? "Income" : "Cost"}
                        {line.analysis ? ` · ${line.analysis}` : ""}
                      </div>
                    </td>
                    <td className="numeric">
                      {money(cell.budget, currency)}
                      {toDate ? (
                        <div className="cell-note">of {money(line.total.budget, currency)}</div>
                      ) : null}
                      {moved ? (
                        <div className="cell-note">
                          {money(cell.original, currency)} before transfers
                        </div>
                      ) : null}
                    </td>
                    <td className="numeric">
                      {period === "total" ? (
                        money(cell.actual, currency)
                      ) : (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            setDrill({ lineId: line.line_id, label: line.label, key: period })
                          }
                        >
                          {money(cell.actual, currency)}
                        </button>
                      )}
                    </td>
                    <td className="numeric">
                      <Variance cell={cell} />
                    </td>
                    <td className="numeric">
                      {cell.variance.utilisation ? `${cell.variance.utilisation}%` : "—"}
                    </td>
                    <td className="numeric">{money(cell.prior, currency)}</td>
                    {period === "total" ? (
                      <td className="numeric">{money(line.forecast, currency)}</td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              {[
                { label: "Income", cell: data.income },
                { label: "Costs", cell: data.costs },
              ].map(({ label, cell }) => (
                <tr key={label}>
                  <th>{label}</th>
                  <th className="numeric">{money(cell.budget, currency)}</th>
                  <th className="numeric">{money(cell.actual, currency)}</th>
                  <th className="numeric">
                    <Variance cell={cell} />
                  </th>
                  <th className="numeric">
                    {cell.variance.utilisation ? `${cell.variance.utilisation}%` : "—"}
                  </th>
                  <th className="numeric">{money(cell.prior, currency)}</th>
                  {period === "total" ? <th /> : null}
                </tr>
              ))}
            </tfoot>
          </table>
        </div>
      )}
      {period === "total" ? (
        <p className="caption" style={{ padding: "var(--space-3) var(--space-4)" }}>
          Choose a period to open the postings behind an actual figure.
        </p>
      ) : null}
      {drill ? (
        <PostingsDialog
          title={`${drill.label}: postings`}
          queryKey={["planning", "drill", budget.id, drill.lineId, drill.key]}
          load={() => api.planning.budgetDrill(budget.id, drill.lineId, drill.key)}
          onClose={() => setDrill(null)}
        />
      ) : null}
    </Card>
  );
}

function Variance({ cell }: { cell: ComparisonCell }) {
  const currency = useCurrency();
  const { favourable, absolute, percent } = cell.variance;
  if (favourable === null) return <span>—</span>;
  return (
    <span className="variance">
      <StatusChip tone={favourable ? "ok" : "warning"}>
        {favourable ? "Favourable" : "Adverse"}
      </StatusChip>
      <span>
        {money(absolute, currency)}
        {percent ? ` (${percent}%)` : ""}
      </span>
    </span>
  );
}

function History({ budget, onOpen }: { budget: BudgetDetail; onOpen: (id: string) => void }) {
  const currency = useCurrency();
  const toast = useToast();
  const queries = useQueryClient();
  const { can } = useSession();
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api.planning.decide(id, approve, approve ? null : "Declined"),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["planning"] });
      toast.show("success", "Decided.");
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  return (
    <div className="debt__pair">
      <Card title="Versions" flush>
        <table className="table">
          <tbody>
            {budget.versions.map((version) => (
              <tr key={version.id}>
                <td>
                  {version.id === budget.id ? (
                    `Version ${version.version}`
                  ) : (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => onOpen(version.id)}
                    >
                      Version {version.version}
                    </button>
                  )}
                </td>
                <td>
                  <StatusChip tone={TONE[version.state] ?? "neutral"}>
                    {version.state_label}
                  </StatusChip>
                </td>
                <td className="numeric">{money(version.total, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Decisions" flush>
        <table className="table">
          <tbody>
            {budget.decisions.map((item, index) => (
              <tr key={index}>
                <td className="nowrap">{dateTime(item.occurred_at)}</td>
                <td>
                  Version {item.version}, {item.action}
                  {item.note ? <div className="cell-note">{item.note}</div> : null}
                </td>
                <td>{item.actor ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {budget.requests.length > 0 ? (
        <Card title="Transfers and leave to overspend" flush>
          <table className="table">
            <tbody>
              {budget.requests.map((item) => (
                <tr key={item.id}>
                  <td className="table__cell--wrap">
                    {item.kind === "transfer" ? "Transfer" : "Overspend"}, {item.period_key}
                    <div className="cell-note">{item.reason}</div>
                  </td>
                  <td className="numeric">{money(item.amount, currency)}</td>
                  <td>
                    {item.state === "pending" && can("budget.approve") ? (
                      <div className="table__actions">
                        <Button
                          size="compact"
                          tone="plain"
                          onClick={() => decide.mutate({ id: item.id, approve: true })}
                        >
                          Approve
                        </Button>
                        <Button
                          size="compact"
                          tone="plain"
                          onClick={() => decide.mutate({ id: item.id, approve: false })}
                        >
                          Decline
                        </Button>
                      </div>
                    ) : (
                      <StatusChip
                        tone={
                          item.state === "approved"
                            ? "ok"
                            : item.state === "pending"
                              ? "warning"
                              : "neutral"
                        }
                      >
                        {item.state}
                      </StatusChip>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
