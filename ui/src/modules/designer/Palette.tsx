/**
 * The field palette: everything this template may bind, grouped and searchable.
 *
 * Internal fields are not in the list for a customer-facing template. The service
 * refuses them too; this is the first of the three controls, and the cheapest.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Draft, PaletteField } from '@/api/types';
import {
  Button,
  Card,
  EmptyState,
  Field,
  Select,
  Skeleton,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';

import type { Selection } from './Outline';

export function Palette({
  typeId,
  draft,
  selection,
  onChanged,
}: {
  typeId: string;
  draft: Draft;
  selection: Selection | null;
  onChanged: (draft: Draft) => void;
}) {
  const toast = useToast();
  const [term, setTerm] = useState('');
  const [sectionId, setSectionId] = useState('');

  const palette = useQuery({
    queryKey: ['designer', 'palette', typeId],
    queryFn: () => api.designer.palette(typeId),
  });

  const itemsSections = draft.definition.sections.filter((item) => item.kind === 'items');
  const customSections = draft.definition.sections.filter((item) => item.kind === 'custom');
  const target =
    sectionId ||
    (selection ? selection.sectionId : '') ||
    itemsSections[0]?.id ||
    customSections[0]?.id ||
    '';
  const targetSection = draft.definition.sections.find((item) => item.id === target);
  const intoTable = targetSection?.kind === 'items';

  const insert = useMutation({
    mutationFn: (entry: PaletteField) =>
      intoTable
        ? api.designer.addColumn(draft.template_id, target, entry.key)
        : api.designer.addElement(draft.template_id, target, {
            kind: 'field',
            label: entry.label,
            binding: entry.key,
          }),
    onSuccess: onChanged,
    onError: (error) => toast.show('error', describe(error).message),
  });

  const groups = useMemo(() => {
    const needle = term.trim().toLowerCase();
    const matching = (palette.data ?? []).filter(
      (item) =>
        // A table column repeats down the page, so only a field that varies by line
        // can be one. Offering the rest greyed out fills the palette with controls
        // that do nothing; leaving them out says what belongs here.
        (!intoTable || item.per_line) &&
        (!needle ||
          item.label.toLowerCase().includes(needle) ||
          item.key.toLowerCase().includes(needle)),
    );
    const byGroup = new Map<string, { label: string; fields: PaletteField[] }>();
    for (const item of matching) {
      const bucket = byGroup.get(item.group) ?? { label: item.group_label, fields: [] };
      bucket.fields.push(item);
      byGroup.set(item.group, bucket);
    }
    return [...byGroup.entries()];
  }, [palette.data, term, intoTable]);

  return (
    <Card
      title="Fields"
      subtitle={
        intoTable
          ? 'Becomes a column of the items table. Only fields that vary by line are listed.'
          : 'Placed in the chosen block.'
      }
      flush
    >
      <div className="palette">
        <div className="palette__controls">
          <Field label="Search">
            {(id) => (
              <TextInput
                id={id}
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder="Manufacturer, tax, total…"
              />
            )}
          </Field>
          <Field label="Insert into">
            {(id) => (
              <Select
                id={id}
                value={target}
                onChange={(event) => setSectionId(event.target.value)}
              >
                {draft.definition.sections
                  .filter((item) => item.kind === 'items' || item.kind === 'custom')
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title || item.kind}
                    </option>
                  ))}
              </Select>
            )}
          </Field>
        </div>

        {palette.isPending ? (
          <Skeleton rows={6} />
        ) : !target ? (
          <EmptyState
            title="Nowhere to put a field."
            detail="Add an items table or a custom block to the outline first."
          />
        ) : groups.length === 0 ? (
          <EmptyState title="No field matches." />
        ) : (
          <div className="palette__groups">
            {groups.map(([key, group]) => (
              <div key={key} className="palette__group">
                <p className="column-header">{group.label}</p>
                <ul className="palette__list">
                  {group.fields.map((entry) => (
                    <li key={entry.key}>
                      <Button
                        tone="plain"
                        size="compact"
                        block
                        disabled={!target}
                        onClick={() => insert.mutate(entry)}
                      >
                        {entry.label}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
