/** Custom fields, added to records without source-code change, per PRD 6.1. */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { CustomField } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  SectionHeader,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextArea,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { humanise } from '@/lib/format';
import { useSession } from '@/lib/session';

const TYPE_LABEL: Record<string, string> = {
  text: 'Text',
  long_text: 'Long text',
  number: 'Number',
  amount: 'Amount',
  date: 'Date',
  boolean: 'Yes or no',
  choice: 'Single choice',
  multi_choice: 'Multiple choice',
};

export function CustomFields() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const catalogue = useQuery({
    queryKey: ['custom-fields', 'catalogue'],
    queryFn: api.customFields.catalogue,
  });

  const [target, setTarget] = useState('product');
  const [creating, setCreating] = useState(false);
  const [withdrawing, setWithdrawing] = useState<CustomField | null>(null);

  const fields = useQuery({
    queryKey: ['custom-fields', target],
    queryFn: () => api.customFields.list(target, true),
  });

  const withdraw = useMutation({
    mutationFn: ({ field, reason }: { field: CustomField; reason: string }) =>
      api.customFields.withdraw(field.id, reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['custom-fields', target] });
      setWithdrawing(null);
      toast.show('success', 'Field withdrawn. Captured values are retained.');
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const targets = catalogue.data?.targets ?? [];

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Custom fields"
        detail="Fields added here appear on the record and, where enabled, in the document designer."
        actions={
          can('custom_field.manage') ? (
            <Button tone="primary" size="compact" onClick={() => setCreating(true)}>
              Define field
            </Button>
          ) : null
        }
      />

      {targets.length > 0 ? (
        <Tabs
          items={targets.map((item) => ({ id: item, label: humanise(item) }))}
          active={target}
          onChange={setTarget}
        />
      ) : null}

      <Card flush>
        {fields.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={3} height={16} />
          </div>
        ) : fields.data && fields.data.length > 0 ? (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Label</th>
                  <th style={{ width: '18%' }}>Code</th>
                  <th style={{ width: '14%' }}>Type</th>
                  <th style={{ width: '12%' }}>Required</th>
                  <th style={{ width: '14%' }}>On documents</th>
                  <th style={{ width: '12%' }}>Standing</th>
                  {can('custom_field.manage') ? <th style={{ width: '12%' }} /> : null}
                </tr>
              </thead>
              <tbody>
                {fields.data.map((field) => (
                  <tr key={field.id}>
                    <td>{field.label}</td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {field.code}
                    </td>
                    <td>{TYPE_LABEL[field.field_type] ?? field.field_type}</td>
                    <td>{field.is_required ? 'Required' : <span className="muted">Optional</span>}</td>
                    <td>
                      {field.show_on_documents ? (
                        'Available'
                      ) : (
                        <span className="muted">Withheld</span>
                      )}
                    </td>
                    <td>
                      {field.is_active ? (
                        <StatusChip tone="ok">Active</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Withdrawn</StatusChip>
                      )}
                    </td>
                    {can('custom_field.manage') ? (
                      <td>
                        <div className="table__actions">
                          {field.is_active ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => setWithdrawing(field)}
                            >
                              Withdraw
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={`No custom fields on ${humanise(target).toLowerCase()}`}
            detail="Define a field to capture information the standard record does not hold."
            action={
              can('custom_field.manage') ? (
                <Button tone="primary" onClick={() => setCreating(true)}>
                  Define field
                </Button>
              ) : undefined
            }
          />
        )}
      </Card>

      {creating ? (
        <FieldDialog
          target={target}
          types={catalogue.data?.types ?? []}
          onClose={() => setCreating(false)}
          onSaved={() => {
            void client.invalidateQueries({ queryKey: ['custom-fields', target] });
            setCreating(false);
            toast.show('success', 'Field defined.');
          }}
        />
      ) : null}

      {withdrawing ? (
        <ConfirmDialog
          title="Withdraw field"
          record={`${withdrawing.label} (${withdrawing.code})`}
          consequence="The field is withdrawn from new records. Values already captured are retained, so historical records remain complete."
          confirmLabel="Withdraw field"
          requireReason
          busy={withdraw.isPending}
          onConfirm={(reason) => withdraw.mutate({ field: withdrawing, reason })}
          onClose={() => setWithdrawing(null)}
        />
      ) : null}
    </div>
  );
}

function FieldDialog({
  target,
  types,
  onClose,
  onSaved,
}: {
  target: string;
  types: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState('');
  const [code, setCode] = useState('');
  const [fieldType, setFieldType] = useState('text');
  const [required, setRequired] = useState(false);
  const [onDocuments, setOnDocuments] = useState(false);
  const [help, setHelp] = useState('');
  const [choices, setChoices] = useState('');
  const [error, setError] = useState<unknown>(null);

  const needsChoices = fieldType === 'choice' || fieldType === 'multi_choice';

  const define = useMutation({
    mutationFn: () =>
      api.customFields.define({
        target,
        code,
        label,
        field_type: fieldType,
        is_required: required,
        show_on_documents: onDocuments,
        help_text: help || null,
        choices: needsChoices
          ? choices
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean)
          : null,
      }),
    onSuccess: onSaved,
    onError: setError,
  });

  const ready =
    label.trim().length > 0 &&
    code.trim().length >= 3 &&
    (!needsChoices || choices.trim().length > 0);

  return (
    <Dialog
      title={`Define field on ${humanise(target).toLowerCase()}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={define.isPending}
            disabled={!ready}
            onClick={() => define.mutate()}
          >
            Define field
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

        <div className="form-grid form-grid--two">
          <Field label="Label" required error={fieldError(error, 'label')}>
            {(id) => (
              <TextInput
                id={id}
                value={label}
                onChange={(event) => {
                  setLabel(event.target.value);
                  if (!code) {
                    setCode(
                      event.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, '_')
                        .replace(/^_+|_+$/g, ''),
                    );
                  }
                }}
                autoFocus
              />
            )}
          </Field>

          <Field
            label="Code"
            required
            hint="Fixed once the field exists. Used by the document designer."
            error={fieldError(error, 'code')}
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={code}
                onChange={(event) => setCode(event.target.value.toLowerCase())}
                spellCheck={false}
              />
            )}
          </Field>

          <Field label="Type" required hint="Fixed once the field exists.">
            {(id, describedBy) => (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={fieldType}
                onChange={(event) => setFieldType(event.target.value)}
              >
                {types.map((item) => (
                  <option key={item} value={item}>
                    {TYPE_LABEL[item] ?? item}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Guidance" optional hint="Shown beneath the field.">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={help}
                onChange={(event) => setHelp(event.target.value)}
              />
            )}
          </Field>

          {needsChoices ? (
            <Field
              label="Options"
              required
              full
              hint="One per line."
              error={fieldError(error, 'choices')}
            >
              {(id, describedBy) => (
                <TextArea
                  id={id}
                  aria-describedby={describedBy}
                  value={choices}
                  onChange={(event) => setChoices(event.target.value)}
                  rows={5}
                />
              )}
            </Field>
          ) : null}
        </div>

        <div className="stack gap-3">
          <Checkbox
            label="Required"
            hint="A record cannot be saved without this field."
            checked={required}
            onChange={setRequired}
          />
          <Checkbox
            label="Offer to the document designer"
            hint="The field appears in the field palette and may be placed on documents."
            checked={onDocuments}
            onChange={setOnDocuments}
          />
        </div>
      </div>
    </Dialog>
  );
}
