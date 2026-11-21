/**
 * Choose a report, set its window, comparison, grouping and filters, and read it. The
 * figures on screen are the figures the exports write.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import type { ReportCatalogueEntry, ReportParameters, ReportResult, ReportRow } from "@/api/types";
import {
  Alert,
  Button,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";

import { PostingsDialog } from "../planning/shared";
import { ReportView } from "./ReportView";

type Granularity = ReportParameters["granularity"];

const PERIODS: { id: Granularity; label: string }[] = [
  { id: "day", label: "Day" },
  { id: "week", label: "Week" },
  { id: "month", label: "Month" },
  { id: "quarter", label: "Quarter" },
  { id: "year", label: "Year" },
  { id: "custom", label: "Custom" },
];

const VIEW_ROUTES: Record<string, string> = {
  quotation: "/sales",
  quotations: "/sales",
  order: "/sales",
  sales_order: "/sales",
  sales: "/sales",
  purchase_order: "/purchases",
  supplier: "/parties",
  affiliate: "/affiliates",
  shipment: "/dispatch",
  product: "/products",
  operation: "/inventory",
  shift: "/register",
  loan: "/finance",
  debts: "/finance",
  budget: "/planning",
  tax_obligation: "/taxation",
};

export function today(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

export function saveFile(url: string, name: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function fileName(report: { title: string; header: { period: string } }, ext: string) {
  const stem = `${report.title} ${report.header.period}`
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
  return `${stem}.${ext}`;
}

export const FORMATS = [
  { id: "pdf", label: "PDF" },
  { id: "xlsx", label: "Excel" },
  { id: "csv", label: "CSV" },
] as const;

function defaults(entry: ReportCatalogueEntry): ReportParameters {
  return {
    granularity: entry.id === "management_pack" ? "month" : "month",
    anchor: today(),
    start: null,
    end: null,
    comparison: entry.id === "management_pack" ? "prior" : "none",
    grouping: entry.groupings[0]?.key ?? null,
    filters: {},
    basis: "accrual",
  };
}

export function Runner() {
  const toast = useToast();
  const navigate = useNavigate();
  const catalogue = useQuery({
    queryKey: ["reports", "catalogue"],
    queryFn: api.reports.catalogue,
  });
  const [chosen, setChosen] = useState<string>("management_pack");
  const entry = (catalogue.data ?? []).find((item) => item.id === chosen);
  const [saved, setSaved] = useState<{ id: string; value: ReportParameters } | null>(null);
  // Parameters belong to the report they were set for: switching reports never runs the
  // new one with the old one's grouping or filters.
  const params = useMemo(
    () => (entry ? (saved?.id === entry.id ? saved.value : defaults(entry)) : null),
    [entry, saved],
  );
  const setParams = (value: ReportParameters) => setSaved({ id: chosen, value });
  const [drill, setDrill] = useState<{ title: string; measure: string } | null>(null);
  const runnable = entry && entry.available && params;
  const report = useQuery({
    queryKey: ["reports", "run", chosen, params],
    queryFn: () => api.reports.run(chosen, params!),
    enabled: Boolean(runnable),
  });
  const save = useMutation({
    mutationFn: () => api.reports.save(chosen, params!),
    onSuccess: (run) => toast.show("success", `${run.title} for ${run.period_label} saved.`),
    onError: (error) => toast.show("error", describe(error).message),
  });
  const [exporting, setExporting] = useState<string | null>(null);
  const exportAs = async (format: string, result: ReportResult) => {
    setExporting(format);
    try {
      const url = await api.reports.exportFile(chosen, format, params!);
      saveFile(url, fileName(result, format));
    } catch (error) {
      toast.show("error", describe(error).message);
    } finally {
      setExporting(null);
    }
  };

  const groups = useMemo(() => {
    const found = new Map<string, ReportCatalogueEntry[]>();
    for (const item of catalogue.data ?? []) {
      const label = item.id === "management_pack" ? "Management" : item.group_label;
      found.set(label, [...(found.get(label) ?? []), item]);
    }
    return [...found.entries()];
  }, [catalogue.data]);

  const onDrill = (row: ReportRow, result: ReportResult) => {
    const target = row.drill;
    if (!target) return;
    const label = String(row.cells.label ?? row.cells.name ?? row.key);
    if (target.view === "account" && target.code) {
      setDrill({ title: `${label}: postings`, measure: `account:${target.code}` });
      void result;
      return;
    }
    if (target.view === "cash") {
      setDrill({ title: "Cash and bank: postings", measure: "cash" });
      return;
    }
    const route = VIEW_ROUTES[target.view ?? ""];
    if (route) navigate(route);
  };

  if (catalogue.isError) return <Alert tone="error" title={describe(catalogue.error).message} />;
  if (catalogue.isPending) return <Skeleton rows={8} />;

  return (
    <div className="reports">
      <nav className="reports__list" aria-label="Reports">
        {groups.map(([label, items]) => (
          <div key={label} className="reports__group">
            <span className="reports__group-label">{label}</span>
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`reports__item${item.id === chosen ? " reports__item--active" : ""}`}
                aria-current={item.id === chosen ? "true" : undefined}
                disabled={!item.available}
                onClick={() => setChosen(item.id)}
                title={item.available ? item.summary : "Discloses cost or margin; not open to you."}
              >
                <span>{item.title}</span>
                {!item.available ? <span className="reports__lock">Restricted</span> : null}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="reports__body">
        {!entry || !params ? (
          <EmptyState title="Choose a report." />
        ) : (
          <>
            <div className="reports__intro">
              <div>
                <h2 className="reports__heading">{entry.title}</h2>
                <p className="caption">{entry.summary}</p>
              </div>
              {entry.sensitive ? (
                <StatusChip tone="warning">Discloses cost and margin</StatusChip>
              ) : null}
            </div>
            <Parameters entry={entry} value={params} onChange={setParams} />
            {report.isError ? (
              <Alert tone="error" title={describe(report.error).message} />
            ) : report.isPending ? (
              <Skeleton rows={10} />
            ) : report.data ? (
              <>
                <div className="reports__actions">
                  {entry.exportable
                    ? FORMATS.map((format) => (
                        <Button
                          key={format.id}
                          size="compact"
                          busy={exporting === format.id}
                          onClick={() => exportAs(format.id, report.data)}
                        >
                          {format.label}
                        </Button>
                      ))
                    : null}
                  <Button size="compact" onClick={() => window.print()}>
                    Print
                  </Button>
                  <Button
                    tone="primary"
                    size="compact"
                    busy={save.isPending}
                    onClick={() => save.mutate()}
                  >
                    Save this run
                  </Button>
                </div>
                <ReportView report={report.data} onDrill={(row) => onDrill(row, report.data)} />
              </>
            ) : null}
          </>
        )}
      </div>
      {drill && report.data ? (
        <PostingsDialog
          title={drill.title}
          queryKey={[
            "reports",
            "drill",
            drill.measure,
            report.data.header.starts_on,
            report.data.header.ends_on,
          ]}
          load={() =>
            api.analytics.drill(
              drill.measure,
              report.data.header.starts_on,
              report.data.header.ends_on,
            )
          }
          onClose={() => setDrill(null)}
        />
      ) : null}
    </div>
  );
}

const FILTER_LABELS: Record<string, string> = {
  branch: "Branch",
  customer: "Customer",
  supplier: "Supplier",
  affiliate: "Affiliate",
  product: "Product",
  person: "Sales person",
  warehouse: "Warehouse",
  register: "Register",
  channel: "Channel",
};

function useOptions(filter: string): { id: string; label: string }[] {
  const parties = useQuery({
    queryKey: ["reports", "filter", filter],
    queryFn: async (): Promise<{ id: string; label: string }[]> => {
      if (filter === "customer" || filter === "supplier" || filter === "affiliate") {
        const page = await api.parties.search({ kind: filter, limit: 200 });
        return page.items.map((party) => ({ id: party.id, label: party.name }));
      }
      if (filter === "branch") {
        return (await api.business.branches()).map((item) => ({ id: item.id, label: item.name }));
      }
      if (filter === "warehouse") {
        return (await api.business.warehouses()).map((item) => ({ id: item.id, label: item.name }));
      }
      if (filter === "person") {
        return (await api.users.list(false)).map((item) => ({
          id: item.id,
          label: item.full_name,
        }));
      }
      if (filter === "product") {
        const page = await api.catalogue.search({ limit: 200 });
        return page.items.map((item) => ({ id: item.id, label: `${item.sku} ${item.name}` }));
      }
      if (filter === "channel") {
        return [
          { id: "quotation", label: "Quotation-led sales" },
          { id: "register", label: "Point of sale" },
        ];
      }
      return [];
    },
    staleTime: 60_000,
    retry: false,
  });
  return parties.data ?? [];
}

function FilterField({
  filter,
  value,
  onChange,
}: {
  filter: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const options = useOptions(filter);
  if (filter === "register") {
    return (
      <Field label="Register">
        {(id) => (
          <TextInput
            id={id}
            value={value}
            placeholder="All registers"
            onChange={(event) => onChange(event.target.value)}
          />
        )}
      </Field>
    );
  }
  return (
    <Field label={FILTER_LABELS[filter] ?? filter}>
      {(id) => (
        <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">All</option>
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );
}

export function Parameters({
  entry,
  value,
  onChange,
  fixedWindow,
}: {
  entry: ReportCatalogueEntry;
  value: ReportParameters;
  onChange: (value: ReportParameters) => void;
  fixedWindow?: boolean;
}) {
  const set = (changes: Partial<ReportParameters>) => onChange({ ...value, ...changes });
  const filters = entry.filters.filter((item) => item !== "basis");
  return (
    <div className="reports__parameters">
      {!entry.as_at && !fixedWindow ? (
        <div className="reports__window">
          <Tabs
            items={PERIODS}
            active={value.granularity}
            onChange={(granularity) => set({ granularity })}
          />
          {value.granularity === "custom" ? (
            <div className="reports__dates">
              <Field label="From">
                {(id) => (
                  <TextInput
                    id={id}
                    type="date"
                    value={value.start ?? ""}
                    onChange={(event) => set({ start: event.target.value || null })}
                  />
                )}
              </Field>
              <Field label="To">
                {(id) => (
                  <TextInput
                    id={id}
                    type="date"
                    value={value.end ?? ""}
                    onChange={(event) => set({ end: event.target.value || null })}
                  />
                )}
              </Field>
            </div>
          ) : (
            <div className="reports__dates">
              <Field label="Period containing">
                {(id) => (
                  <TextInput
                    id={id}
                    type="date"
                    value={value.anchor ?? ""}
                    onChange={(event) => set({ anchor: event.target.value || null })}
                  />
                )}
              </Field>
            </div>
          )}
        </div>
      ) : null}
      <div className="reports__controls">
        {entry.comparisons.length > 0 ? (
          <Field label="Compare with">
            {(id) => (
              <Select
                id={id}
                value={value.comparison}
                onChange={(event) =>
                  set({ comparison: event.target.value as ReportParameters["comparison"] })
                }
              >
                <option value="none">No comparison</option>
                {entry.comparisons.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        {entry.groupings.length > 0 ? (
          <Field label="Group by">
            {(id) => (
              <Select
                id={id}
                value={value.grouping ?? ""}
                onChange={(event) => set({ grouping: event.target.value })}
              >
                {entry.groupings.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        {entry.ledger ? (
          <Field label="Accounting basis">
            {(id) => (
              <Select
                id={id}
                value={value.basis}
                onChange={(event) =>
                  set({ basis: event.target.value as ReportParameters["basis"] })
                }
              >
                <option value="accrual">Accrual</option>
                <option value="cash">Cash</option>
              </Select>
            )}
          </Field>
        ) : null}
        {filters.map((filter) => (
          <FilterField
            key={filter}
            filter={filter}
            value={value.filters[filter] ?? ""}
            onChange={(chosen) => {
              const next = { ...value.filters };
              if (chosen) next[filter] = chosen;
              else delete next[filter];
              set({ filters: next });
            }}
          />
        ))}
      </div>
    </div>
  );
}
