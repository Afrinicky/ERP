/**
 * Catalogue and party import.
 *
 * A file is validated before it commits, and a rejected file leaves nothing behind,
 * per PRD 9.6.
 */

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ImportReport } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Field,
  SectionHeader,
  Select,
  Skeleton,
  StatusChip,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { dateTime, humanise } from '@/lib/format';
import { useSession } from '@/lib/session';

const KINDS: [string, string][] = [
  ['products', 'Products'],
  ['parties', 'Customers and suppliers'],
];

export function DataImport() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);

  const [kind, setKind] = useState('products');
  const [filename, setFilename] = useState('');
  const [content, setContent] = useState('');
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<unknown>(null);

  const history = useQuery({ queryKey: ['imports'], queryFn: () => api.imports.history() });

  const preview = useMutation({
    mutationFn: () => api.imports.preview(kind, filename, content),
    onSuccess: setReport,
    onError: (caught) => {
      setReport(null);
      setError(caught);
    },
  });

  const commit = useMutation({
    mutationFn: () => api.imports.commit(kind, filename, content),
    onSuccess: (result) => {
      setReport(result);
      void client.invalidateQueries({ queryKey: ['imports'] });
      void client.invalidateQueries({ queryKey: ['products'] });
      void client.invalidateQueries({ queryKey: ['parties'] });
      toast.show(
        result.committed ? 'success' : 'error',
        result.committed ? result.summary : 'The file was refused. Nothing was changed.',
      );
    },
    onError: (caught) => {
      setReport(null);
      setError(caught);
    },
  });

  function readFile(file: File) {
    setError(null);
    setReport(null);
    setFilename(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      setContent(result.slice(result.indexOf(',') + 1));
    };
    reader.readAsDataURL(file);
  }

  function clear() {
    setFilename('');
    setContent('');
    setReport(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = '';
  }

  if (!can('product.manage') && !can('customer.manage') && !can('supplier.manage')) {
    return (
      <EmptyState
        title="Import is restricted"
        detail="You do not hold the permission to load catalogue or party data."
      />
    );
  }

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Import"
        detail="A file is checked in full before anything is written, and a file with any fault is refused entirely."
      />

      <Alert tone="info" title="An import is all or nothing">
        Every row is validated first. Where any row is rejected the whole file is refused,
        so a partially loaded catalogue cannot result.
      </Alert>

      <Card
        title="Load a file"
        subtitle="Comma-separated values, saved as UTF-8."
        footer={
          <>
            <Button tone="plain" onClick={clear} disabled={!filename}>
              Clear
            </Button>
            <Button
              tone="secondary"
              busy={preview.isPending}
              disabled={!content}
              onClick={() => preview.mutate()}
            >
              Check the file
            </Button>
            <Button
              tone="primary"
              busy={commit.isPending}
              disabled={!report?.acceptable || report.committed}
              onClick={() => commit.mutate()}
            >
              Apply import
            </Button>
          </>
        }
      >
        <div className="stack gap-4">
          {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

          <div className="form-grid form-grid--two">
            <Field label="What is being imported" required>
              {(id) => (
                <Select
                  id={id}
                  value={kind}
                  onChange={(event) => {
                    setKind(event.target.value);
                    setReport(null);
                  }}
                >
                  {KINDS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            <Field label="File" required hint={filename || 'No file chosen.'}>
              {(id, describedBy) => (
                <input
                  id={id}
                  aria-describedby={describedBy}
                  ref={fileInput}
                  type="file"
                  accept=".csv,text/csv"
                  className="file-input"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) readFile(file);
                  }}
                />
              )}
            </Field>
          </div>

          <p className="caption">
            Not sure of the columns?{' '}
            <a href={api.imports.template(kind)} download>
              Download a template
            </a>
            , which documents each one.
          </p>
        </div>
      </Card>

      {report ? <ReportCard report={report} /> : null}

      <Card title="Past imports" flush>
        {history.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={3} height={16} />
          </div>
        ) : history.data && history.data.length > 0 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>When</th>
                  <th>File</th>
                  <th style={{ width: '12%' }}>Kind</th>
                  <th style={{ width: '14%' }}>Outcome</th>
                  <th style={{ width: '10%' }} className="numeric">
                    Rows
                  </th>
                  <th style={{ width: '10%' }} className="numeric">
                    Created
                  </th>
                  <th style={{ width: '10%' }} className="numeric">
                    Amended
                  </th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((run) => (
                  <tr key={run.id}>
                    <td className="muted">{dateTime(run.started_at)}</td>
                    <td className="table__cell--wrap">{run.filename}</td>
                    <td className="muted">{humanise(run.kind)}</td>
                    <td>
                      {run.status === 'committed' ? (
                        <StatusChip tone="ok">Applied</StatusChip>
                      ) : run.status === 'rejected' ? (
                        <StatusChip tone="error">Refused</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Checked only</StatusChip>
                      )}
                    </td>
                    <td className="numeric">{run.total_rows}</td>
                    <td className="numeric">{run.created_rows || '—'}</td>
                    <td className="numeric">{run.amended_rows || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No import has been run" />
        )}
      </Card>
    </div>
  );
}

function ReportCard({ report }: { report: ImportReport }) {
  const faults = report.rows.filter((row) => row.outcome === 'reject');

  return (
    <Card
      title={report.committed ? 'Import applied' : 'File checked'}
      subtitle={report.filename}
      actions={
        report.committed ? (
          <StatusChip tone="ok">Applied</StatusChip>
        ) : report.acceptable ? (
          <StatusChip tone="ok">Ready to apply</StatusChip>
        ) : (
          <StatusChip tone="error">{report.rejected} to correct</StatusChip>
        )
      }
    >
      <div className="stack gap-4">
        <Alert tone={report.acceptable ? 'success' : 'error'}>{report.summary}</Alert>

        <section className="measure-grid">
          <Measure label="Rows read" value={report.total} />
          <Measure label="To create" value={report.to_create} />
          <Measure label="To amend" value={report.to_amend} />
          <Measure label="Rejected" value={report.rejected} />
        </section>

        {faults.length > 0 ? (
          <div className="table-frame" style={{ border: '1px solid var(--colour-border-default)' }}>
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '10%' }} className="numeric">
                    Line
                  </th>
                  <th style={{ width: '18%' }}>Reference</th>
                  <th style={{ width: '16%' }}>Column</th>
                  <th>What is wrong</th>
                </tr>
              </thead>
              <tbody>
                {faults.map((row) => (
                  <tr key={`${row.line}-${row.reference}`}>
                    <td className="numeric">{row.line}</td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {row.reference || <span className="muted">—</span>}
                    </td>
                    <td className="muted">{row.field ? humanise(row.field) : '—'}</td>
                    <td className="table__cell--wrap">{row.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Measure({ label, value }: { label: string; value: number }) {
  return (
    <div className="measure">
      <span className="measure__label">{label}</span>
      <span className="measure__value">{value.toLocaleString('en-GB')}</span>
    </div>
  );
}
