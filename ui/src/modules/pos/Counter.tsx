/**
 * The counter: the sale on the left, payment on the right.
 *
 * Every figure is the service's. The screen sends what the cashier did — a scan, a
 * quantity, a tender — and shows the sale the service returns. Cash that settles the
 * balance completes the sale at once, so a cash sale is scan, amount, done.
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ServiceError } from "@/api/client";
import type { PosSale, PosSaleLine, PosTender, TenderMethodName } from "@/api/types";
import {
  Alert,
  AmountShortcuts,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Keypad,
  RadioGroup,
  Skeleton,
  StatusChip,
  Stepper,
  TextInput,
  TotalDisplay,
  useToast,
  type Tone,
} from "@/components/primitives";
import { isZero } from "@/lib/decimal";
import { describe } from "@/lib/errors";
import { editable, money, quantity } from "@/lib/format";
import { useCurrency, useSession } from "@/lib/session";

const METHODS: { value: TenderMethodName; label: string }[] = [
  { value: "cash", label: "Cash" },
  { value: "mobile_money", label: "Mobile Money" },
  { value: "card", label: "Card" },
  { value: "bank_transfer", label: "Transfer" },
  { value: "on_account", label: "On account" },
];

const TENDER_TONE: Record<PosTender["state"], Tone> = {
  pending: "warning",
  confirmed: "ok",
  failed: "error",
  abandoned: "neutral",
};

export function Counter({ onChanged }: { onChanged: () => void }) {
  const queries = useQueryClient();
  const toast = useToast();
  const [completed, setCompleted] = useState<PosSale | null>(null);

  const sale = useQuery({ queryKey: ["pos", "sale"], queryFn: () => api.pos.sale() });
  const show = (next: PosSale) => queries.setQueryData(["pos", "sale"], next);

  const complete = useMutation({
    mutationFn: (saleId: string) => api.pos.complete(saleId),
    onSuccess: (done) => {
      setCompleted(done);
      onChanged();
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  const settle = (next: PosSale) => {
    show(next);
    if (isZero(next.due) && !next.awaiting_confirmation && next.lines.length > 0) {
      complete.mutate(next.id);
    }
  };

  const nextSale = async () => {
    setCompleted(null);
    await queries.invalidateQueries({ queryKey: ["pos", "sale"] });
  };

  if (sale.isPending) {
    return (
      <div className="register">
        <Card title="Sale">
          <Skeleton rows={5} height={28} />
        </Card>
        <Card title="Payment">
          <Skeleton rows={4} height={36} />
        </Card>
      </div>
    );
  }
  if (sale.isError) {
    return <Alert tone="error" title={describe(sale.error).message} />;
  }

  const current = completed ?? sale.data;
  return (
    <div className="register">
      <SalePanel sale={current} locked={completed !== null} onUpdated={show} />
      {completed ? (
        <CompletedPanel sale={completed} onNext={() => void nextSale()} />
      ) : (
        <PaymentPanel
          sale={current}
          completing={complete.isPending}
          onUpdated={settle}
          onComplete={() => complete.mutate(current.id)}
        />
      )}
    </div>
  );
}

// -- the sale ---------------------------------------------------------------------------

function SalePanel({
  sale,
  locked,
  onUpdated,
}: {
  sale: PosSale;
  locked: boolean;
  onUpdated: (sale: PosSale) => void;
}) {
  const toast = useToast();
  const currency = useCurrency();
  const [code, setCode] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<PosSaleLine | null>(null);
  const [choosingCustomer, setChoosingCustomer] = useState(false);
  const scanField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "F2") {
        event.preventDefault();
        scanField.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!locked) scanField.current?.focus();
  }, [locked, sale.id]);

  const scan = useMutation({
    mutationFn: (value: string) => api.pos.scan(sale.id, value),
    onSuccess: (next) => {
      onUpdated(next);
      setCode("");
      setSearch("");
    },
    onError: (error, value) => {
      if (error instanceof ServiceError && error.status === 404 && /[a-z]/i.test(value)) {
        setSearch(value);
        return;
      }
      toast.show("error", describe(error).message);
    },
  });
  const add = useMutation({
    mutationFn: (productId: string) => api.pos.addLine(sale.id, productId),
    onSuccess: (next) => {
      onUpdated(next);
      setCode("");
      setSearch("");
      scanField.current?.focus();
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const matches = useQuery({
    queryKey: ["pos", "products", search],
    queryFn: () => api.catalogue.search({ term: search, sellable_only: true, limit: 6 }),
    enabled: search.length >= 2,
  });

  return (
    <Card
      title={sale.number ? `Sale ${sale.number}` : "Sale"}
      subtitle={sale.customer ?? "Walk-in customer"}
      actions={
        locked ? undefined : (
          <Button size="touch" onClick={() => setChoosingCustomer(true)}>
            {sale.customer ? "Change customer" : "Customer"}
          </Button>
        )
      }
      flush
    >
      {locked ? null : (
        <div className="register__scan">
          <label className="register__scan-label" htmlFor="register-scan">
            Scan or enter an item
          </label>
          <div className="register__scan-row">
            <input
              id="register-scan"
              ref={scanField}
              className="input register__scan-input"
              value={code}
              autoComplete="off"
              spellCheck={false}
              placeholder="Barcode, code or name"
              onChange={(event) => {
                setCode(event.target.value);
                if (!event.target.value) setSearch("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && code.trim()) {
                  event.preventDefault();
                  scan.mutate(code.trim());
                } else if (event.key === "Escape") {
                  setCode("");
                  setSearch("");
                }
              }}
            />
            <Button
              tone="primary"
              size="transactional"
              busy={scan.isPending}
              disabled={!code.trim()}
              onClick={() => scan.mutate(code.trim())}
            >
              Add
            </Button>
          </div>
          {search ? (
            <div className="register__matches" role="listbox" aria-label="Matching items">
              {matches.isPending ? (
                <Skeleton rows={2} />
              ) : (matches.data?.items.length ?? 0) === 0 ? (
                <p className="cell-note">No item matches “{search}”.</p>
              ) : (
                matches.data?.items.map((product) => (
                  <button
                    key={product.id}
                    type="button"
                    role="option"
                    aria-selected={false}
                    className="register__match"
                    onClick={() => add.mutate(product.id)}
                  >
                    <span>{product.name}</span>
                    <span className="cell-note">{product.sku}</span>
                  </button>
                ))
              )}
            </div>
          ) : null}
        </div>
      )}

      {sale.lines.length === 0 ? (
        <EmptyState
          title="The cart is empty."
          detail="Scan a barcode, or type an item's code or name and press Enter."
        />
      ) : (
        <table className="table register__lines">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="numeric">
                Price
              </th>
              <th scope="col" className="register__quantity-heading">
                Quantity
              </th>
              <th scope="col" className="numeric">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {sale.lines.map((line) => (
              <LineRow
                key={line.id}
                saleId={sale.id}
                line={line}
                locked={locked}
                onUpdated={onUpdated}
                onEdit={() => setEditing(line)}
              />
            ))}
          </tbody>
        </table>
      )}

      {sale.lines.length > 0 ? (
        <dl className="register__summary">
          <div>
            <dt>Subtotal</dt>
            <dd>{money(sale.gross_total, currency)}</dd>
          </div>
          {isZero(sale.discount_total) ? null : (
            <div>
              <dt>Discount</dt>
              <dd>({money(sale.discount_total, currency)})</dd>
            </div>
          )}
          <div>
            <dt>Tax and levies</dt>
            <dd>{money(sale.tax_total, currency)}</dd>
          </div>
        </dl>
      ) : null}

      {editing ? (
        <LineDialog
          saleId={sale.id}
          line={editing}
          onClose={() => setEditing(null)}
          onUpdated={(next) => {
            onUpdated(next);
            setEditing(null);
          }}
        />
      ) : null}
      {choosingCustomer ? (
        <CustomerDialog
          sale={sale}
          onClose={() => setChoosingCustomer(false)}
          onUpdated={(next) => {
            onUpdated(next);
            setChoosingCustomer(false);
          }}
        />
      ) : null}
    </Card>
  );
}

function LineRow({
  saleId,
  line,
  locked,
  onUpdated,
  onEdit,
}: {
  saleId: string;
  line: PosSaleLine;
  locked: boolean;
  onUpdated: (sale: PosSale) => void;
  onEdit: () => void;
}) {
  const toast = useToast();
  const currency = useCurrency();
  const { can } = useSession();
  const [draft, setDraft] = useState(quantity(line.quantity));
  const timer = useRef<number | null>(null);

  useEffect(() => setDraft(quantity(line.quantity)), [line.quantity]);

  const save = useMutation({
    mutationFn: (value: string) => api.pos.setQuantity(saleId, line.id, value),
    onSuccess: onUpdated,
    onError: (error) => {
      setDraft(quantity(line.quantity));
      toast.show("error", describe(error).message);
    },
  });

  /** Held presses arrive in a burst; the service hears the figure the cashier settles on. */
  const change = (value: string) => {
    setDraft(value);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (value.trim() !== "") save.mutate(value.trim());
    }, 350);
  };
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const adjusted = line.override_reason !== null || !isZero(line.discount_rate);
  return (
    <tr>
      <td>
        <div className="register__item">{line.description}</div>
        <div className="cell-note">
          {line.sku}
          {line.override_reason ? ` · Price overridden: ${line.override_reason}` : ""}
          {isZero(line.discount_rate) ? "" : ` · Discount ${money(line.discount, currency)}`}
        </div>
      </td>
      <td className="numeric">
        {money(line.unit_price, currency)}
        {!locked && (can("pos.discount") || can("pos.price_override")) ? (
          <div>
            <button type="button" className="text-button" onClick={onEdit}>
              {adjusted ? "Adjusted" : "Adjust"}
            </button>
          </div>
        ) : null}
      </td>
      <td className="register__quantity">
        {locked ? (
          quantity(line.quantity)
        ) : (
          <div className="register__quantity-control">
            <Stepper
              value={draft}
              onChange={change}
              min={0}
              size="transactional"
              label={`Quantity of ${line.description}`}
            />
            <Button
              tone="plain"
              size="touch"
              aria-label={`Remove ${line.description}`}
              onClick={() => save.mutate("0")}
            >
              Remove
            </Button>
          </div>
        )}
      </td>
      <td className="numeric register__amount">{money(line.net, currency)}</td>
    </tr>
  );
}

function LineDialog({
  saleId,
  line,
  onClose,
  onUpdated,
}: {
  saleId: string;
  line: PosSaleLine;
  onClose: () => void;
  onUpdated: (sale: PosSale) => void;
}) {
  const { can } = useSession();
  const currency = useCurrency();
  const [percent, setPercent] = useState("");
  const [price, setPrice] = useState(line.unit_price.replace(/0+$/, "").replace(/\.$/, ""));
  const [reason, setReason] = useState(line.override_reason ?? "");

  const discount = useMutation({
    mutationFn: () => api.pos.discount(saleId, line.id, toRate(percent)),
    onSuccess: onUpdated,
  });
  const override = useMutation({
    mutationFn: () => api.pos.overridePrice(saleId, line.id, price, reason),
    onSuccess: onUpdated,
  });
  const failure = discount.error ?? override.error;

  return (
    <Dialog title={`Adjust ${line.description}`} size="sm" onClose={onClose}>
      <div className="stack gap-5">
        {failure ? <Alert tone="error" title={describe(failure).message} /> : null}
        <p className="cell-note">
          List price {money(line.list_price, currency)}
          {line.price_source ? ` · ${line.price_source}` : ""}
        </p>
        {can("pos.discount") ? (
          <div className="stack gap-3">
            <Field label="Discount" hint="Per cent of this line.">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={percent}
                  onChange={(event) => setPercent(event.target.value)}
                  placeholder="5"
                />
              )}
            </Field>
            <Button
              size="touch"
              busy={discount.isPending}
              disabled={percent.trim() === ""}
              onClick={() => discount.mutate()}
            >
              Apply discount
            </Button>
          </div>
        ) : null}
        {can("pos.price_override") ? (
          <div className="stack gap-3">
            <Field label="Unit price" required>
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={price}
                  onChange={(event) => setPrice(event.target.value)}
                />
              )}
            </Field>
            <Field label="Reason" required hint="Recorded against the sale.">
              {(id) => (
                <TextInput
                  id={id}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Price matched to the agreed contract rate"
                />
              )}
            </Field>
            <Button
              size="touch"
              busy={override.isPending}
              disabled={price.trim() === "" || reason.trim() === ""}
              onClick={() => override.mutate()}
            >
              Override price
            </Button>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

/** "5" per cent to "0.05", textually: the rate is the service's to apply. */
function toRate(percent: string): string {
  const text = percent.trim() || "0";
  const [whole = "0", fraction = ""] = text.split(".");
  const digits = `${whole.padStart(3, "0")}${fraction}`;
  const point = digits.length - fraction.length - 2;
  return `${digits.slice(0, point)}.${digits.slice(point)}`.replace(/^0+(?=\d)/, "");
}

function CustomerDialog({
  sale,
  onClose,
  onUpdated,
}: {
  sale: PosSale;
  onClose: () => void;
  onUpdated: (sale: PosSale) => void;
}) {
  const [term, setTerm] = useState("");
  const found = useQuery({
    queryKey: ["pos", "customers", term],
    queryFn: () => api.parties.search({ term, kind: "customer", limit: 8 }),
    enabled: term.trim().length >= 2,
  });
  const choose = useMutation({
    mutationFn: (customerId: string | null) => api.pos.setCustomer(sale.id, customerId),
    onSuccess: onUpdated,
  });

  return (
    <Dialog title="Customer" size="sm" onClose={onClose}>
      <div className="stack gap-4">
        {choose.isError ? <Alert tone="error" title={describe(choose.error).message} /> : null}
        <Field label="Find a registered customer" hint="Their agreed prices apply to the sale.">
          {(id) => (
            <TextInput
              id={id}
              autoFocus
              value={term}
              onChange={(event) => setTerm(event.target.value)}
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
              onClick={() => choose.mutate(party.id)}
            >
              <span>{party.name}</span>
              <span className="cell-note">{party.code}</span>
            </button>
          ))}
          {term.trim().length >= 2 && found.data && found.data.items.length === 0 ? (
            <p className="cell-note">No customer matches “{term}”.</p>
          ) : null}
        </div>
        {sale.customer_id ? (
          <Button size="touch" onClick={() => choose.mutate(null)}>
            Sell to a walk-in customer
          </Button>
        ) : null}
      </div>
    </Dialog>
  );
}

// -- payment ----------------------------------------------------------------------------

function PaymentPanel({
  sale,
  completing,
  onUpdated,
  onComplete,
}: {
  sale: PosSale;
  completing: boolean;
  onUpdated: (sale: PosSale) => void;
  onComplete: () => void;
}) {
  const toast = useToast();
  const currency = useCurrency();
  const [method, setMethod] = useState<TenderMethodName>("cash");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [parking, setParking] = useState(false);

  useEffect(() => {
    setAmount("");
    setReference("");
  }, [sale.id, method]);

  const tender = useMutation({
    mutationFn: () =>
      api.pos.tender(sale.id, {
        method,
        cash_received: method === "cash" ? amount || null : null,
        amount: method === "cash" ? null : amount || null,
        reference: reference.trim() || null,
      }),
    onSuccess: (next) => {
      setAmount("");
      setReference("");
      onUpdated(next);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  const empty = sale.lines.length === 0;
  const settled = isZero(sale.due);
  const ready = !empty && settled && !sale.awaiting_confirmation;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "F12" && ready && !completing) {
        event.preventDefault();
        onComplete();
      } else if (event.key === "F8" && !empty) {
        event.preventDefault();
        setParking(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, completing, empty, onComplete]);

  return (
    <Card title="Payment" flush>
      <div className="register__payment">
        <TotalDisplay
          label={ready ? "Total, paid" : "Total"}
          value={money(sale.total, currency)}
          caption={
            empty
              ? undefined
              : sale.awaiting_confirmation
                ? "Payment awaiting confirmation"
                : settled
                  ? undefined
                  : `${money(sale.due, currency)} still due`
          }
          tone={empty ? "default" : ready ? "settled" : "due"}
        />

        {sale.tenders.length > 0 ? (
          <ul className="register__tenders" aria-label="Payments taken">
            {sale.tenders.map((item) => (
              <TenderRow key={item.id} tender={item} onUpdated={onUpdated} />
            ))}
          </ul>
        ) : null}

        {settled ? null : (
          <>
            <RadioGroup
              legend="Payment method"
              inline
              value={method}
              onChange={setMethod}
              disabled={empty}
              options={METHODS.map((item) => ({
                ...item,
                disabled: item.value === "on_account" && !sale.customer_id,
              }))}
            />
            {method === "cash" ? (
              <div className="stack gap-3">
                <AmountShortcuts
                  suggestions={sale.suggested_cash}
                  disabled={empty || tender.isPending}
                  format={(value) => money(value, currency)}
                  onChoose={(value) => {
                    setAmount(value);
                  }}
                />
                <Field label="Cash received">
                  {(id) => (
                    <TextInput
                      id={id}
                      numeric
                      inputMode="decimal"
                      value={amount}
                      disabled={empty}
                      placeholder={empty ? "" : editable(sale.due)}
                      onChange={(event) => setAmount(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && amount) tender.mutate();
                      }}
                    />
                  )}
                </Field>
                <Keypad
                  value={amount}
                  onChange={setAmount}
                  disabled={empty || tender.isPending}
                  compact
                  completeLabel="Take cash"
                  onComplete={amount ? () => tender.mutate() : undefined}
                />
              </div>
            ) : (
              <div className="stack gap-3">
                <Field
                  label="Amount"
                  hint={`Leave blank to take the ${money(sale.due, currency)} due.`}
                >
                  {(id) => (
                    <TextInput
                      id={id}
                      numeric
                      inputMode="decimal"
                      value={amount}
                      disabled={empty}
                      placeholder={editable(sale.due)}
                      onChange={(event) => setAmount(event.target.value)}
                    />
                  )}
                </Field>
                {method === "on_account" ? null : (
                  <Field
                    label="Reference"
                    optional
                    hint="The provider's transaction reference, if already known."
                  >
                    {(id) => (
                      <TextInput
                        id={id}
                        value={reference}
                        disabled={empty}
                        onChange={(event) => setReference(event.target.value)}
                      />
                    )}
                  </Field>
                )}
                <Button
                  tone="primary"
                  size="transactional"
                  block
                  busy={tender.isPending}
                  disabled={empty}
                  onClick={() => tender.mutate()}
                >
                  {method === "on_account" ? "Charge to account" : "Request payment"}
                </Button>
              </div>
            )}
          </>
        )}

        <div className="register__finish">
          <Button
            tone="primary"
            size="counter"
            block
            busy={completing}
            disabled={!ready}
            onClick={onComplete}
          >
            Complete sale
          </Button>
          {sale.awaiting_confirmation ? (
            <p className="cell-note">
              A payment is awaiting confirmation. The sale completes once it is confirmed.
            </p>
          ) : null}
          <div className="row gap-2">
            <Button size="touch" disabled={empty} onClick={() => setParking(true)}>
              Park sale
            </Button>
          </div>
          <p className="register__keys" aria-label="Keyboard shortcuts">
            <kbd>F2</kbd> Scan · <kbd>F8</kbd> Park · <kbd>F12</kbd> Complete
          </p>
        </div>
      </div>
      {parking ? (
        <ParkDialog
          sale={sale}
          onClose={() => setParking(false)}
          onParked={(next) => {
            setParking(false);
            onUpdated(next);
            toast.show("success", "Sale parked.");
          }}
        />
      ) : null}
    </Card>
  );
}

function TenderRow({
  tender,
  onUpdated,
}: {
  tender: PosTender;
  onUpdated: (sale: PosSale) => void;
}) {
  const currency = useCurrency();
  const toast = useToast();
  const [reference, setReference] = useState(tender.provider_reference ?? "");
  const [confirming, setConfirming] = useState(false);

  const confirm = useMutation({
    mutationFn: () => api.pos.confirmTender(tender.id, reference.trim()),
    onSuccess: onUpdated,
    onError: (error) => toast.show("error", describe(error).message),
  });
  const fail = useMutation({
    mutationFn: () => api.pos.failTender(tender.id, "Not received from the provider"),
    onSuccess: onUpdated,
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <li className="register__tender">
      <div className="register__tender-head">
        <span>
          {tender.method_label}
          {tender.provider_reference ? (
            <span className="cell-note"> · {tender.provider_reference}</span>
          ) : null}
        </span>
        <span className="register__tender-amount">{money(tender.amount, currency)}</span>
      </div>
      <div className="row gap-2 wrap">
        <StatusChip tone={TENDER_TONE[tender.state]}>{tender.state_label}</StatusChip>
        {tender.change_given && !isZero(tender.change_given) ? (
          <span className="cell-note">Change {money(tender.change_given, currency)}</span>
        ) : null}
      </div>
      {tender.state === "pending" ? (
        confirming ? (
          <div className="stack gap-2">
            <Field
              label="Provider reference"
              required
              hint="From the confirmation message or slip."
            >
              {(id) => (
                <TextInput
                  id={id}
                  autoFocus
                  value={reference}
                  onChange={(event) => setReference(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && reference.trim()) confirm.mutate();
                  }}
                />
              )}
            </Field>
            <div className="row gap-2">
              <Button
                tone="primary"
                size="touch"
                busy={confirm.isPending}
                disabled={!reference.trim()}
                onClick={() => confirm.mutate()}
              >
                Confirm payment
              </Button>
              <Button size="touch" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="row gap-2">
            <Button tone="primary" size="touch" onClick={() => setConfirming(true)}>
              Confirm
            </Button>
            <Button size="touch" busy={fail.isPending} onClick={() => fail.mutate()}>
              Not received
            </Button>
          </div>
        )
      ) : null}
    </li>
  );
}

function ParkDialog({
  sale,
  onClose,
  onParked,
}: {
  sale: PosSale;
  onClose: () => void;
  onParked: (sale: PosSale) => void;
}) {
  const queries = useQueryClient();
  const [label, setLabel] = useState("");
  const park = useMutation({
    mutationFn: async () => {
      await api.pos.park(sale.id, label.trim() || null);
      await queries.invalidateQueries({ queryKey: ["pos", "parked"] });
      return api.pos.sale();
    },
    onSuccess: onParked,
  });
  return (
    <Dialog
      title="Park this sale"
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" busy={park.isPending} onClick={() => park.mutate()}>
            Park sale
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {park.isError ? <Alert tone="error" title={describe(park.error).message} /> : null}
        <Field label="Label" optional hint="So the sale is easy to find again.">
          {(id) => (
            <TextInput
              id={id}
              autoFocus
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Customer collecting at 3 pm"
              onKeyDown={(event) => {
                if (event.key === "Enter") park.mutate();
              }}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function CompletedPanel({ sale, onNext }: { sale: PosSale; onNext: () => void }) {
  const currency = useCurrency();
  const toast = useToast();
  const print = useMutation({
    mutationFn: async () => {
      if (!sale.receipt_id) throw new Error("No receipt was issued for this sale.");
      const url = await api.documents.file(sale.receipt_id);
      window.open(url, "_blank", "noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <Card title="Sale complete" subtitle={sale.number ?? undefined}>
      <div className="register__payment">
        <TotalDisplay
          label="Change due"
          value={money(sale.change, currency)}
          caption={`Total ${money(sale.total, currency)}`}
          tone="settled"
        />
        <ul className="register__tenders" aria-label="Payments taken">
          {sale.tenders
            .filter((item) => item.state === "confirmed")
            .map((item) => (
              <li key={item.id} className="register__tender">
                <div className="register__tender-head">
                  <span>{item.method_label}</span>
                  <span className="register__tender-amount">
                    {money(item.cash_received ?? item.amount, currency)}
                  </span>
                </div>
              </li>
            ))}
        </ul>
        <div className="register__finish">
          <Button tone="primary" size="counter" block autoFocus onClick={onNext}>
            Next sale
          </Button>
          <Button size="transactional" block busy={print.isPending} onClick={() => print.mutate()}>
            Print receipt
          </Button>
        </div>
      </div>
    </Card>
  );
}
