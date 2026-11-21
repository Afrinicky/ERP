/**
 * Month-end close: what the system measures, what a person confirms, then the lock.
 * Reopening a closed month is asked for, with a reason, and decided by someone else.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { AccountingPeriod, CloseChecklist, ReopenRequest } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  StatusChip,
  TextArea,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

const CHECK_LABELS: Record<string, string> = {
  earlier_periods_closed: "Earlier periods closed",
  no_outstanding_entries: "No draft or pending entries",
  entries_balanced: "Every entry balances",
  trial_balance_balanced: "Trial balance balances",
  subledgers_reconciled: "Control accounts agree with their registers",
  shifts_closed: "Till shifts counted and closed",
  expenses_decided: "Expenses decided",
  statements_reconciled: "Bank statements reconciled",
};

function stateChip(period: AccountingPeriod) {
  if (period.state === "closed") return <StatusChip tone="neutral">Closed</StatusChip>;
  if (period.state === "reopened") return <StatusChip tone="warning">Reopened</StatusChip>;
  return <StatusChip tone="ok">Open</StatusChip>;
}

export function PeriodClose() {
  const periods = useQuery({
    queryKey: ["ledger", "periods"],
    queryFn: () => api.ledger.periods(),
  });
  const ordered = useMemo(
    () => [...(periods.data ?? [])].sort((a, b) => a.starts_on.localeCompare(b.starts_on)),
    [periods.data],
  );
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    if (chosen || ordered.length === 0) return;
    const firstOpen = ordered.find((item) => item.state !== "closed");
    setChosen((firstOpen ?? ordered[ordered.length - 1])!.id);
  }, [ordered, chosen]);

  if (periods.isError) return <Alert tone="error" title={describe(periods.error).message} />;
  if (periods.isPending) return <Skeleton rows={8} />;
  if (ordered.length === 0) return <EmptyState title="No financial year has been opened." />;

  return (
    <div className="reports">
      <nav className="reports__list" aria-label="Accounting periods">
        {Object.entries(
          ordered.reduce<Record<string, AccountingPeriod[]>>((years, item) => {
            const key = String(item.financial_year);
            years[key] = [...(years[key] ?? []), item];
            return years;
          }, {}),
        ).map(([year, items]) => (
          <div key={year} className="reports__group">
            <span className="reports__group-label">Financial year {year}</span>
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`reports__item${item.id === chosen ? " reports__item--active" : ""}`}
                aria-current={item.id === chosen ? "true" : undefined}
                onClick={() => setChosen(item.id)}
              >
                <span>{item.label}</span>
                {stateChip(item)}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="reports__body">{chosen ? <Checklist periodId={chosen} /> : null}</div>
    </div>
  );
}

function Checklist({ periodId }: { periodId: string }) {
  const { can } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const checklist = useQuery({
    queryKey: ["ledger", "checklist", periodId],
    queryFn: () => api.reports.checklist(periodId),
  });
  const [asking, setAsking] = useState(false);
  const [deciding, setDeciding] = useState<ReopenRequest | null>(null);
  const refresh = (data?: CloseChecklist) => {
    if (data) queryClient.setQueryData(["ledger", "checklist", periodId], data);
    void queryClient.invalidateQueries({ queryKey: ["ledger"] });
  };
  const step = useMutation({
    mutationFn: ({ key, done }: { key: string; done: boolean }) =>
      api.reports.confirmStep(periodId, key, done),
    onSuccess: refresh,
    onError: (error) => toast.show("error", describe(error).message),
  });
  const close = useMutation({
    mutationFn: () => api.ledger.closePeriod(periodId),
    onSuccess: (readiness) => {
      refresh();
      toast.show("success", `${readiness.period.label} is closed. Posting into it is now refused.`);
    },
    onError: (error) => {
      refresh();
      toast.show("error", describe(error).message);
    },
  });

  if (checklist.isError) return <Alert tone="error" title={describe(checklist.error).message} />;
  if (checklist.isPending) return <Skeleton rows={10} />;
  const data = checklist.data;
  const period = data.period;
  const closed = period.state === "closed";
  const pending = data.requests.find((item) => item.state === "pending");
  const measured = data.checks.filter((item) => item.passed).length;

  return (
    <div className="stack gap-5">
      <div className="reports__intro">
        <div>
          <h2 className="reports__heading">{period.label}</h2>
          <p className="caption">
            {formatDate(period.starts_on)} to {formatDate(period.ends_on)}
            {closed && data.closed_by
              ? ` · closed by ${data.closed_by} on ${dateTime(period.closed_at)}`
              : ""}
          </p>
        </div>
        {stateChip(period)}
      </div>

      {period.state === "reopened" ? (
        <Alert tone="warning" title="This period was reopened.">
          {period.reopen_reason}
          {data.reopened_by ? ` Requested by ${data.reopened_by}` : ""}
          {data.reopen_approved_by ? `, approved by ${data.reopen_approved_by}.` : "."}
        </Alert>
      ) : null}

      <Card
        title="Measured by the system"
        subtitle={`${measured} of ${data.checks.length} in order.`}
        flush
      >
        <ul className="close__list">
          {data.checks.map((check) => (
            <li key={check.name} className="close__item">
              <StatusChip tone={check.passed ? "ok" : "error"}>
                {check.passed ? "In order" : "Outstanding"}
              </StatusChip>
              <div>
                <strong>{CHECK_LABELS[check.name] ?? check.name}</strong>
                <div className="cell-note">{check.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <Card
        title="Confirmed by a person"
        subtitle="Each step records who confirmed it and when."
        flush
      >
        <ul className="close__list">
          {data.steps.map((item) => (
            <li key={item.key} className="close__item">
              <StatusChip tone={item.done ? "ok" : "neutral"}>
                {item.done ? "Confirmed" : "To confirm"}
              </StatusChip>
              <div>
                <strong>{item.label}</strong>
                {item.done ? (
                  <div className="cell-note">
                    {item.done_by ?? "Confirmed"} · {dateTime(item.done_at)}
                  </div>
                ) : null}
              </div>
              {!closed && can("period.close") ? (
                <Button
                  size="compact"
                  tone={item.done ? "plain" : "secondary"}
                  busy={step.isPending && step.variables?.key === item.key}
                  onClick={() => step.mutate({ key: item.key, done: !item.done })}
                >
                  {item.done ? "Withdraw" : "Confirm"}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </Card>

      <div className="reports__actions reports__actions--split">
        <span className="caption">
          {closed
            ? "Closed periods refuse every posting. A correction needs the period reopened."
            : data.ready
              ? "Everything is in order. Closing locks the period against posting."
              : "The period closes once everything above is in order."}
        </span>
        <div className="state-row">
          {!closed && can("period.close") ? (
            <Button
              tone="primary"
              size="transactional"
              disabled={!data.ready}
              busy={close.isPending}
              onClick={() => close.mutate()}
            >
              Close {period.label}
            </Button>
          ) : null}
          {closed && !pending && (can("period.close") || can("period.reopen")) ? (
            <Button onClick={() => setAsking(true)}>Request reopening</Button>
          ) : null}
        </div>
      </div>

      {data.requests.length > 0 ? (
        <Card title="Reopening requests" flush>
          <ul className="close__list">
            {data.requests.map((item) => (
              <li key={item.id} className="close__item">
                <StatusChip
                  tone={
                    item.state === "pending"
                      ? "warning"
                      : item.state === "approved"
                        ? "ok"
                        : "neutral"
                  }
                >
                  {item.state === "pending"
                    ? "Awaiting decision"
                    : item.state === "approved"
                      ? "Approved"
                      : "Declined"}
                </StatusChip>
                <div>
                  <strong>{item.reason}</strong>
                  <div className="cell-note">
                    Asked by {item.requested_by ?? "—"} on {dateTime(item.requested_at)}
                    {item.decided_by
                      ? ` · decided by ${item.decided_by} on ${dateTime(item.decided_at)}`
                      : ""}
                    {item.self_approved ? " · approved by the requester, as administrator" : ""}
                    {item.decision_note ? ` · ${item.decision_note}` : ""}
                  </div>
                </div>
                {item.state === "pending" && can("period.reopen") ? (
                  <Button size="compact" onClick={() => setDeciding(item)}>
                    Decide
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {asking ? (
        <ReasonDialog
          title={`Request reopening of ${period.label}`}
          label="Why must it be reopened?"
          hint="Read by whoever decides, and kept with the period."
          confirm="Send request"
          onClose={() => setAsking(false)}
          onSubmit={async (reason) => {
            await api.reports.requestReopen(periodId, reason);
            refresh();
            setAsking(false);
            toast.show("success", "Request sent. A second person decides it.");
          }}
        />
      ) : null}
      {deciding ? (
        <DecisionDialog
          request={deciding}
          onClose={() => setDeciding(null)}
          onDecided={(approved) => {
            refresh();
            setDeciding(null);
            toast.show("success", approved ? `${period.label} is reopened.` : "Request declined.");
          }}
        />
      ) : null}
    </div>
  );
}

function ReasonDialog({
  title,
  label,
  hint,
  confirm,
  onClose,
  onSubmit,
}: {
  title: string;
  label: string;
  hint: string;
  confirm: string;
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      title={title}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={busy}
            disabled={reason.trim().length < 10}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(reason.trim());
              } catch (cause) {
                setError(describe(cause).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirm}
          </Button>
        </>
      }
    >
      <div className="stack gap-3">
        {error ? <Alert tone="error" title={error} /> : null}
        <Field label={label} hint={hint}>
          {(id) => (
            <TextArea
              id={id}
              rows={4}
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function DecisionDialog({
  request,
  onClose,
  onDecided,
}: {
  request: ReopenRequest;
  onClose: () => void;
  onDecided: (approved: boolean) => void;
}) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      api.reports.decideReopen(request.id, approve, note.trim() || null),
    onSuccess: (item) => onDecided(item.state === "approved"),
    onError: (cause) => setError(describe(cause).message),
  });
  return (
    <Dialog
      title={`Reopen ${request.period_label}?`}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            busy={decide.isPending && decide.variables === false}
            onClick={() => decide.mutate(false)}
          >
            Decline
          </Button>
          <Button
            tone="primary"
            busy={decide.isPending && decide.variables === true}
            onClick={() => decide.mutate(true)}
          >
            Approve and reopen
          </Button>
        </>
      }
    >
      <div className="stack gap-3">
        {error ? <Alert tone="error" title={error} /> : null}
        <p>
          <strong>{request.requested_by ?? "Someone"}</strong> asks: {request.reason}
        </p>
        <Field label="Note" optional>
          {(id) => (
            <TextArea
              id={id}
              rows={2}
              value={note}
              maxLength={1000}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
