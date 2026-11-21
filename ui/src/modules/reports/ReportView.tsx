/**
 * A report as the service returns it: the header that states what it covers, the
 * headline figures, the tables, the reconciliation to the ledger and any notes. Every
 * figure is written by `figure()`, the same rule the PDF export follows.
 */

import { useState } from "react";

import type { ReportFigure, ReportResult, ReportRow, ReportTable } from "@/api/types";
import { Button, StatusChip, TextArea } from "@/components/primitives";
import { dateTime, figure } from "@/lib/format";

export function ReportHeaderFacts({ report }: { report: ReportResult }) {
  const head = report.header;
  const facts: [string, string][] = [
    ["Currency", head.currency],
    ["Accounting basis", head.basis],
    [
      "Generated",
      `${dateTime(head.generated_at)}${head.generated_by ? `, ${head.generated_by}` : ""}`,
    ],
  ];
  if (head.comparison) facts.push(["Compared with", head.comparison]);
  for (const [label, value] of head.filters) facts.push([label, value]);
  return (
    <div className="report__masthead">
      <div className="report__title">
        <span className="report__business">{head.business}</span>
        <h2>{report.title}</h2>
        <span className="report__period">{head.period}</span>
      </div>
      <dl className="report__facts">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="state-row">
        {report.sections.some((section) => section.checks.length > 0) ? (
          <StatusChip tone={report.reconciled ? "ok" : "error"}>
            {report.reconciled ? "Reconciled to the ledger" : "Does not reconcile"}
          </StatusChip>
        ) : null}
        {report.withheld ? (
          <StatusChip tone="neutral">Cost and value columns withheld</StatusChip>
        ) : null}
      </div>
    </div>
  );
}

function Figure({ item, comparison }: { item: ReportFigure; comparison: string | null }) {
  return (
    <div className="report__figure">
      <span className="report__figure-label">{item.label}</span>
      <span className="report__figure-value">{figure(item.value, item.kind)}</span>
      {item.prior !== null ? (
        <span className="report__figure-prior">
          {comparison ?? "Prior"}: {figure(item.prior, item.kind)}
          {item.change !== null ? ` · ${figure(item.change, "percent")}` : ""}
        </span>
      ) : item.note ? (
        <span className="report__figure-prior">{item.note}</span>
      ) : null}
    </div>
  );
}

function TableRow({
  table,
  row,
  onDrill,
}: {
  table: ReportTable;
  row: ReportRow;
  onDrill?: (row: ReportRow) => void;
}) {
  const className = row.emphasis ? `report__row--${row.emphasis}` : undefined;
  return (
    <tr className={className}>
      {table.columns.map((column, position) => {
        const value = row.cells[column.key];
        const text = row.emphasis === "heading" && position > 0 ? "" : figure(value, column.kind);
        const shown = text === "—" && row.emphasis === "heading" ? "" : text;
        const numeric = column.kind !== "text" && column.kind !== "date";
        const indent = position === 0 && row.level > 0 ? " report__cell--indent" : "";
        return (
          <td
            key={column.key}
            className={`${numeric ? "numeric" : ""}${indent}${column.kind === "date" ? " nowrap" : ""}`}
          >
            {position === 0 && row.drill && onDrill ? (
              <button type="button" className="text-button" onClick={() => onDrill(row)}>
                {shown}
              </button>
            ) : (
              shown
            )}
          </td>
        );
      })}
    </tr>
  );
}

export function ReportTableView({
  table,
  heading,
  onDrill,
}: {
  table: ReportTable;
  heading: boolean;
  onDrill?: (row: ReportRow) => void;
}) {
  return (
    <div className="report__table">
      {heading ? <h4>{table.title}</h4> : null}
      {table.rows.length === 0 ? (
        <p className="report__empty">{table.empty}</p>
      ) : (
        <div className="table-frame">
          <table className="table report__grid">
            <thead>
              <tr>
                {table.columns.map((column) => (
                  <th
                    key={column.key}
                    className={column.kind !== "text" && column.kind !== "date" ? "numeric" : ""}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row) => (
                <TableRow key={row.key} table={table} row={row} onDrill={onDrill} />
              ))}
            </tbody>
            {table.total ? (
              <tfoot>
                <TableRow table={table} row={table.total} />
              </tfoot>
            ) : null}
          </table>
        </div>
      )}
      {table.note ? <p className="caption report__note">{table.note}</p> : null}
    </div>
  );
}

export function ReportView({
  report,
  notes,
  onNote,
  onDrill,
}: {
  report: ReportResult;
  notes?: Record<string, string>;
  onNote?: (section: string, text: string | null) => Promise<unknown>;
  onDrill?: (row: ReportRow) => void;
}) {
  return (
    <article className="report">
      <ReportHeaderFacts report={report} />
      {report.sections.map((section) => {
        const single = section.tables.length === 1 && section.tables[0]?.title === section.title;
        const empty =
          section.figures.length === 0 &&
          section.tables.length === 0 &&
          section.checks.length === 0 &&
          section.commentary.length === 0;
        if (empty) return null;
        return (
          <section key={section.key} className="report__section" id={`section-${section.key}`}>
            {report.sections.length > 1 || section.title !== report.title ? (
              <h3 className="report__section-title">{section.title}</h3>
            ) : null}
            {section.figures.length > 0 ? (
              <div className="report__figures">
                {section.figures.map((item) => (
                  <Figure key={item.key} item={item} comparison={report.header.comparison} />
                ))}
              </div>
            ) : null}
            {section.commentary.length > 0 ? (
              <div className="report__commentary">
                {section.commentary.map((sentence) => (
                  <p key={sentence}>{sentence}</p>
                ))}
              </div>
            ) : null}
            {section.tables.map((table) => (
              <ReportTableView
                key={table.key}
                table={table}
                heading={!single || section.tables.length > 1}
                onDrill={onDrill}
              />
            ))}
            {section.checks.length > 0 ? (
              <div className="report__table">
                <h4>Reconciliation to the ledger</h4>
                <div className="table-frame">
                  <table className="table report__grid">
                    <thead>
                      <tr>
                        <th>Check</th>
                        <th className="numeric">Reported</th>
                        <th className="numeric">Ledger</th>
                        <th className="numeric">Difference</th>
                        <th>Standing</th>
                      </tr>
                    </thead>
                    <tbody>
                      {section.checks.map((check) => (
                        <tr key={check.key}>
                          <td>{check.label}</td>
                          <td className="numeric">{figure(check.reported, "money")}</td>
                          <td className="numeric">{figure(check.ledger, "money")}</td>
                          <td className="numeric">{figure(check.difference, "money")}</td>
                          <td>
                            <StatusChip tone={check.passed ? "ok" : "error"}>
                              {check.passed ? "Agrees" : "Differs"}
                            </StatusChip>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
            {onNote || notes?.[section.key] ? (
              <SectionNote
                text={notes?.[section.key] ?? null}
                onSave={onNote ? (text) => onNote(section.key, text) : undefined}
              />
            ) : null}
          </section>
        );
      })}
    </article>
  );
}

function SectionNote({
  text,
  onSave,
}: {
  text: string | null;
  onSave?: (text: string | null) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text ?? "");
  const [busy, setBusy] = useState(false);
  if (editing && onSave) {
    return (
      <div className="report__annotation">
        <TextArea
          rows={3}
          value={draft}
          maxLength={4000}
          aria-label="Note"
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="state-row">
          <Button
            tone="primary"
            size="compact"
            busy={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onSave(draft.trim() || null);
                setEditing(false);
              } finally {
                setBusy(false);
              }
            }}
          >
            Save note
          </Button>
          <Button tone="plain" size="compact" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }
  if (!text) {
    return onSave ? (
      <button
        type="button"
        className="text-button report__add-note"
        onClick={() => setEditing(true)}
      >
        Add a note
      </button>
    ) : null;
  }
  return (
    <div className="report__annotation">
      <span className="report__annotation-label">Note</span>
      <p>{text}</p>
      {onSave ? (
        <button
          type="button"
          className="text-button"
          onClick={() => {
            setDraft(text);
            setEditing(true);
          }}
        >
          Edit note
        </button>
      ) : null}
    </div>
  );
}
