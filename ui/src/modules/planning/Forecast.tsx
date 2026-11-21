/** The rolling cash forecast: scenarios, what-ifs, planned items and the low-cash line. */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { CashForecast, ChartData, ForecastAssumptions } from "@/api/types";
import { ChartPanel } from "@/components/charts";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  Measure,
  RadioGroup,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, editable, money } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

import { today } from "./shared";

const HORIZONS = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "60", label: "60 days" },
  { id: "90", label: "90 days" },
  { id: "180", label: "180 days" },
];

function chartOf(forecast: CashForecast): ChartData {
  return {
    id: "cash_forecast",
    title: "Cash forecast",
    kind: "columns",
    unit: "currency",
    starts_on: forecast.starts_on,
    ends_on: forecast.ends_on,
    interval: forecast.bucket_days === 1 ? "day" : "week",
    series: [
      {
        key: "inflow",
        label: "Expected in",
        basis: "forecast",
        basis_label: "Forecast",
        role: "primary",
      },
      {
        key: "outflow",
        label: "Expected out",
        basis: "forecast",
        basis_label: "Forecast",
        role: "supporting",
      },
      {
        key: "closing",
        label: "Cash and bank, forecast",
        basis: "forecast",
        basis_label: "Forecast",
        role: "level",
      },
    ],
    points: forecast.rows.map((row) => ({
      key: row.starts_on,
      label:
        forecast.bucket_days === 1
          ? formatDate(row.starts_on).slice(0, 6)
          : `w/c ${formatDate(row.starts_on).slice(0, 6)}`,
      values: { inflow: row.inflow, outflow: row.outflow, closing: row.closing },
      drill: {},
    })),
    note: null,
  };
}

export function Forecast() {
  const { can } = useSession();
  const currency = useCurrency();
  const [horizon, setHorizon] = useState("30");
  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const [whatIf, setWhatIf] = useState<ForecastAssumptions | null>(null);
  const [editing, setEditing] = useState(false);
  const [planning, setPlanning] = useState(false);
  const scenarios = useQuery({
    queryKey: ["planning", "scenarios"],
    queryFn: api.planning.scenarios,
  });
  const chosen = scenarioId ?? scenarios.data?.find((item) => item.is_default)?.id ?? null;
  const forecast = useQuery({
    queryKey: ["planning", "forecast", horizon, chosen, whatIf],
    queryFn: () =>
      whatIf
        ? api.planning.whatIf({
            horizon_days: Number(horizon),
            scenario_id: chosen,
            assumptions: whatIf,
          })
        : api.planning.forecast(Number(horizon), chosen ?? undefined),
    enabled: scenarios.isSuccess,
  });
  const data = forecast.data;
  const chart = useMemo(() => (data ? chartOf(data) : null), [data]);

  return (
    <div className="stack gap-5">
      <div className="command__bar">
        <Tabs items={HORIZONS} active={horizon} onChange={setHorizon} />
        <div className="row gap-2">
          <Select
            aria-label="Scenario"
            value={chosen ?? ""}
            onChange={(event) => {
              setScenarioId(event.target.value);
              setWhatIf(null);
            }}
          >
            {(scenarios.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Button onClick={() => setEditing(true)}>Test a what-if</Button>
          {can("budget.manage") ? (
            <Button onClick={() => setPlanning(true)}>Plan an item</Button>
          ) : null}
        </div>
      </div>

      {forecast.isError ? <Alert tone="error" title={describe(forecast.error).message} /> : null}
      {!data ? (
        <Skeleton rows={4} height={96} />
      ) : (
        <>
          {whatIf ? (
            <Alert tone="info" title={`What-if on the ${data.scenario.toLowerCase()}`}>
              <Button size="compact" tone="plain" onClick={() => setWhatIf(null)}>
                Return to the scenario as saved
              </Button>
            </Alert>
          ) : null}
          {data.warnings.map((text) => (
            <Alert key={text} tone="warning" title={text} />
          ))}
          <div className="measure-grid">
            <Measure
              label="Cash and bank today"
              value={money(data.opening_cash, currency)}
              footer={<StatusChip tone="info">Actual, posted</StatusChip>}
            />
            <Measure
              label={`At ${formatDate(data.ends_on)}`}
              value={money(data.closing_cash, currency)}
              footer={<StatusChip tone="neutral">{data.basis_label}</StatusChip>}
            />
            <Measure
              label="Lowest point"
              value={money(data.lowest, currency)}
              period={formatDate(data.lowest_on)}
              footer={<StatusChip tone="neutral">{data.basis_label}</StatusChip>}
            />
            <ThresholdTile threshold={data.threshold} />
          </div>
          <Card
            title="Expected in and out"
            subtitle={`${data.scenario}. Every figure here is a forecast; none is posted.`}
          >
            {chart ? <ChartPanel chart={chart} currency={currency} /> : null}
          </Card>
          <div className="debt__pair">
            <Card title="By class" flush>
              <table className="table">
                <tbody>
                  {Object.entries(data.class_labels).map(([key, label]) =>
                    data.totals[key] ? (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="numeric">{money(data.totals[key] ?? "0", currency)}</td>
                      </tr>
                    ) : null,
                  )}
                </tbody>
              </table>
            </Card>
            <Card title="Assumptions" flush>
              <table className="table">
                <tbody>
                  <tr>
                    <td>Customers pay</td>
                    <td>
                      {describeDays(Number(data.assumptions.collection_delay_days))},{" "}
                      {editable(String(data.assumptions.collection_rate))}% of what they owe
                    </td>
                  </tr>
                  <tr>
                    <td>The business pays</td>
                    <td>{describeDays(Number(data.assumptions.payment_delay_days))}</td>
                  </tr>
                  <tr>
                    <td>Recurring costs</td>
                    <td>{data.assumptions.include_recurring ? "Included" : "Left out"}</td>
                  </tr>
                  <tr>
                    <td>What-ifs added</td>
                    <td>{data.assumptions.extra_flows.length || "None"}</td>
                  </tr>
                </tbody>
              </table>
            </Card>
          </div>
          <Card
            title="What it is made of"
            subtitle="Each expected receipt and payment, by date."
            flush
          >
            {data.items.length === 0 ? (
              <EmptyState title="Nothing is expected in this window." />
            ) : (
              <div className="table-frame">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Expected</th>
                      <th>Reference</th>
                      <th>Class</th>
                      <th className="numeric">In</th>
                      <th className="numeric">Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item, index) => (
                      <tr key={`${item.reference}-${index}`}>
                        <td className="nowrap">{formatDate(item.on)}</td>
                        <td>
                          {item.reference}
                          {item.counterparty ? (
                            <div className="cell-note">{item.counterparty}</div>
                          ) : null}
                        </td>
                        <td>{item.cls_label}</td>
                        <td className="numeric">
                          {item.inward ? money(item.amount, currency) : ""}
                        </td>
                        <td className="numeric">
                          {item.inward ? "" : money(item.amount, currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Planned />
        </>
      )}
      {editing && data ? (
        <WhatIfDialog
          base={whatIf ?? data.assumptions}
          onClose={() => setEditing(false)}
          onRun={(next) => {
            setWhatIf(next);
            setEditing(false);
          }}
        />
      ) : null}
      {planning ? <PlanDialog onClose={() => setPlanning(false)} /> : null}
    </div>
  );
}

function describeDays(days: number): string {
  if (days === 0) return "on the due date";
  return days > 0 ? `${days} days late` : `${-days} days early`;
}

function ThresholdTile({ threshold }: { threshold: string | null }) {
  const currency = useCurrency();
  const { can } = useSession();
  const queries = useQueryClient();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(threshold ? editable(threshold) : "");
  const save = useMutation({
    mutationFn: () => api.planning.setThreshold(text ? text : null),
    onSuccess: () => {
      setOpen(false);
      void queries.invalidateQueries({ queryKey: ["planning"] });
    },
  });
  return (
    <>
      <Measure
        label="Keep in hand"
        value={threshold ? money(threshold, currency) : "Not set"}
        footer={
          can("budget.manage") ? (
            <Button size="compact" tone="plain" onClick={() => setOpen(true)}>
              Change
            </Button>
          ) : undefined
        }
      />
      {open ? (
        <Dialog
          title="Low-cash warning"
          size="sm"
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button tone="plain" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button tone="primary" busy={save.isPending} onClick={() => save.mutate()}>
                Save
              </Button>
            </>
          }
        >
          <Field label="Warn when forecast cash falls below" optional>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            )}
          </Field>
        </Dialog>
      ) : null}
    </>
  );
}

function WhatIfDialog({
  base,
  onClose,
  onRun,
}: {
  base: ForecastAssumptions;
  onClose: () => void;
  onRun: (next: ForecastAssumptions) => void;
}) {
  const loans = useQuery({
    queryKey: ["financing", "loans", "all", "active"],
    queryFn: () => api.financing.loans(undefined, "active"),
  });
  const [delay, setDelay] = useState(String(base.collection_delay_days));
  const [rate, setRate] = useState(editable(String(base.collection_rate)));
  const [payDelay, setPayDelay] = useState(String(base.payment_delay_days));
  const [recurring, setRecurring] = useState(base.include_recurring);
  const [flowKind, setFlowKind] = useState<"none" | "borrow" | "purchase">("none");
  const [flowAmount, setFlowAmount] = useState("");
  const [flowOn, setFlowOn] = useState(today());
  const [settle, setSettle] = useState(base.settle_loan_id ?? "");
  const [settleOn, setSettleOn] = useState(base.settle_on ?? today());
  const run = () =>
    onRun({
      ...base,
      collection_delay_days: Number(delay) || 0,
      collection_rate: rate || "100",
      payment_delay_days: Number(payDelay) || 0,
      include_recurring: recurring,
      extra_flows:
        flowKind === "none" || !Number(flowAmount)
          ? base.extra_flows
          : [
              ...base.extra_flows,
              {
                on: flowOn,
                amount: flowAmount,
                inward: flowKind === "borrow",
                label: flowKind === "borrow" ? "New borrowing" : "Large purchase",
              },
            ],
      settle_loan_id: settle || null,
      settle_on: settle ? settleOn : null,
    });
  return (
    <Dialog
      title="Test a what-if"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button tone="primary" onClick={run}>
            Run the forecast
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <p className="caption">
          Nothing is saved. The forecast is redrawn under these assumptions.
        </p>
        <div className="form-grid">
          <Field label="Customers pay, days late" hint="Negative for early.">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={delay}
                onChange={(e) => setDelay(e.target.value)}
              />
            )}
          </Field>
          <Field label="Share collected, percent">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={rate}
                onChange={(e) => setRate(e.target.value)}
              />
            )}
          </Field>
          <Field label="The business pays, days late">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                value={payDelay}
                onChange={(e) => setPayDelay(e.target.value)}
              />
            )}
          </Field>
        </div>
        <Checkbox label="Include recurring costs" checked={recurring} onChange={setRecurring} />
        <RadioGroup
          legend="Add"
          inline
          value={flowKind}
          onChange={setFlowKind}
          options={[
            { value: "none", label: "Nothing" },
            { value: "borrow", label: "New borrowing" },
            { value: "purchase", label: "A large purchase" },
          ]}
        />
        {flowKind !== "none" ? (
          <div className="form-grid">
            <Field label="Amount" required>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={flowAmount}
                  onChange={(e) => setFlowAmount(e.target.value)}
                />
              )}
            </Field>
            <Field label="On" required>
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={flowOn}
                  onChange={(e) => setFlowOn(e.target.value)}
                />
              )}
            </Field>
          </div>
        ) : null}
        <div className="form-grid">
          <Field label="Settle a loan early" optional>
            {(id) => (
              <Select id={id} value={settle} onChange={(e) => setSettle(e.target.value)}>
                <option value="">None</option>
                {(loans.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.number} · {item.counterparty_name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {settle ? (
            <Field label="On" required>
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={settleOn}
                  onChange={(e) => setSettleOn(e.target.value)}
                />
              )}
            </Field>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}

function Planned() {
  const currency = useCurrency();
  const { can } = useSession();
  const toast = useToast();
  const queries = useQueryClient();
  const items = useQuery({ queryKey: ["planning", "planned"], queryFn: api.planning.planned });
  const remove = useMutation({
    mutationFn: api.planning.unplan,
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["planning"] });
      toast.show("success", "Removed from the forecast.");
    },
  });
  if (!items.data || items.data.length === 0) return null;
  return (
    <Card title="Planned items" subtitle="Expected money that no record yet holds." flush>
      <table className="table">
        <tbody>
          {items.data.map((item) => (
            <tr key={item.id}>
              <td className="nowrap">{formatDate(item.expected_on)}</td>
              <td>{item.label}</td>
              <td className="numeric">{item.inward ? money(item.amount, currency) : ""}</td>
              <td className="numeric">{item.inward ? "" : money(item.amount, currency)}</td>
              <td>
                {can("budget.manage") ? (
                  <Button size="compact" tone="plain" onClick={() => remove.mutate(item.id)}>
                    Remove
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function PlanDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const [inward, setInward] = useState<"in" | "out">("out");
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(today());
  const plan = useMutation({
    mutationFn: () =>
      api.planning.plan({ expected_on: on, amount, inward: inward === "in", label }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["planning"] });
      onClose();
    },
  });
  return (
    <Dialog
      title="Plan an item"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={plan.isPending}
            disabled={!label.trim() || !Number(amount)}
            onClick={() => plan.mutate()}
          >
            Add to the forecast
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {plan.isError ? <Alert tone="error" title={describe(plan.error).message} /> : null}
        <RadioGroup
          legend="Direction"
          inline
          value={inward}
          onChange={setInward}
          options={[
            { value: "out", label: "Money out" },
            { value: "in", label: "Money in" },
          ]}
        />
        <Field label="What it is" required>
          {(id) => (
            <TextInput
              id={id}
              autoFocus
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Second delivery van"
            />
          )}
        </Field>
        <div className="form-grid">
          <Field label="Amount" required>
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            )}
          </Field>
          <Field label="Expected on" required>
            {(id) => (
              <TextInput id={id} type="date" value={on} onChange={(e) => setOn(e.target.value)} />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
