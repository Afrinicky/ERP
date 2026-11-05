/**
 * Document numbering.
 *
 * A number is never reused, so the next value may be raised but never lowered,
 * per PRD 6.7.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Sequence } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Dialog,
  Field,
  SectionHeader,
  Select,
  Skeleton,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { useSession } from '@/lib/session';

const CADENCE: { value: string; label: string }[] = [
  { value: 'never', label: 'Never — one continuous series' },
  { value: 'annually', label: 'Annually — restart each year' },
  { value: 'monthly', label: 'Monthly — restart each month' },
];

const PLACEHOLDERS = [
  ['{prefix}', 'The series prefix, such as QTN'],
  ['{branch}', 'The branch code'],
  ['{year}', 'Four-digit year'],
  ['{year2}', 'Two-digit year'],
  ['{month}', 'Two-digit month'],
  ['{day}', 'Two-digit day'],
  ['{counter}', 'The sequence number'],
  ['{counter:4}', 'The sequence number, padded to four digits'],
];

export function Numbering() {
  const { can } = useSession();
  const sequences = useQuery({ queryKey: ['numbering'], queryFn: api.numbering.list });
  const [editing, setEditing] = useState<Sequence | null>(null);

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Document numbering"
        detail="Each document type carries its own series, unique per business and branch."
      />

      <Alert tone="info" title="Numbers are never reused">
        The next number may be raised, to skip a range already issued on paper, but it cannot
        be lowered.
      </Alert>

      <Card flush>
        {sequences.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={6} height={16} />
          </div>
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '22%' }}>Document type</th>
                  <th style={{ width: '10%' }}>Prefix</th>
                  <th style={{ width: '24%' }}>Pattern</th>
                  <th style={{ width: '14%' }}>Restarts</th>
                  <th style={{ width: '18%' }}>Next number</th>
                  {can('numbering.manage') ? <th style={{ width: '10%' }} /> : null}
                </tr>
              </thead>
              <tbody>
                {(sequences.data ?? []).map((sequence) => (
                  <tr key={sequence.id}>
                    <td>{sequence.document_label}</td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {sequence.prefix}
                    </td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {sequence.pattern}
                    </td>
                    <td className="muted">
                      {CADENCE.find((item) => item.value === sequence.reset_cadence)?.label.split(
                        ' — ',
                      )[0]}
                    </td>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {sequence.preview}
                    </td>
                    {can('numbering.manage') ? (
                      <td>
                        <div className="table__actions">
                          <Button tone="plain" size="compact" onClick={() => setEditing(sequence)}>
                            Amend
                          </Button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing ? <SequenceDialog sequence={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function SequenceDialog({
  sequence,
  onClose,
}: {
  sequence: Sequence;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();

  const [prefix, setPrefix] = useState(sequence.prefix);
  const [pattern, setPattern] = useState(sequence.pattern);
  const [cadence, setCadence] = useState(sequence.reset_cadence);
  const [nextValue, setNextValue] = useState(String(sequence.next_value));
  const [error, setError] = useState<unknown>(null);

  const preview = useQuery({
    queryKey: ['numbering', 'preview', sequence.document_kind, pattern, prefix],
    queryFn: () => api.numbering.preview(sequence.document_kind, pattern, prefix),
    retry: false,
  });

  const save = useMutation({
    mutationFn: () =>
      api.numbering.amend(sequence.document_kind, {
        prefix,
        pattern,
        reset_cadence: cadence,
        next_value: Number(nextValue),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['numbering'] });
      toast.show('success', `${sequence.document_label} numbering saved.`);
      onClose();
    },
    onError: setError,
  });

  const lowered = Number(nextValue) < sequence.next_value;

  return (
    <Dialog
      title={`Numbering — ${sequence.document_label}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={!prefix.trim() || !pattern.trim() || lowered}
            onClick={() => save.mutate()}
          >
            Save numbering
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

        <div className="form-grid form-grid--two">
          <Field label="Prefix" required error={fieldError(error, 'prefix')}>
            {(id) => (
              <TextInput
                id={id}
                value={prefix}
                onChange={(event) => setPrefix(event.target.value.toUpperCase())}
                autoFocus
              />
            )}
          </Field>

          <Field label="Restarts" required>
            {(id) => (
              <Select id={id} value={cadence} onChange={(event) => setCadence(event.target.value as Sequence['reset_cadence'])}>
                {CADENCE.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field
            label="Pattern"
            required
            full
            error={fieldError(error, 'pattern')}
            hint="Must include {counter}."
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={pattern}
                onChange={(event) => setPattern(event.target.value)}
                spellCheck={false}
              />
            )}
          </Field>

          <Field
            label="Next number"
            required
            error={lowered ? `Cannot be lowered below ${sequence.next_value}.` : fieldError(error, 'next_value')}
          >
            {(id) => (
              <TextInput
                id={id}
                numeric
                value={nextValue}
                onChange={(event) => setNextValue(event.target.value.replace(/\D/g, ''))}
              />
            )}
          </Field>

          <Field label="Next number will read">
            {(id) => (
              <TextInput
                id={id}
                numeric
                readOnly
                value={preview.isError ? 'Pattern not valid' : (preview.data ?? '…')}
              />
            )}
          </Field>
        </div>

        <fieldset className="fieldset">
          <legend className="fieldset__legend">Available placeholders</legend>
          <dl className="details">
            {PLACEHOLDERS.map(([token, meaning]) => (
              <div key={token} style={{ display: 'contents' }}>
                <dt className="numeric" style={{ textAlign: 'left' }}>
                  {token}
                </dt>
                <dd>{meaning}</dd>
              </div>
            ))}
          </dl>
        </fieldset>
      </div>
    </Dialog>
  );
}
