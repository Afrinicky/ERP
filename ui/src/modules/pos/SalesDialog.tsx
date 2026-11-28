/**
 * Sales and refunds: this shift's completed sales, a sale found by its receipt number,
 * and a return against it.
 *
 * A refund names the sale and the lines it reverses, and is authorised by someone who
 * holds refunds — the cashier, if they do, or a supervisor at the register, whose
 * password is checked and who is recorded beside the cashier.
 */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PosRefund, PosSale, SaleStateName, TenderMethodName } from "@/api/types";
import {
  Alert,
  Button,
  Checkbox,
  Details,
  Dialog,
  EmptyState,
  Field,
  RadioGroup,
  Skeleton,
  StatusChip,
  Stepper,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { isZero } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { dateTime, money, quantity } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

const SALE_STATE: Record<SaleStateName, { label: string; tone: Tone }> = {
  open: { label: "Open", tone: "neutral" },
  parked: { label: "Parked", tone: "info" },
  completed: { label: "Completed", tone: "ok" },
  voided: { label: "Voided", tone: "neutral" },
  refunded: { label: "Refunded", tone: "warning" },
  partially_refunded: { label: "Part refunded", tone: "warning" },
};

export function SalesDialog({
  shiftId,
  onClose,
  onChanged,
}: {
  shiftId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const currency = useCurrency();
  const [number, setNumber] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const sales = useQuery({
    queryKey: ["pos", "sales", shiftId],
    queryFn: () => api.pos.sales(shiftId),
  });
  const find = useMutation({
    mutationFn: () => api.pos.findSale(number.trim()),
    onSuccess: (sale) => setOpenId(sale.id),
  });

  if (openId) {
    return (
      <SaleRecord
        saleId={openId}
        onBack={() => setOpenId(null)}
        onClose={onClose}
        onChanged={() => {
          onChanged();
          void sales.refetch();
        }}
      />
    );
  }

  return (
    <Dialog title="Sales and refunds" size="lg" onClose={onClose}>
      <div className="stack gap-5">
        <div className="register__scan-row">
          <Field label="Receipt number" hint="For a sale from another shift.">
            {(id) => (
              <TextInput
                id={id}
                value={number}
                onChange={(event) => setNumber(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && number.trim()) find.mutate();
                }}
              />
            )}
          </Field>
          <Button busy={find.isPending} disabled={!number.trim()} onClick={() => find.mutate()}>
            Find sale
          </Button>
        </div>
        {find.isError ? <Alert tone="error" title={describe(find.error).message} /> : null}
        {sales.isPending ? (
          <Skeleton rows={4} />
        ) : (sales.data ?? []).length === 0 ? (
          <EmptyState title="No sale has been completed in this shift." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Receipt</th>
                <th scope="col">Paid by</th>
                <th scope="col">Status</th>
                <th scope="col" className="numeric">
                  Total
                </th>
              </tr>
            </thead>
            <tbody>
              {(sales.data ?? []).map((sale) => (
                <tr key={sale.id}>
                  <td>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setOpenId(sale.id)}
                    >
                      {sale.number}
                    </button>
                    <div className="cell-note">
                      {dateTime(sale.completed_at)} · {sale.customer ?? "Walk-in customer"}
                    </div>
                  </td>
                  <td>{sale.methods.join(", ")}</td>
                  <td>
                    <StatusChip tone={SALE_STATE[sale.state].tone}>
                      {SALE_STATE[sale.state].label}
                    </StatusChip>
                  </td>
                  <td className="numeric">{money(sale.total, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Dialog>
  );
}

function SaleRecord({
  saleId,
  onBack,
  onClose,
  onChanged,
}: {
  saleId: string;
  onBack: () => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const currency = useCurrency();
  const [returning, setReturning] = useState(false);
  const sale = useQuery({
    queryKey: ["pos", "sale", saleId],
    queryFn: () => api.pos.saleById(saleId),
  });
  const refunds = useQuery({
    queryKey: ["pos", "refunds", saleId],
    queryFn: () => api.pos.refunds(saleId),
  });

  if (returning && sale.data) {
    return (
      <RefundDialog
        sale={sale.data}
        onClose={() => setReturning(false)}
        onDone={() => {
          setReturning(false);
          void sale.refetch();
          void refunds.refetch();
          onChanged();
        }}
      />
    );
  }

  const data = sale.data;
  const returnable = data ? data.lines.some((line) => !isZero(line.returnable)) : false;
  return (
    <Dialog
      title={data?.number ? `Sale ${data.number}` : "Sale"}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onBack}>Back to sales</Button>
          <Button
            tone="primary"
            size="transactional"
            disabled={!returnable}
            onClick={() => setReturning(true)}
          >
            Return items
          </Button>
        </>
      }
    >
      {!data ? (
        <Skeleton rows={5} />
      ) : (
        <div className="stack gap-5">
          <Details
            rows={[
              ["Status", SALE_STATE[data.state].label],
              ["Completed", dateTime(data.completed_at)],
              ["Served by", data.cashier],
              ["Customer", data.customer ?? "Walk-in customer"],
              ["Total", money(data.total, currency)],
              [
                "Refunded",
                isZero(data.refunded_total) ? null : money(data.refunded_total, currency),
              ],
            ]}
          />
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col" className="numeric">
                  Sold
                </th>
                <th scope="col" className="numeric">
                  Returnable
                </th>
                <th scope="col" className="numeric">
                  Amount
                </th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.id}>
                  <td>
                    <div>{line.description}</div>
                    <div className="cell-note">{line.sku}</div>
                  </td>
                  <td className="numeric">{quantity(line.quantity)}</td>
                  <td className="numeric">{quantity(line.returnable)}</td>
                  <td className="numeric">{money(line.total, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(refunds.data ?? []).length > 0 ? <RefundList refunds={refunds.data ?? []} /> : null}
        </div>
      )}
    </Dialog>
  );
}

function RefundList({ refunds }: { refunds: PosRefund[] }) {
  const currency = useCurrency();
  return (
    <div className="stack gap-2">
      <h3 className="section-label">Returns against this sale</h3>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Credit note</th>
            <th scope="col">Authorised by</th>
            <th scope="col" className="numeric">
              Refunded
            </th>
          </tr>
        </thead>
        <tbody>
          {refunds.map((refund) => (
            <tr key={refund.id}>
              <td>
                <div>{refund.number}</div>
                <div className="cell-note">{refund.reason}</div>
              </td>
              <td>
                {refund.authorised_by}
                {refund.requested_by && refund.requested_by !== refund.authorised_by ? (
                  <div className="cell-note">at the request of {refund.requested_by}</div>
                ) : null}
              </td>
              <td className="numeric">{money(refund.total, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RefundDialog({
  sale,
  onClose,
  onDone,
}: {
  sale: PosSale;
  onClose: () => void;
  onDone: () => void;
}) {
  const { can } = useSession();
  const toast = useToast();
  const currency = useCurrency();
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState<TenderMethodName>(
    sale.tenders.find((item) => item.state === "confirmed")?.method ?? "cash",
  );
  const [restock, setRestock] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const needsSupervisor = !can("pos.refund");

  const lines = Object.entries(chosen)
    .filter(([, value]) => value.trim() !== "" && !isZero(value))
    .map(([sale_line_id, value]) => ({ sale_line_id, quantity: value }));

  const refund = useMutation({
    mutationFn: () =>
      api.pos.refund(sale.id, {
        lines,
        reason,
        method,
        restock,
        supervisor: needsSupervisor ? { username, password } : null,
      }),
    onSuccess: (made) => {
      toast.show("success", `${made.number}: ${money(made.total, currency)} refunded.`);
      onDone();
    },
  });

  const ready =
    lines.length > 0 && reason.trim() !== "" && (!needsSupervisor || (username && password));

  return (
    <Dialog
      title={`Return against ${sale.number ?? "sale"}`}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            size="transactional"
            busy={refund.isPending}
            disabled={!ready}
            onClick={() => refund.mutate()}
          >
            Refund
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {refund.isError ? <Alert tone="error" title={describe(refund.error).message} /> : null}
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="numeric">
                Returnable
              </th>
              <th scope="col">Returning</th>
            </tr>
          </thead>
          <tbody>
            {sale.lines
              .filter((line) => !isZero(line.returnable))
              .map((line) => (
                <tr key={line.id}>
                  <td>
                    <div>{line.description}</div>
                    <div className="cell-note">
                      {line.sku} · {money(line.unit_price, currency)} each
                    </div>
                  </td>
                  <td className="numeric">{quantity(line.returnable)}</td>
                  <td>
                    <Stepper
                      value={chosen[line.id] ?? "0"}
                      onChange={(value) => setChosen((all) => ({ ...all, [line.id]: value }))}
                      min={0}
                      max={Number.parseFloat(line.returnable)}
                      size="transactional"
                      label={`Returning ${line.description}`}
                    />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="form-grid">
          <Field label="Reason" required full>
            {(id) => (
              <TextInput
                id={id}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Seal broken on delivery to the ward"
              />
            )}
          </Field>
          <RadioGroup
            legend="Refund by"
            inline
            value={method}
            onChange={setMethod}
            options={[
              { value: "cash", label: "Cash" },
              { value: "mobile_money", label: "Mobile Money" },
              { value: "card", label: "Card" },
              { value: "bank_transfer", label: "Transfer" },
              { value: "on_account", label: "To account", disabled: !sale.customer_id },
            ]}
          />
          <Checkbox
            label="Fit to resell"
            hint="Unticked, the goods go to quarantine for inspection."
            checked={restock}
            onChange={setRestock}
          />
        </div>
        {needsSupervisor ? (
          <div className="stack gap-3">
            <h3 className="section-label">Supervisor authorisation</h3>
            <p className="cell-note">
              A refund is authorised by someone who holds refunds. They enter their own credentials
              here; both names are recorded.
            </p>
            <div className="form-grid">
              <Field label="Supervisor username" required>
                {(id) => (
                  <TextInput
                    id={id}
                    autoComplete="off"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                )}
              </Field>
              <Field label="Password" required>
                {(id) => (
                  <TextInput
                    id={id}
                    type="password"
                    autoComplete="off"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                )}
              </Field>
            </div>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
