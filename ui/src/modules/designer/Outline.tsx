/**
 * The document tree: sections in order, and the items table's columns within it.
 *
 * Reordering is by button, and each button is an ordinary focusable control, so the
 * keyboard path and the pointer path are the same path. There is no drag handler
 * that the keyboard has to imitate.
 */

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Draft, TemplateColumn, TemplateSection } from '@/api/types';
import { Button, Card, Select, StatusChip, useToast } from '@/components/primitives';
import { describe } from '@/lib/errors';
import { humanise } from '@/lib/format';

export type Selection =
  | { kind: 'section'; sectionId: string }
  | { kind: 'column'; sectionId: string; columnId: string }
  | { kind: 'element'; sectionId: string; elementId: string };

export function Outline({
  draft,
  selection,
  onSelect,
  onChanged,
}: {
  draft: Draft;
  selection: Selection | null;
  onSelect: (selection: Selection | null) => void;
  onChanged: (draft: Draft) => void;
}) {
  const toast = useToast();
  const [kind, setKind] = useState('custom');

  const kinds = useQuery({
    queryKey: ['designer', 'sections'],
    queryFn: () => api.designer.sections(),
  });

  const act = useMutation({
    mutationFn: (run: () => Promise<Draft>) => run(),
    onSuccess: onChanged,
    onError: (error) => toast.show('error', describe(error).message),
  });

  const run = (operation: () => Promise<Draft>) => act.mutate(operation);

  const isSelected = (candidate: Selection) =>
    selection !== null &&
    selection.kind === candidate.kind &&
    selection.sectionId === candidate.sectionId &&
    ('columnId' in candidate ? selection.sectionId === candidate.sectionId : true) &&
    JSON.stringify(selection) === JSON.stringify(candidate);

  return (
    <Card
      title="Outline"
      subtitle="Sections in the order they print."
      actions={
        <div className="row gap-2">
          <Select
            aria-label="Section to add"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {(kinds.data ?? []).map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </Select>
          <Button
            tone="plain"
            size="compact"
            onClick={() => run(() => api.designer.addSection(draft.template_id, kind))}
          >
            Add section
          </Button>
        </div>
      }
      flush
    >
      <ul className="outline">
        {draft.definition.sections.map((section: TemplateSection, index: number) => (
          <li key={section.id}>
            <div
              className={`outline__row${
                isSelected({ kind: 'section', sectionId: section.id }) ? ' outline__row--on' : ''
              }`}
            >
              <button
                type="button"
                className="outline__name"
                onClick={() => onSelect({ kind: 'section', sectionId: section.id })}
              >
                {section.title || humanise(section.kind)}
              </button>
              {section.visibility.mode !== 'always' ? (
                <StatusChip tone={section.visibility.mode === 'never' ? 'neutral' : 'info'}>
                  {section.visibility.mode === 'never' ? 'Hidden' : 'Conditional'}
                </StatusChip>
              ) : null}
              <span className="outline__actions">
                <Button
                  tone="plain"
                  size="compact"
                  aria-label={`Move ${section.title || section.kind} up`}
                  disabled={index === 0}
                  onClick={() => run(() => api.designer.moveSection(draft.template_id, section.id, -1))}
                >
                  ↑
                </Button>
                <Button
                  tone="plain"
                  size="compact"
                  aria-label={`Move ${section.title || section.kind} down`}
                  disabled={index === draft.definition.sections.length - 1}
                  onClick={() => run(() => api.designer.moveSection(draft.template_id, section.id, 1))}
                >
                  ↓
                </Button>
                <Button
                  tone="plain"
                  size="compact"
                  aria-label={`Remove ${section.title || section.kind}`}
                  onClick={() => {
                    onSelect(null);
                    run(() => api.designer.removeSection(draft.template_id, section.id));
                  }}
                >
                  Remove
                </Button>
              </span>
            </div>

            {section.columns.length > 0 ? (
              <ul className="outline outline--nested">
                {section.columns.map((column: TemplateColumn, position: number) => (
                  <li key={column.id}>
                    <div
                      className={`outline__row${
                        isSelected({
                          kind: 'column',
                          sectionId: section.id,
                          columnId: column.id,
                        })
                          ? ' outline__row--on'
                          : ''
                      }`}
                    >
                      <button
                        type="button"
                        className="outline__name"
                        onClick={() =>
                          onSelect({
                            kind: 'column',
                            sectionId: section.id,
                            columnId: column.id,
                          })
                        }
                      >
                        {column.label}
                        <span className="cell-note">
                          {column.binding ?? 'formula'} · {column.width_percent}%
                        </span>
                      </button>
                      {column.visibility.mode !== 'always' ? (
                        <StatusChip
                          tone={column.visibility.mode === 'never' ? 'neutral' : 'info'}
                        >
                          {column.visibility.mode === 'never' ? 'Hidden' : 'Conditional'}
                        </StatusChip>
                      ) : null}
                      <span className="outline__actions">
                        <Button
                          tone="plain"
                          size="compact"
                          aria-label={`Move column ${column.label} left`}
                          disabled={position === 0}
                          onClick={() =>
                            run(() =>
                              api.designer.moveColumn(
                                draft.template_id,
                                section.id,
                                column.id,
                                -1,
                              ),
                            )
                          }
                        >
                          ↑
                        </Button>
                        <Button
                          tone="plain"
                          size="compact"
                          aria-label={`Move column ${column.label} right`}
                          disabled={position === section.columns.length - 1}
                          onClick={() =>
                            run(() =>
                              api.designer.moveColumn(
                                draft.template_id,
                                section.id,
                                column.id,
                                1,
                              ),
                            )
                          }
                        >
                          ↓
                        </Button>
                        <Button
                          tone="plain"
                          size="compact"
                          aria-label={`Remove column ${column.label}`}
                          onClick={() => {
                            onSelect(null);
                            run(() =>
                              api.designer.removeColumn(draft.template_id, section.id, column.id),
                            );
                          }}
                        >
                          Remove
                        </Button>
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : null}

            {section.elements.length > 0 ? (
              <ul className="outline outline--nested">
                {section.elements.map((element) => (
                  <li key={element.id}>
                    <div
                      className={`outline__row${
                        isSelected({
                          kind: 'element',
                          sectionId: section.id,
                          elementId: element.id,
                        })
                          ? ' outline__row--on'
                          : ''
                      }`}
                    >
                      <button
                        type="button"
                        className="outline__name"
                        onClick={() =>
                          onSelect({
                            kind: 'element',
                            sectionId: section.id,
                            elementId: element.id,
                          })
                        }
                      >
                        {element.label || humanise(element.kind)}
                        <span className="cell-note">
                          {element.binding ?? (element.expression || humanise(element.kind))}
                        </span>
                      </button>
                      <span className="outline__actions">
                        <Button
                          tone="plain"
                          size="compact"
                          aria-label={`Remove ${element.label || element.kind}`}
                          onClick={() => {
                            onSelect(null);
                            run(() =>
                              api.designer.removeElement(
                                draft.template_id,
                                section.id,
                                element.id,
                              ),
                            );
                          }}
                        >
                          Remove
                        </Button>
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
