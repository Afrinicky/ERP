/**
 * Creating a document type the system did not ship, with its own fields.
 *
 * AC-06. The numbering sequence is created with the type rather than configured
 * afterwards, so a new kind of document can be issued the moment it exists.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Draft } from '@/api/types';
import {
  Alert,
  Button,
  Dialog,
  Field,
  Select,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';

interface FieldForm {
  key: string;
  label: string;
  value_kind: string;
  per_line: boolean;
}

export function NewTypeDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (draft: Draft) => void;
}) {
  const toast = useToast();
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [kind, setKind] = useState('pro_forma');
  const [classification, setClassification] = useState('customer_facing');
  const [prefix, setPrefix] = useState('');
  const [fields, setFields] = useState<FieldForm[]>([
    { key: '', label: '', value_kind: 'text', per_line: false },
  ]);

  const create = useMutation({
    mutationFn: () =>
      api.designer.createType({
        code,
        name,
        kind,
        classification,
        prefix: prefix || null,
        fields: fields
          .filter((item) => item.key.trim() && item.label.trim())
          .map((item) => ({ ...item, key: item.key.trim().toLowerCase() })),
      }),
    onSuccess: (draft) => {
      toast.show('success', `${name} created, with its own numbering.`);
      onCreated(draft);
    },
  });

  const amend = (index: number, patch: Partial<FieldForm>) =>
    setFields((rows) =>
      rows.map((row, position) => (position === index ? { ...row, ...patch } : row)),
    );

  return (
    <Dialog
      size="lg"
      title="New document type"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => create.mutate()}
            busy={create.isPending}
            disabled={!code.trim() || !name.trim()}
          >
            Create type
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {create.isError ? <Alert tone="error" title={describe(create.error).message} /> : null}
        <div className="form-grid">
          <Field label="Code" required hint="Lower case, no spaces." error={fieldError(create.error, 'code')}>
            {(id) => (
              <TextInput
                id={id}
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder="site_report"
              />
            )}
          </Field>
          <Field label="Name" required>
            {(id) => (
              <TextInput
                id={id}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Installation site report"
              />
            )}
          </Field>
          <Field label="Numbering prefix" optional hint="Defaults to the code.">
            {(id) => (
              <TextInput
                id={id}
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
                placeholder="SR"
              />
            )}
          </Field>
          <Field label="Lays out like">
            {(id) => (
              <Select id={id} value={kind} onChange={(event) => setKind(event.target.value)}>
                <option value="pro_forma">Pro forma invoice</option>
                <option value="quotation">Quotation</option>
                <option value="invoice">Invoice</option>
                <option value="receipt">Receipt</option>
                <option value="delivery_note">Delivery note</option>
                <option value="waybill">Waybill</option>
                <option value="packing_list">Packing list</option>
                <option value="purchase_order">Purchase order</option>
              </Select>
            )}
          </Field>
          <Field
            label="Classification"
            hint="An internal document may show cost and margin. A customer-facing one may not, and the choice is fixed once documents have been issued."
          >
            {(id) => (
              <Select
                id={id}
                value={classification}
                onChange={(event) => setClassification(event.target.value)}
              >
                <option value="customer_facing">Customer-facing</option>
                <option value="internal">Internal</option>
              </Select>
            )}
          </Field>
        </div>

        <div className="stack gap-3">
          <span className="column-header">Its own fields</span>
          {fields.map((row, index) => (
            <div key={index} className="form-grid">
              <Field label="Key">
                {(id) => (
                  <TextInput
                    id={id}
                    value={row.key}
                    onChange={(event) => amend(index, { key: event.target.value })}
                    placeholder="engineer"
                  />
                )}
              </Field>
              <Field label="Label">
                {(id) => (
                  <TextInput
                    id={id}
                    value={row.label}
                    onChange={(event) => amend(index, { label: event.target.value })}
                    placeholder="Attending engineer"
                  />
                )}
              </Field>
              <Field label="Holds">
                {(id) => (
                  <Select
                    id={id}
                    value={row.value_kind}
                    onChange={(event) => amend(index, { value_kind: event.target.value })}
                  >
                    <option value="text">Text</option>
                    <option value="money">Money</option>
                    <option value="quantity">Quantity</option>
                    <option value="count">Whole number</option>
                    <option value="date">Date</option>
                    <option value="boolean">Yes or no</option>
                  </Select>
                )}
              </Field>
            </div>
          ))}
          <div>
            <Button
              tone="plain"
              size="compact"
              onClick={() =>
                setFields((rows) => [
                  ...rows,
                  { key: '', label: '', value_kind: 'text', per_line: false },
                ])
              }
            >
              Add a field
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
