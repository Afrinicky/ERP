/**
 * The document designer.
 *
 * Four regions, as the specification sets out: a palette of bindable fields, an
 * outline of the document tree, an inspector for whatever is selected, and a
 * page-accurate preview rendered by the same engine that produces the final PDF.
 *
 * Every operation here has a keyboard path. Reordering is by button and by arrow
 * key; inserting is by selecting a palette entry and pressing Enter. The two input
 * modes call the same endpoints, so neither can quietly acquire behaviour the other
 * lacks — which is what PRD §11 is actually asking for.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Draft, DocumentType } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Select,
  Skeleton,
  StatusChip,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { useSession } from '@/lib/session';

import { Inspector } from './Inspector';
import { NewTypeDialog } from './NewTypeDialog';
import { Outline, type Selection } from './Outline';
import { Palette } from './Palette';

function Preview({ templateId, nonce }: { templateId: string; nonce: number }) {
  const markup = useQuery({
    queryKey: ['designer', 'preview', templateId, nonce],
    queryFn: () => api.designer.previewHtml(templateId),
  });
  if (markup.isPending) {
    return (
      <div style={{ padding: 'var(--space-4)' }}>
        <Skeleton rows={8} />
      </div>
    );
  }
  if (!markup.data) {
    return <EmptyState title="The preview could not be rendered." />;
  }
  return <iframe className="preview-frame" title="Document preview" srcDoc={markup.data} />;
}

export function Designer() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();
  const canManage = can('template.manage');

  const [typeId, setTypeId] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [creating, setCreating] = useState(false);
  const [preview, setPreview] = useState(0);

  const types = useQuery({ queryKey: ['documents', 'types'], queryFn: () => api.documents.types() });
  const chosen = typeId || types.data?.[0]?.id || '';
  const documentType = (types.data ?? []).find((item) => item.id === chosen);

  const draft = useQuery({
    queryKey: ['designer', 'draft', chosen],
    queryFn: () => api.designer.openDraft(chosen),
    enabled: Boolean(chosen) && canManage,
  });

  const refresh = (next: Draft) => {
    client.setQueryData(['designer', 'draft', chosen], next);
    setPreview((value) => value + 1);
  };

  const publish = useMutation({
    mutationFn: (templateId: string) => api.designer.publish(templateId),
    onSuccess: (next) => {
      toast.show('success', `Version ${next.version} published.`);
      void client.invalidateQueries({ queryKey: ['designer'] });
      void client.invalidateQueries({ queryKey: ['documents'] });
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const revert = useMutation({
    mutationFn: (templateId: string) => api.designer.revert(templateId),
    onSuccess: () => {
      toast.show('success', 'Draft discarded.');
      setSelection(null);
      void client.invalidateQueries({ queryKey: ['designer'] });
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  if (!canManage) {
    return (
      <EmptyState
        title="Designing templates requires the template permission."
        detail="Ask an administrator to grant it, or ask them to make the change."
      />
    );
  }

  const current = draft.data;
  const blocking = (current?.findings ?? []).filter((item) => item.is_blocking);
  const advisory = (current?.findings ?? []).filter((item) => !item.is_blocking);

  return (
    <div className="stack gap-5">
      <Card
        title={documentType?.name ?? 'Templates'}
        subtitle={
          current
            ? `Draft version ${current.version}. Version ${current.version - 1} stays in use until you publish.`
            : 'Choose a document type to compose.'
        }
        actions={
          <div className="row gap-2">
            <Select
              aria-label="Document type"
              value={chosen}
              onChange={(event) => {
                setTypeId(event.target.value);
                setSelection(null);
              }}
            >
              {(types.data ?? []).map((item: DocumentType) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
            <Button tone="plain" size="compact" onClick={() => setCreating(true)}>
              New type
            </Button>
            {current ? (
              <>
                <a
                  className="button button--plain button--compact"
                  href={api.designer.exportUrl(current.template_id)}
                  download
                >
                  Export
                </a>
                <Button
                  tone="plain"
                  size="compact"
                  onClick={() => revert.mutate(current.template_id)}
                >
                  Discard draft
                </Button>
                <Button
                  size="compact"
                  disabled={!current.is_publishable}
                  busy={publish.isPending}
                  onClick={() => publish.mutate(current.template_id)}
                >
                  Publish
                </Button>
              </>
            ) : null}
          </div>
        }
      >
        {draft.isPending ? (
          <Skeleton rows={3} />
        ) : !current ? (
          <EmptyState title="No template to compose." />
        ) : (
          <div className="stack gap-3">
            <div className="row gap-3">
              <StatusChip tone={current.is_publishable ? 'ok' : 'error'}>
                {current.is_publishable ? 'Ready to publish' : 'Not publishable'}
              </StatusChip>
              <StatusChip tone="neutral">
                {current.definition.sections.length} sections
              </StatusChip>
              <StatusChip tone="neutral">
                {documentType?.classification === 'internal'
                  ? 'Internal — may show cost'
                  : 'Customer-facing'}
              </StatusChip>
            </div>
            {blocking.length > 0 ? (
              <Alert tone="error" title="This template cannot be published yet">
                {blocking.map((item) => `${item.where}: ${item.message}`).join(' · ')}
              </Alert>
            ) : null}
            {advisory.length > 0 ? (
              <Alert tone="warning" title="Worth checking">
                {advisory.map((item) => item.message).join(' · ')}
              </Alert>
            ) : null}
          </div>
        )}
      </Card>

      {current ? (
        <div className="designer">
          <Palette
            typeId={chosen}
            draft={current}
            selection={selection}
            onChanged={refresh}
          />
          <Outline
            draft={current}
            selection={selection}
            onSelect={setSelection}
            onChanged={refresh}
          />
          <Inspector
            typeId={chosen}
            draft={current}
            selection={selection}
            classification={documentType?.classification ?? 'customer_facing'}
            onChanged={refresh}
          />
        </div>
      ) : null}

      {current ? (
        <Card
          title="Preview"
          subtitle="Rendered by the engine that produces the final document, against sample data."
          actions={
            <Button tone="plain" size="compact" onClick={() => setPreview((v) => v + 1)}>
              Refresh
            </Button>
          }
          flush
        >
          <Preview templateId={current.template_id} nonce={preview} />
        </Card>
      ) : null}

      {creating ? (
        <NewTypeDialog
          onClose={() => setCreating(false)}
          onCreated={(next) => {
            setCreating(false);
            setTypeId(next.type_id);
            void client.invalidateQueries({ queryKey: ['documents', 'types'] });
            void client.invalidateQueries({ queryKey: ['designer'] });
          }}
        />
      ) : null}
    </div>
  );
}
