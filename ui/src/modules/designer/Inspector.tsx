/**
 * The inspector: the properties of whatever is selected.
 *
 * A formula is checked as it is typed, against the sample document, so the author
 * sees both that it parses and what it produces. A formula that is legal and means
 * something other than what was intended is the failure this guards against; the
 * parser alone cannot catch it.
 */

import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type {
  Draft,
  TemplateColumn,
  TemplateElement,
  TemplateVisibility,
} from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  Select,
  StatusChip,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { humanise } from '@/lib/format';

import type { Selection } from './Outline';

export function Inspector({
  typeId,
  draft,
  selection,
  classification,
  onChanged,
}: {
  typeId: string;
  draft: Draft;
  selection: Selection | null;
  classification: string;
  onChanged: (draft: Draft) => void;
}) {
  const toast = useToast();

  const operators = useQuery({
    queryKey: ['designer', 'operators'],
    queryFn: () => api.designer.operators(),
  });
  const palette = useQuery({
    queryKey: ['designer', 'palette', typeId],
    queryFn: () => api.designer.palette(typeId),
  });

  const apply = useMutation({
    mutationFn: (run: () => Promise<Draft>) => run(),
    onSuccess: onChanged,
    onError: (error) => toast.show('error', describe(error).message),
  });

  if (!selection) {
    return (
      <Card title="Properties" subtitle="Select a section, column or element.">
        <EmptyState
          title="Nothing selected."
          detail="Choose an item from the outline to change its label, width, alignment, format or visibility."
        />
      </Card>
    );
  }

  const section = draft.definition.sections.find((item) => item.id === selection.sectionId);
  if (!section) {
    return (
      <Card title="Properties">
        <EmptyState title="That item is no longer in the template." />
      </Card>
    );
  }

  if (selection.kind === 'column') {
    const column = section.columns.find((item) => item.id === selection.columnId);
    if (!column) {
      return (
        <Card title="Properties">
          <EmptyState title="That column is no longer in the template." />
        </Card>
      );
    }
    return (
      <ColumnInspector
        column={column}
        classification={classification}
        operators={(operators.data ?? []).map((item) => ({ code: item.code, label: item.label }))}
        fieldKeys={(palette.data ?? []).map((item) => ({ key: item.key, label: item.label }))}
        onApply={(changes) =>
          apply.mutate(() =>
            api.designer.amendColumn(draft.template_id, section.id, column.id, changes),
          )
        }
      />
    );
  }

  if (selection.kind === 'element') {
    const element = section.elements.find((item) => item.id === selection.elementId);
    if (!element) {
      return (
        <Card title="Properties">
          <EmptyState title="That element is no longer in the template." />
        </Card>
      );
    }
    return (
      <ElementInspector
        element={element}
        classification={classification}
        onApply={(changes) =>
          apply.mutate(() =>
            api.designer.amendElement(draft.template_id, section.id, element.id, changes),
          )
        }
      />
    );
  }

  return (
    <Card title="Section" subtitle={humanise(section.kind)}>
      <div className="stack gap-4">
        <Field label="Heading" hint="Printed above the section. Leave blank for none.">
          {(id) => (
            <TextInput
              id={id}
              defaultValue={section.title}
              onBlur={(event) =>
                apply.mutate(() =>
                  api.designer.save(draft.template_id, {
                    ...draft.definition,
                    sections: draft.definition.sections.map((item) =>
                      item.id === section.id ? { ...item, title: event.target.value } : item,
                    ),
                  }),
                )
              }
            />
          )}
        </Field>
        <VisibilityEditor
          visibility={section.visibility}
          operators={(operators.data ?? []).map((item) => ({ code: item.code, label: item.label }))}
          fieldKeys={(palette.data ?? []).map((item) => ({ key: item.key, label: item.label }))}
          onApply={(visibility) =>
            apply.mutate(() =>
              api.designer.save(draft.template_id, {
                ...draft.definition,
                sections: draft.definition.sections.map((item) =>
                  item.id === section.id ? { ...item, visibility } : item,
                ),
              }),
            )
          }
        />
      </div>
    </Card>
  );
}

function ColumnInspector({
  column,
  classification,
  operators,
  fieldKeys,
  onApply,
}: {
  column: TemplateColumn;
  classification: string;
  operators: { code: string; label: string }[];
  fieldKeys: { key: string; label: string }[];
  onApply: (changes: Record<string, unknown>) => void;
}) {
  return (
    <Card title="Column" subtitle={column.binding ?? 'Calculated'}>
      <div className="stack gap-4">
        <Field label="Heading" hint="Independent of the field it is bound to.">
          {(id) => (
            <TextInput
              id={id}
              defaultValue={column.label}
              onBlur={(event) => onApply({ label: event.target.value })}
            />
          )}
        </Field>
        <div className="form-grid">
          <Field label="Width" hint="Per cent of the printable width.">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                defaultValue={String(column.width_percent)}
                onBlur={(event) =>
                  onApply({ width_percent: Number(event.target.value) || column.width_percent })
                }
              />
            )}
          </Field>
          <Field label="Alignment">
            {(id) => (
              <Select
                id={id}
                value={column.alignment}
                onChange={(event) => onApply({ alignment: event.target.value })}
              >
                <option value="left">Left</option>
                <option value="centre">Centre</option>
                <option value="right">Right</option>
              </Select>
            )}
          </Field>
          <Field label="Decimal places" hint="Blank uses the currency's own.">
            {(id) => (
              <TextInput
                id={id}
                numeric
                inputMode="numeric"
                defaultValue={column.format.precision === null ? '' : String(column.format.precision)}
                onBlur={(event) =>
                  onApply({
                    format: {
                      ...column.format,
                      precision: event.target.value === '' ? null : Number(event.target.value),
                    },
                  })
                }
              />
            )}
          </Field>
        </div>
        <Checkbox
          label="Show the currency symbol"
          checked={column.format.currency_symbol}
          onChange={(checked) =>
            onApply({ format: { ...column.format, currency_symbol: checked } })
          }
        />
        <Checkbox
          label="Add this column up beneath the table"
          checked={column.subtotal}
          onChange={(checked) => onApply({ subtotal: checked })}
        />
        <VisibilityEditor
          visibility={column.visibility}
          operators={operators}
          fieldKeys={fieldKeys}
          onApply={(visibility) => onApply({ visibility })}
        />
        <FormulaEditor
          value={column.expression}
          classification={classification}
          hint="Leave blank to use the bound field. A formula replaces it."
          onApply={(expression) => onApply({ expression })}
        />
      </div>
    </Card>
  );
}

function ElementInspector({
  element,
  classification,
  onApply,
}: {
  element: TemplateElement;
  classification: string;
  onApply: (changes: Record<string, unknown>) => void;
}) {
  return (
    <Card title="Element" subtitle={humanise(element.kind)}>
      <div className="stack gap-4">
        <Field label="Label">
          {(id) => (
            <TextInput
              id={id}
              defaultValue={element.label}
              onBlur={(event) => onApply({ label: event.target.value })}
            />
          )}
        </Field>
        {element.kind === 'text' ? (
          <Field label="Text">
            {(id) => (
              <TextInput
                id={id}
                defaultValue={element.text}
                onBlur={(event) => onApply({ text: event.target.value })}
              />
            )}
          </Field>
        ) : null}
        {element.kind === 'calculated' ? (
          <FormulaEditor
            value={element.expression}
            classification={classification}
            hint="The value this element prints."
            onApply={(expression) => onApply({ expression })}
          />
        ) : null}
        <Checkbox
          label="Emphasise"
          checked={element.emphasis}
          onChange={(checked) => onApply({ emphasis: checked })}
        />
      </div>
    </Card>
  );
}

function VisibilityEditor({
  visibility,
  operators,
  fieldKeys,
  onApply,
}: {
  visibility: TemplateVisibility;
  operators: { code: string; label: string }[];
  fieldKeys: { key: string; label: string }[];
  onApply: (visibility: TemplateVisibility) => void;
}) {
  const condition = visibility.conditions[0];
  return (
    <div className="stack gap-3">
      <Field label="Shown" hint="A condition keeps a block off the page until it applies.">
        {(id) => (
          <Select
            id={id}
            value={visibility.mode}
            onChange={(event) =>
              onApply({
                ...visibility,
                mode: event.target.value as TemplateVisibility['mode'],
                conditions:
                  event.target.value === 'conditional' && visibility.conditions.length === 0
                    ? [{ field: fieldKeys[0]?.key ?? '', operator: 'is_not_empty', value: null, values: [] }]
                    : visibility.conditions,
              })
            }
          >
            <option value="always">Always</option>
            <option value="conditional">When a condition holds</option>
            <option value="never">Never</option>
          </Select>
        )}
      </Field>
      {visibility.mode === 'conditional' && condition ? (
        <div className="form-grid">
          <Field label="Field">
            {(id) => (
              <Select
                id={id}
                value={condition.field}
                onChange={(event) =>
                  onApply({
                    ...visibility,
                    conditions: [{ ...condition, field: event.target.value }],
                  })
                }
              >
                {fieldKeys.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Test">
            {(id) => (
              <Select
                id={id}
                value={condition.operator}
                onChange={(event) =>
                  onApply({
                    ...visibility,
                    conditions: [{ ...condition, operator: event.target.value }],
                  })
                }
              >
                {operators.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Value" optional>
            {(id) => (
              <TextInput
                id={id}
                defaultValue={condition.value ?? ''}
                onBlur={(event) =>
                  onApply({
                    ...visibility,
                    conditions: [{ ...condition, value: event.target.value || null }],
                  })
                }
              />
            )}
          </Field>
        </div>
      ) : null}
    </div>
  );
}

function FormulaEditor({
  value,
  classification,
  hint,
  onApply,
}: {
  value: string;
  classification: string;
  hint: string;
  onApply: (expression: string) => void;
}) {
  const [source, setSource] = useState(value);
  useEffect(() => setSource(value), [value]);

  const check = useQuery({
    queryKey: ['designer', 'expression', source, classification],
    queryFn: () => api.designer.checkExpression(source, classification),
    enabled: source.trim().length > 0,
  });

  return (
    <div className="stack gap-2">
      <Field label="Formula" optional hint={hint}>
        {(id) => (
          <TextInput
            id={id}
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder="round(sum([line.total]) * 0.15, 2)"
          />
        )}
      </Field>
      {source.trim() && check.data ? (
        check.data.is_valid ? (
          <div className="row gap-3">
            <StatusChip tone="ok">Valid</StatusChip>
            {check.data.sample ? (
              <span className="muted">On the sample document: {check.data.sample}</span>
            ) : null}
            {check.data.message ? <span className="muted">{check.data.message}</span> : null}
          </div>
        ) : (
          <Alert tone="error" title={check.data.message ?? 'That formula is not valid.'} />
        )
      ) : null}
      <div>
        <Button
          tone="plain"
          size="compact"
          disabled={source === value || (source.trim().length > 0 && !check.data?.is_valid)}
          onClick={() => onApply(source)}
        >
          Apply formula
        </Button>
      </div>
    </div>
  );
}
