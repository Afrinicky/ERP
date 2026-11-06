/**
 * Documents: the types the business issues, their layout, and what has been issued.
 *
 * The preview renders through the same engine as final output, so what is approved
 * here is what a customer receives. A receipt can be previewed on an 80mm or 58mm
 * roll, and as the plain text a thermal printer is fed directly.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { DocumentType, PageFormatCode, TemplateDefinition } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextInput,
  useToast,
} from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { describe } from '@/lib/errors';
import { bytes, dateTime } from '@/lib/format';
import { useSession } from '@/lib/session';

type View = 'templates' | 'issued';

const ROLLS: PageFormatCode[] = ['roll_80', 'roll_58'];

const BLOCKS: { key: keyof Toggles; label: string; hint: string }[] = [
  { key: 'show_bill_to', label: 'Bill to', hint: 'Who the document is addressed to.' },
  { key: 'show_ship_to', label: 'Ship to', hint: 'Where the goods are going.' },
  { key: 'show_delivery', label: 'Dispatch', hint: 'Carrier, driver, vehicle and packages.' },
  { key: 'show_totals', label: 'Totals', hint: 'Subtotal, tax and the grand total.' },
  { key: 'show_terms', label: 'Terms', hint: 'Validity, delivery and payment terms.' },
  { key: 'show_banking', label: 'Banking', hint: 'Where payment should be sent.' },
  { key: 'show_notes', label: 'Notes', hint: 'The standout note above the terms.' },
  { key: 'show_signatures', label: 'Signatures', hint: 'Ruled lines for signing.' },
];

const COLUMNS: { key: keyof Toggles; label: string; hint: string }[] = [
  { key: 'show_index_column', label: 'Line number', hint: 'A running count down the table.' },
  { key: 'show_unit_column', label: 'Unit', hint: 'The unit beside each quantity.' },
  {
    key: 'show_discount_column',
    label: 'Discount',
    hint: 'Shown only where a line actually carries one.',
  },
  { key: 'show_batch_and_expiry', label: 'Batch and expiry', hint: 'Beneath the description.' },
  { key: 'show_manufacturer', label: 'Manufacturer', hint: 'Beneath the description.' },
];

interface Toggles {
  show_bill_to: boolean;
  show_ship_to: boolean;
  show_delivery: boolean;
  show_totals: boolean;
  show_terms: boolean;
  show_banking: boolean;
  show_notes: boolean;
  show_signatures: boolean;
  show_index_column: boolean;
  show_unit_column: boolean;
  show_discount_column: boolean;
  show_batch_and_expiry: boolean;
  show_manufacturer: boolean;
}

export function Documents() {
  const { can } = useSession();
  // A cashier may read issued documents without being able to read a template, so
  // the first tab is whichever they are actually permitted to open. Landing on a
  // tab that refuses them is a permission error dressed up as a page.
  const tabs = [
    ...(can('template.view') ? [{ id: 'templates' as View, label: 'Templates' }] : []),
    ...(can('document.view') ? [{ id: 'issued' as View, label: 'Issued' }] : []),
  ];
  const [view, setView] = useState<View>(tabs[0]?.id ?? 'issued');

  return (
    <>
      <ContextBar trail={['Documents', view === 'templates' ? 'Templates' : 'Issued']} />
      <WorkSurface>
        <PageHeading
          title="Documents"
          detail={
            view === 'templates'
              ? 'Every document the business issues, and the layout each is rendered through.'
              : 'Documents already issued, kept as the bytes that were sent.'
          }
        />
        {tabs.length > 1 ? <Tabs items={tabs} active={view} onChange={setView} /> : null}
        {view === 'templates' ? <Templates /> : <Issued />}
      </WorkSurface>
    </>
  );
}

function Templates() {
  const { can } = useSession();
  const [selected, setSelected] = useState<DocumentType | null>(null);

  const types = useQuery({ queryKey: ['documents', 'types'], queryFn: () => api.documents.types() });

  return (
    <div className="stack gap-5">
      <Card
        title={`${(types.data ?? []).length} document types`}
        subtitle="Each carries one published template. Amending publishes the next version."
        flush
      >
        {types.isPending ? (
          <div style={{ padding: 'var(--space-4)' }}>
            <Skeleton rows={6} />
          </div>
        ) : (types.data ?? []).length === 0 ? (
          <EmptyState title="No document types are configured." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Document</th>
                  <th style={{ width: '16%' }}>Stock</th>
                  <th style={{ width: '16%' }}>Classification</th>
                  <th style={{ width: '10%' }}>Version</th>
                  <th style={{ width: '22%' }} />
                </tr>
              </thead>
              <tbody>
                {(types.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>{item.name}</td>
                    <td className="caption">{stockLabel(item.page_format)}</td>
                    <td>
                      <StatusChip tone={item.classification === 'internal' ? 'warning' : 'ok'}>
                        {item.classification === 'internal' ? 'Internal' : 'Customer facing'}
                      </StatusChip>
                    </td>
                    <td className="numeric">{item.published_version ?? '—'}</td>
                    <td className="table__actions">
                      <a
                        className="button button--plain button--compact"
                        href={api.documents.previewUrl(item.id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Preview
                      </a>
                      {can('template.manage') ? (
                        <Button tone="plain" size="compact" onClick={() => setSelected(item)}>
                          Layout
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

      {selected ? <LayoutPanel type={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}

function stockLabel(code: PageFormatCode): string {
  if (code === 'roll_80') return '80mm roll';
  if (code === 'roll_58') return '58mm roll';
  return code.toUpperCase();
}

function LayoutPanel({ type, onClose }: { type: DocumentType; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const template = useQuery({
    queryKey: ['documents', 'template', type.id],
    queryFn: () => api.documents.template(type.id),
  });
  const [draft, setDraft] = useState<Partial<TemplateDefinition>>({});

  const save = useMutation({
    mutationFn: () => api.documents.amendTemplate(type.id, draft),
    onSuccess: (next) => {
      void client.invalidateQueries({ queryKey: ['documents'] });
      toast.show('success', `Published version ${next.version}.`);
      setDraft({});
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const definition = template.data?.definition;
  const value = (key: keyof Toggles): boolean =>
    draft[key] ?? (definition?.[key] as boolean | undefined) ?? false;
  const dirty = Object.keys(draft).length > 0;

  return (
    <Card
      title={`${type.name} — layout`}
      subtitle={
        template.data
          ? `Version ${template.data.version}. Amending publishes the next version; the current one stays readable.`
          : undefined
      }
      actions={
        <>
          <a
            className="button button--secondary button--compact"
            href={api.documents.previewUrl(type.id)}
            target="_blank"
            rel="noreferrer"
          >
            Preview
          </a>
          <Button tone="plain" size="compact" onClick={onClose}>
            Close
          </Button>
        </>
      }
      footer={
        <>
          <span className="caption">
            {dirty ? 'Unpublished changes' : 'No changes'}
          </span>
          <div className="grow" />
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={!dirty}
            onClick={() => save.mutate()}
          >
            Publish next version
          </Button>
        </>
      }
    >
      {template.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <div className="stack gap-5">
          <div className="form-grid">
            <Field label="Stock" hint="A receipt may be laid out for a till roll.">
              {(id) => (
                <Select
                  id={id}
                  value={draft.page ?? definition?.page ?? 'a4'}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      page: event.target.value as PageFormatCode,
                    }))
                  }
                >
                  <option value="a4">A4</option>
                  <option value="a5">A5</option>
                  <option value="letter">Letter</option>
                  <option value="legal">Legal</option>
                  <option value="roll_80">80mm roll</option>
                  <option value="roll_58">58mm roll</option>
                </Select>
              )}
            </Field>
            <div className="form-grid__full">
              <Field label="Footer note" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={draft.footer_note ?? definition?.footer_note ?? ''}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, footer_note: event.target.value }))
                    }
                  />
                )}
              </Field>
            </div>
          </div>

          <div className="stack gap-3">
            <p className="label">Blocks</p>
            <div className="form-grid">
              {BLOCKS.map((block) => (
                <Checkbox
                  key={block.key}
                  label={block.label}
                  hint={block.hint}
                  checked={value(block.key)}
                  onChange={(next) => setDraft((current) => ({ ...current, [block.key]: next }))}
                />
              ))}
            </div>
          </div>

          <div className="stack gap-3">
            <p className="label">Table columns</p>
            <div className="form-grid">
              {COLUMNS.map((column) => (
                <Checkbox
                  key={column.key}
                  label={column.label}
                  hint={column.hint}
                  checked={value(column.key)}
                  onChange={(next) => setDraft((current) => ({ ...current, [column.key]: next }))}
                />
              ))}
            </div>
          </div>

          <ThermalPreview type={type} />
        </div>
      )}
    </Card>
  );
}

function ThermalPreview({ type }: { type: DocumentType }) {
  const [width, setWidth] = useState(42);
  const [open, setOpen] = useState(false);
  const preview = useQuery({
    queryKey: ['documents', 'thermal', type.id, width],
    queryFn: () => api.documents.previewThermal(type.id, width),
    enabled: open,
  });

  return (
    <details
      className="disclosure"
      open={open}
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="disclosure__summary">Thermal printer output</summary>
      <div className="disclosure__body stack gap-3">
        <p className="caption">
          What a counter printer receives when it is driven directly, without a layout
          engine. 42 columns is the usual 80mm default and 32 the 58mm one.
        </p>
        <div className="row gap-2">
          {[42, 32].map((candidate) => (
            <Button
              key={candidate}
              tone={width === candidate ? 'secondary' : 'plain'}
              size="compact"
              onClick={() => setWidth(candidate)}
            >
              {candidate} columns
            </Button>
          ))}
          {ROLLS.map((roll) => (
            <a
              key={roll}
              className="button button--plain button--compact"
              href={api.documents.previewUrl(type.id, roll)}
              target="_blank"
              rel="noreferrer"
            >
              {stockLabel(roll)} PDF
            </a>
          ))}
        </div>
        {preview.isPending ? (
          <Skeleton rows={6} />
        ) : (
          <pre className="receipt-preview">{preview.data?.text}</pre>
        )}
      </div>
    </details>
  );
}

function Issued() {
  const issued = useQuery({
    queryKey: ['documents', 'issued'],
    queryFn: () => api.documents.issued(),
  });

  if (issued.isPending) {
    return (
      <Card>
        <Skeleton rows={6} />
      </Card>
    );
  }

  if ((issued.data ?? []).length === 0) {
    return (
      <Card>
        <EmptyState
          title="Nothing has been issued yet."
          detail="A document appears here the moment it is issued, with the bytes that were sent and a hash of them."
        />
      </Card>
    );
  }

  return (
    <div className="stack gap-4">
      <Alert tone="info" title="A reprint serves the stored copy">
        What was sent is kept verbatim, so a reprint can never differ from the copy the
        customer already holds.
      </Alert>
      <Card flush>
        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: '18%' }}>Number</th>
                <th style={{ width: '16%' }}>Kind</th>
                <th style={{ width: '18%' }}>Issued</th>
                <th style={{ width: '10%' }}>Version</th>
                <th style={{ width: '12%' }}>Size</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(issued.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td className="numeric">{item.number}</td>
                  <td className="caption">{item.kind.replace(/_/g, ' ')}</td>
                  <td className="caption">{dateTime(item.issued_at)}</td>
                  <td className="numeric">{item.template_version ?? '—'}</td>
                  <td className="numeric">{bytes(item.byte_count)}</td>
                  <td className="table__actions">
                    <a
                      className="button button--plain button--compact"
                      href={api.documents.fileUrl(item.id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Reprint
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
