/**
 * Quotations: the offer, and the cost build-up behind it.
 *
 * The build-up is a separate request, behind the costing permission. A sales officer
 * sees the offer; the proprietor sees what it is made of. The same record, read with
 * different rights.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Quotation } from "@/api/types";
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
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, humanise, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { CostBuildUp } from "./CostBuildUp";
import { STATE_TONE } from "./SalesOverview";

export function Quotations({
  canSeeCosting,
  canManage,
  canIssue,
  canApprove,
}: {
  canSeeCosting: boolean;
  canManage: boolean;
  canIssue: boolean;
  canApprove: boolean;
}) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("");
  const [open, setOpen] = useState<Quotation | null>(null);
  const [deciding, setDeciding] = useState<Quotation | null>(null);
  const [revising, setRevising] = useState<Quotation | null>(null);

  const quotations = useQuery({
    queryKey: ["sales", "quotations", state],
    queryFn: () =>
      api.quotations.list({ state: state || undefined, limit: 50 }),
  });

  const act = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "submit" | "approve" | "issue";
    }) =>
      action === "submit"
        ? api.quotations.submit(id)
        : action === "approve"
          ? api.quotations.approve(id)
          : api.quotations.issue(id),
    onSuccess: (updated) => {
      toast.show(
        "success",
        `${updated.number} is now ${humanise(updated.state)}.`,
      );
      void client.invalidateQueries({ queryKey: ["sales"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <div className="stack gap-5">
      <Card
        title={`${quotations.data?.total ?? 0} quotations`}
        subtitle="Newest first. A revision carries the same number with the next index."
        actions={
          <Select
            aria-label="Filter by state"
            value={state}
            onChange={(event) => setState(event.target.value)}
          >
            <option value="">Every state</option>
            <option value="draft">Draft</option>
            <option value="pending_approval">Pending approval</option>
            <option value="issued">Issued</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
            <option value="expired">Expired</option>
            <option value="superseded">Superseded</option>
          </Select>
        }
        flush
      >
        {quotations.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (quotations.data?.items ?? []).length === 0 ? (
          <EmptyState
            title="No quotation has been raised."
            detail="Open a request, compare the supplier offers, and quote from the comparison."
          />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "13%" }}>Number</th>
                  <th>Customer</th>
                  <th style={{ width: "11%" }}>Quoted</th>
                  <th style={{ width: "11%" }}>Valid until</th>
                  <th className="numeric" style={{ width: "15%" }}>
                    Total payable
                  </th>
                  <th style={{ width: "12%" }}>Standing</th>
                  <th style={{ width: "20%" }} />
                </tr>
              </thead>
              <tbody>
                {(quotations.data?.items ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.number}
                      {item.revision > 1 ? (
                        <span className="cell-note">
                          Revision {item.revision}
                        </span>
                      ) : null}
                    </td>
                    <td className="table__cell--wrap">
                      {item.customer_name ?? "—"}
                      {item.payment_plan === "instalment" ? (
                        <span className="cell-note">
                          {item.instalment_months} months ·{" "}
                          {humanise(item.instalment_frequency ?? "monthly")}
                        </span>
                      ) : null}
                    </td>
                    <td>{formatDate(item.quoted_on)}</td>
                    <td>
                      {item.valid_until ? formatDate(item.valid_until) : "—"}
                    </td>
                    <td className="numeric">
                      {money(item.total_payable, currency)}
                    </td>
                    <td>
                      <StatusChip tone={STATE_TONE[item.state] ?? "neutral"}>
                        {humanise(item.state)}
                      </StatusChip>
                      {item.requires_approval && item.approved_at === null ? (
                        <span className="cell-note">Approval required</span>
                      ) : null}
                    </td>
                    <td className="table__actions">
                      <Button
                        tone="plain"
                        size="compact"
                        onClick={() => setOpen(item)}
                      >
                        Open
                      </Button>
                      {canManage &&
                      item.state === "draft" &&
                      item.requires_approval ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() =>
                            act.mutate({ id: item.id, action: "submit" })
                          }
                        >
                          Submit
                        </Button>
                      ) : null}
                      {canApprove && item.state === "pending_approval" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() =>
                            act.mutate({ id: item.id, action: "approve" })
                          }
                        >
                          Approve
                        </Button>
                      ) : null}
                      {canIssue && item.state === "draft" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() =>
                            act.mutate({ id: item.id, action: "issue" })
                          }
                        >
                          Issue
                        </Button>
                      ) : null}
                      {canManage && item.state === "issued" ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => setDeciding(item)}
                        >
                          Answer
                        </Button>
                      ) : null}
                      {canManage &&
                      ["issued", "accepted", "rejected", "expired"].includes(
                        item.state,
                      ) ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => setRevising(item)}
                        >
                          Revise
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {open ? (
        <QuotationDetail
          quotation={open}
          canSeeCosting={canSeeCosting}
          onClose={() => setOpen(null)}
        />
      ) : null}
      {deciding ? (
        <DecideDialog quotation={deciding} onClose={() => setDeciding(null)} />
      ) : null}
      {revising ? (
        <ReviseDialog quotation={revising} onClose={() => setRevising(null)} />
      ) : null}
    </div>
  );
}

function QuotationDetail({
  quotation,
  canSeeCosting,
  onClose,
}: {
  quotation: Quotation;
  canSeeCosting: boolean;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const [tab, setTab] = useState<"offer" | "costing" | "schedule">("offer");

  const costing = useQuery({
    queryKey: ["sales", "costing", quotation.id],
    queryFn: () => api.quotations.costing(quotation.id),
    enabled: canSeeCosting && tab === "costing",
  });
  const schedule = useQuery({
    queryKey: ["sales", "schedule", quotation.id],
    queryFn: () => api.quotations.schedule(quotation.id),
    enabled: tab === "schedule",
  });

  return (
    <Dialog
      size="lg"
      title={`${quotation.number}${quotation.revision > 1 ? ` revision ${quotation.revision}` : ""}`}
      onClose={onClose}
    >
      <div className="stack gap-4">
        <div className="row gap-2">
          <Button
            tone={tab === "offer" ? "primary" : "plain"}
            size="compact"
            onClick={() => setTab("offer")}
          >
            The offer
          </Button>
          {canSeeCosting ? (
            <Button
              tone={tab === "costing" ? "primary" : "plain"}
              size="compact"
              onClick={() => setTab("costing")}
            >
              Cost build-up
            </Button>
          ) : null}
          {quotation.payment_plan === "instalment" ? (
            <Button
              tone={tab === "schedule" ? "primary" : "plain"}
              size="compact"
              onClick={() => setTab("schedule")}
            >
              Repayment schedule
            </Button>
          ) : null}
        </div>

        {tab === "offer" ? (
          <div className="stack gap-4">
            <Details
              rows={[
                ["Customer", quotation.customer_name ?? "—"],
                ["Quoted", formatDate(quotation.quoted_on)],
                [
                  "Valid until",
                  quotation.valid_until
                    ? formatDate(quotation.valid_until)
                    : "No expiry",
                ],
                ["Standing", humanise(quotation.state)],
              ]}
            />
            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th className="numeric" style={{ width: "6%" }}>
                      #
                    </th>
                    <th>Item</th>
                    <th className="numeric" style={{ width: "11%" }}>
                      Quantity
                    </th>
                    <th className="numeric" style={{ width: "14%" }}>
                      Unit price
                    </th>
                    <th className="numeric" style={{ width: "12%" }}>
                      Discount
                    </th>
                    <th className="numeric" style={{ width: "13%" }}>
                      Tax
                    </th>
                    <th className="numeric" style={{ width: "15%" }}>
                      Amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {quotation.lines.map((line) => (
                    <tr key={line.id}>
                      <td className="numeric">{line.position}</td>
                      <td className="table__cell--wrap">
                        {line.description}
                        {line.specification ? (
                          <span className="cell-note">
                            {line.specification}
                          </span>
                        ) : null}
                      </td>
                      <td className="numeric">{quantity(line.quantity)}</td>
                      <td className="numeric">
                        {money(line.unit_net_price, currency)}
                      </td>
                      <td className="numeric">
                        {money(line.discount_amount, currency)}
                      </td>
                      <td className="numeric">
                        {money(line.tax_amount, currency)}
                      </td>
                      <td className="numeric">
                        {money(line.total_payable, currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Details
              rows={[
                ["Net of discount", money(quotation.net_total, currency)],
                ...(quotation.payment_plan === "instalment"
                  ? ([
                      [
                        "Finance charge",
                        money(quotation.finance_charge_total, currency),
                      ],
                    ] as [string, string][])
                  : []),
                ["Tax", money(quotation.tax_total, currency)],
                ["Total payable", money(quotation.total_payable, currency)],
              ]}
            />
            {quotation.terms ? (
              <p className="muted">{quotation.terms}</p>
            ) : null}
          </div>
        ) : null}

        {tab === "costing" ? (
          costing.isPending ? (
            <Skeleton rows={6} />
          ) : costing.data ? (
            <CostBuildUp costing={costing.data} />
          ) : (
            <EmptyState title="The cost build-up is not available." />
          )
        ) : null}

        {tab === "schedule" ? (
          schedule.isPending ? (
            <Skeleton rows={5} />
          ) : schedule.data ? (
            <div className="stack gap-4">
              <Details
                rows={[
                  ["Deposit", money(schedule.data.deposit, currency)],
                  [
                    "Instalment",
                    money(schedule.data.instalment_amount, currency),
                  ],
                  ["Frequency", humanise(schedule.data.frequency ?? "monthly")],
                  ["Duration", `${schedule.data.duration_months ?? 0} months`],
                  [
                    "First due",
                    schedule.data.first_due_on
                      ? formatDate(schedule.data.first_due_on)
                      : "—",
                  ],
                  [
                    "Final due",
                    schedule.data.final_due_on
                      ? formatDate(schedule.data.final_due_on)
                      : "—",
                  ],
                  [
                    "Finance charge",
                    money(schedule.data.finance_charge, currency),
                  ],
                  [
                    "Total payable",
                    money(schedule.data.total_payable, currency),
                  ],
                ]}
              />
              <div className="table-frame">
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th className="numeric" style={{ width: "10%" }}>
                        #
                      </th>
                      <th style={{ width: "30%" }}>Due</th>
                      <th className="numeric" style={{ width: "30%" }}>
                        Amount
                      </th>
                      <th style={{ width: "30%" }}>Standing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {schedule.data.instalments.map((row) => (
                      <tr key={row.id}>
                        <td className="numeric">
                          {row.is_deposit ? "" : row.sequence}
                        </td>
                        <td>{formatDate(row.due_on)}</td>
                        <td className="numeric">
                          {money(row.amount, currency)}
                        </td>
                        <td>
                          <StatusChip
                            tone={row.is_deposit ? "accent" : "neutral"}
                          >
                            {row.is_deposit ? "Deposit" : humanise(row.state)}
                          </StatusChip>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <EmptyState title="No schedule has been generated." />
          )
        ) : null}
      </div>
    </Dialog>
  );
}

function DecideDialog({
  quotation,
  onClose,
}: {
  quotation: Quotation;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("accepted");
  const [reason, setReason] = useState("");

  const decide = useMutation({
    mutationFn: () =>
      api.quotations.decide(quotation.id, { state, reason: reason || null }),
    onSuccess: (updated) => {
      toast.show(
        "success",
        `${updated.number} recorded as ${humanise(updated.state)}.`,
      );
      void client.invalidateQueries({ queryKey: ["sales"] });
      onClose();
    },
  });

  return (
    <Dialog
      title={`Record the answer to ${quotation.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => decide.mutate()} busy={decide.isPending}>
            Record
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {decide.isError ? (
          <Alert tone="error" title={describe(decide.error).message} />
        ) : null}
        <Field label="Outcome" required>
          {(id) => (
            <Select
              id={id}
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="accepted">Accepted</option>
              <option value="rejected">Rejected</option>
              <option value="expired">Expired</option>
            </Select>
          )}
        </Field>
        <Field
          label="Reason"
          hint="Required on a rejection. It is the most useful thing a lost quotation leaves behind."
        >
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function ReviseDialog({
  quotation,
  onClose,
}: {
  quotation: Quotation;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");

  const revise = useMutation({
    mutationFn: () => api.quotations.revise(quotation.id, reason),
    onSuccess: (created) => {
      toast.show(
        "success",
        `${created.number} revision ${created.revision} opened as a draft.`,
      );
      void client.invalidateQueries({ queryKey: ["sales"] });
      onClose();
    },
  });

  return (
    <Dialog
      title={`Revise ${quotation.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => revise.mutate()}
            busy={revise.isPending}
            disabled={!reason.trim()}
          >
            Open revision
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {revise.isError ? (
          <Alert tone="error" title={describe(revise.error).message} />
        ) : null}
        <p className="muted">
          The current quotation is superseded and stays on the record with its
          figures and its costing snapshot. The revision opens as a draft
          carrying the same number.
        </p>
        <Field label="Why the revision is needed" required>
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
