/**
 * The configuration every quotation is priced by.
 *
 * Three things, each of which answers a question the build-up raises: where did this
 * margin come from, who may grant this discount, and what does the credit cost.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { InstalmentPlan, MarginPolicy } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { humanise, money, percent } from "@/lib/format";
import { useCurrency } from "@/lib/session";

export function PricingPolicy({ canManage }: { canManage: boolean }) {
  const currency = useCurrency();
  const client = useQueryClient();
  const [editing, setEditing] = useState<MarginPolicy | "new" | null>(null);
  const [banding, setBanding] = useState<InstalmentPlan | null>(null);

  const margins = useQuery({
    queryKey: ["sales", "policy", "margins"],
    queryFn: api.quotations.marginPolicies,
  });
  const limits = useQuery({
    queryKey: ["sales", "policy", "limits"],
    queryFn: api.quotations.discountLimits,
  });
  const plans = useQuery({
    queryKey: ["sales", "policy", "plans"],
    queryFn: api.quotations.plans,
  });

  return (
    <div className="stack gap-5">
      <Card
        title="Margin"
        subtitle="The most specific policy prevails: a product displaces its category, which displaces the house rate."
        actions={
          canManage ? (
            <Button size="compact" onClick={() => setEditing("new")}>
              Set a policy
            </Button>
          ) : null
        }
        flush
      >
        {margins.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : (margins.data ?? []).length === 0 ? (
          <EmptyState title="No margin policy is configured." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "16%" }}>Scope</th>
                  <th>Method</th>
                  <th className="numeric" style={{ width: "14%" }}>
                    Rate
                  </th>
                  <th style={{ width: "14%" }}>Standing</th>
                  <th style={{ width: "16%" }} />
                </tr>
              </thead>
              <tbody>
                {(margins.data ?? []).map((policy) => (
                  <tr key={policy.id}>
                    <td className="table__cell--wrap">
                      {humanise(policy.scope)}
                      {policy.target_name ? (
                        <span className="cell-note">{policy.target_name}</span>
                      ) : null}
                    </td>
                    <td className="table__cell--wrap">
                      {humanise(policy.method)}
                      {policy.notes ? (
                        <span className="cell-note">{policy.notes}</span>
                      ) : null}
                    </td>
                    <td className="numeric">{percent(policy.rate)}</td>
                    <td>
                      {policy.rate === "0.000000" ? (
                        <StatusChip tone="warning">Prices at cost</StatusChip>
                      ) : (
                        <StatusChip tone="ok">In use</StatusChip>
                      )}
                    </td>
                    <td className="table__actions">
                      {canManage ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => setEditing(policy)}
                        >
                          Amend
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

      <Card
        title="Discount limits"
        subtitle="What each role may grant on its own authority. A role with no limit may grant nothing without approval."
        flush
      >
        {limits.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={2} />
          </div>
        ) : (limits.data ?? []).length === 0 ? (
          <EmptyState title="No discount limit is recorded." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Role</th>
                  <th className="numeric" style={{ width: "20%" }}>
                    Maximum rate
                  </th>
                  <th className="numeric" style={{ width: "24%" }}>
                    Maximum amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {(limits.data ?? []).map((limit) => (
                  <tr key={limit.id}>
                    <td>{humanise(limit.role_code)}</td>
                    <td className="numeric">{percent(limit.maximum_rate)}</td>
                    <td className="numeric">
                      {limit.maximum_amount
                        ? money(limit.maximum_amount, currency)
                        : "No ceiling"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Instalment plans"
        subtitle="The finance charge by repayment duration. A duration no band covers cannot be quoted."
        flush
      >
        {plans.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : (plans.data ?? []).length === 0 ? (
          <EmptyState title="No instalment plan is configured." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th style={{ width: "14%" }}>Frequency</th>
                  <th className="numeric" style={{ width: "12%" }}>
                    Deposit
                  </th>
                  <th style={{ width: "34%" }}>Duration bands</th>
                  <th style={{ width: "14%" }} />
                </tr>
              </thead>
              <tbody>
                {(plans.data ?? []).map((plan) => (
                  <tr key={plan.id}>
                    <td className="table__cell--wrap">
                      {plan.name}
                      {plan.description ? (
                        <span className="cell-note">{plan.description}</span>
                      ) : null}
                    </td>
                    <td>{humanise(plan.frequency)}</td>
                    <td className="numeric">{percent(plan.deposit_rate)}</td>
                    <td className="table__cell--wrap">
                      {plan.bands.length === 0
                        ? "No band configured"
                        : plan.bands
                            .map(
                              (band) =>
                                `${band.minimum_months}–${band.maximum_months}m ${percent(band.rate)}`,
                            )
                            .join(" · ")}
                    </td>
                    <td className="table__actions">
                      {canManage ? (
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => setBanding(plan)}
                        >
                          Bands
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

      {editing ? (
        <MarginDialog
          policy={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void client.invalidateQueries({ queryKey: ["sales", "policy"] });
          }}
        />
      ) : null}
      {banding ? (
        <BandsDialog
          plan={banding}
          onClose={() => setBanding(null)}
          onSaved={() => {
            setBanding(null);
            void client.invalidateQueries({ queryKey: ["sales", "policy"] });
          }}
        />
      ) : null}
    </div>
  );
}

function MarginDialog({
  policy,
  onClose,
  onSaved,
}: {
  policy: MarginPolicy | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [scope, setScope] = useState(policy?.scope ?? "business");
  const [method, setMethod] = useState(policy?.method ?? "markup_on_cost");
  const [rate, setRate] = useState(policy?.rate ?? "0");
  const [productId, setProductId] = useState(policy?.product_fk ?? "");
  const [categoryId, setCategoryId] = useState(policy?.category_fk ?? "");

  const products = useQuery({
    queryKey: ["catalogue", "products", "all"],
    queryFn: () => api.catalogue.search({ limit: 200 }),
    enabled: scope === "product",
  });
  const categories = useQuery({
    queryKey: ["catalogue", "categories"],
    queryFn: () => api.catalogue.categories(),
    enabled: scope === "category",
  });

  const save = useMutation({
    mutationFn: () =>
      api.quotations.setMarginPolicy({
        scope,
        method,
        rate,
        product_id: scope === "product" ? productId || null : null,
        category_id: scope === "category" ? categoryId || null : null,
      }),
    onSuccess: () => {
      toast.show("success", "Margin policy saved.");
      onSaved();
    },
  });

  return (
    <Dialog
      title="Margin policy"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} busy={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? (
          <Alert tone="error" title={describe(save.error).message} />
        ) : null}
        <Field label="Applies to" required>
          {(id) => (
            <Select
              id={id}
              value={scope}
              onChange={(event) =>
                setScope(event.target.value as MarginPolicy["scope"])
              }
            >
              <option value="business">The whole business</option>
              <option value="category">One product category</option>
              <option value="product">One product</option>
            </Select>
          )}
        </Field>
        {scope === "category" ? (
          <Field label="Category" required>
            {(id) => (
              <Select
                id={id}
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
              >
                <option value="">Choose a category</option>
                {(categories.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        {scope === "product" ? (
          <Field label="Product" required>
            {(id) => (
              <Select
                id={id}
                value={productId}
                onChange={(event) => setProductId(event.target.value)}
              >
                <option value="">Choose a product</option>
                {(products.data?.items ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.sku} — {item.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        <Field label="Method" required>
          {(id) => (
            <Select
              id={id}
              value={method}
              onChange={(event) =>
                setMethod(event.target.value as MarginPolicy["method"])
              }
            >
              <option value="markup_on_cost">
                Markup on cost — the cost plus that percentage of it
              </option>
              <option value="target_gross_margin">
                Target gross margin — a price of which that percentage is margin
              </option>
            </Select>
          )}
        </Field>
        <Field
          label="Rate"
          hint="A fraction, so 0.25 is twenty-five per cent. The two methods are not the same arithmetic: on a cost of 1,200 markup gives 1,500 and target margin gives 1,600."
        >
          {(id) => (
            <TextInput
              id={id}
              numeric
              inputMode="decimal"
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function BandsDialog({
  plan,
  onClose,
  onSaved,
}: {
  plan: InstalmentPlan;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [rows, setRows] = useState(
    plan.bands.length > 0
      ? plan.bands.map((band) => ({
          minimum_months: String(band.minimum_months),
          maximum_months: String(band.maximum_months),
          rate: band.rate,
          label: band.label,
        }))
      : [{ minimum_months: "1", maximum_months: "3", rate: "0", label: "" }],
  );

  const save = useMutation({
    mutationFn: () =>
      api.quotations.setBands(
        plan.id,
        rows.map((row) => ({
          minimum_months: Number(row.minimum_months),
          maximum_months: Number(row.maximum_months),
          method: "percentage",
          rate: row.rate,
          label: row.label,
        })),
      ),
    onSuccess: () => {
      toast.show("success", "Duration bands saved.");
      onSaved();
    },
  });

  const amend = (index: number, patch: Partial<(typeof rows)[number]>) =>
    setRows((current) =>
      current.map((row, position) =>
        position === index ? { ...row, ...patch } : row,
      ),
    );

  return (
    <Dialog
      size="lg"
      title={`Duration bands — ${plan.name}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} busy={save.isPending}>
            Save bands
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? (
          <Alert tone="error" title={describe(save.error).message} />
        ) : null}
        <p className="muted">
          Bands may not overlap. A duration must fall in exactly one of them, or
          the finance charge would depend on which row was read first.
        </p>
        {rows.map((row, index) => (
          <div key={index} className="form-grid">
            <Field label="From (months)">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="numeric"
                  value={row.minimum_months}
                  onChange={(event) =>
                    amend(index, { minimum_months: event.target.value })
                  }
                />
              )}
            </Field>
            <Field label="To (months)">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="numeric"
                  value={row.maximum_months}
                  onChange={(event) =>
                    amend(index, { maximum_months: event.target.value })
                  }
                />
              )}
            </Field>
            <Field label="Charge" hint="A fraction, so 0.08 is eight per cent.">
              {(id) => (
                <TextInput
                  id={id}
                  numeric
                  inputMode="decimal"
                  value={row.rate}
                  onChange={(event) =>
                    amend(index, { rate: event.target.value })
                  }
                />
              )}
            </Field>
          </div>
        ))}
        <div>
          <Button
            tone="plain"
            size="compact"
            onClick={() =>
              setRows((current) => [
                ...current,
                {
                  minimum_months: "1",
                  maximum_months: "3",
                  rate: "0",
                  label: "",
                },
              ])
            }
          >
            Add a band
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
