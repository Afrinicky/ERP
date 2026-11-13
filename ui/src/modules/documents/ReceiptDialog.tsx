/**
 * Proof that a document was received, per PRD §6.7.
 *
 * The signature is drawn on the screen with a finger or a stylus, which is how it
 * is taken at the door. It is kept as the image that was drawn, with a hash, and
 * cannot be amended afterwards: a receipt that could be edited would be worth
 * nothing in the dispute it exists for.
 */

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { IssuedDocument } from '@/api/types';
import {
  Alert,
  Button,
  Dialog,
  Field,
  Select,
  Skeleton,
  TextArea,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { dateTime } from '@/lib/format';
import { useSession } from '@/lib/session';

const MAXIMUM_EVIDENCE_BYTES = 2 * 1024 * 1024;
/** What the service accepts. Checked here too, so the refusal is immediate. */

const KINDS: { value: string; label: string }[] = [
  { value: 'signature', label: 'Signature taken here' },
  { value: 'scan', label: 'Scan of a signed sheet' },
  { value: 'photograph', label: 'Photograph' },
  { value: 'verbal', label: 'Confirmed by telephone' },
];

export function ReceiptDialog({
  document: issued,
  onClose,
}: {
  document: IssuedDocument;
  onClose: () => void;
}) {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const existing = useQuery({
    queryKey: ['documents', 'acknowledgements', issued.id],
    queryFn: () => api.documents.acknowledgements(issued.id),
  });

  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [location, setLocation] = useState('');
  const [comments, setComments] = useState('');
  const [kind, setKind] = useState('signature');
  const [drawn, setDrawn] = useState(false);
  const [scan, setScan] = useState<{ name: string; base64: string; type: string } | null>(
    null,
  );
  const [tooLarge, setTooLarge] = useState(false);
  const pad = useRef<SignaturePadHandle | null>(null);

  const evidence = (): { base64: string | null; type: string | null } => {
    if (kind === 'signature') return { base64: pad.current?.toBase64() ?? null, type: 'image/png' };
    if (kind === 'verbal') return { base64: null, type: null };
    return { base64: scan?.base64 ?? null, type: scan?.type ?? null };
  };

  const record = useMutation({
    mutationFn: () => {
      const taken = evidence();
      return api.documents.acknowledge(issued.id, {
        recipient_name: name,
        recipient_role: role || null,
        location: location || null,
        comments: comments || null,
        evidence_kind: kind,
        evidence_base64: taken.base64,
        media_type: taken.type,
      });
    },
    onSuccess: () => {
      toast.show('success', `${issued.number} receipted by ${name}.`);
      void client.invalidateQueries({ queryKey: ['documents', 'acknowledgements', issued.id] });
      onClose();
    },
  });

  const needsDrawing = kind === 'signature';
  const needsFile = kind === 'scan' || kind === 'photograph';
  const ready =
    name.trim().length > 0 && (!needsDrawing || drawn) && (!needsFile || scan !== null);

  const take = async (file: File | undefined) => {
    setTooLarge(false);
    if (!file) {
      setScan(null);
      return;
    }
    if (file.size > MAXIMUM_EVIDENCE_BYTES) {
      setScan(null);
      setTooLarge(true);
      return;
    }
    const buffer = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (const byte of buffer) binary += String.fromCharCode(byte);
    setScan({ name: file.name, base64: btoa(binary), type: file.type || 'application/pdf' });
  };

  return (
    <Dialog
      size="lg"
      title={`Proof of receipt — ${issued.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Close
          </Button>
          {can('document.acknowledge') ? (
            <Button onClick={() => record.mutate()} busy={record.isPending} disabled={!ready}>
              Record receipt
            </Button>
          ) : null}
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}

        {existing.isPending ? <Skeleton rows={2} /> : null}
        {(existing.data ?? []).length > 0 ? (
          <div className="stack gap-2">
            <h4 className="caption caption--strong">Already recorded</h4>
            <ul className="receipt-list">
              {(existing.data ?? []).map((item) => (
                <li key={item.id}>
                  <span>{item.recipient_name}</span>
                  <span className="caption">
                    {item.recipient_role ? `${item.recipient_role} · ` : ''}
                    {dateTime(item.acknowledged_at)}
                    {item.location ? ` · ${item.location}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {can('document.acknowledge') ? (
          <>
            <div className="form-grid">
              <Field
                label="Received by"
                required
                error={fieldError(record.error, 'recipient_name')}
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="A. Mensah"
                  />
                )}
              </Field>
              <Field label="Capacity" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={role}
                    onChange={(event) => setRole(event.target.value)}
                    placeholder="Stores officer"
                  />
                )}
              </Field>
              <Field label="Where" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={location}
                    onChange={(event) => setLocation(event.target.value)}
                    placeholder="Korle Bu, Accra"
                  />
                )}
              </Field>
              <Field label="Evidence">
                {(id) => (
                  <Select id={id} value={kind} onChange={(event) => setKind(event.target.value)}>
                    {KINDS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>

            {needsDrawing ? (
              <Field
                label="Signature"
                required
                hint="Choose a scan or a telephone confirmation instead where signing here is not practical."
              >
                {(id) => <SignaturePad id={id} handle={pad} onDrawnChange={setDrawn} />}
              </Field>
            ) : null}

            {needsFile ? (
              <Field
                label={kind === 'scan' ? 'Signed sheet' : 'Photograph'}
                required
                hint={scan ? scan.name : 'PDF or image, up to 2 MB.'}
                error={tooLarge ? 'That file is larger than 2 MB.' : undefined}
              >
                {(id) => (
                  <input
                    id={id}
                    className="input"
                    type="file"
                    accept="image/*,application/pdf"
                    onChange={(event) => void take(event.target.files?.[0])}
                  />
                )}
              </Field>
            ) : null}

            <Field label="Notes" optional>
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={comments}
                  onChange={(event) => setComments(event.target.value)}
                  placeholder="Two cartons short; balance to follow."
                />
              )}
            </Field>
          </>
        ) : (
          <Alert tone="info" title="You may read receipts but not record them.">
            Ask the proprietor for permission to capture proof of receipt.
          </Alert>
        )}
      </div>
    </Dialog>
  );
}

interface SignaturePadHandle {
  toBase64: () => string | null;
  clear: () => void;
}

/**
 * A canvas drawn on with a finger, a stylus or a mouse, cleared by its own button.
 *
 * The handle is an ordinary prop rather than a `ref`: React reserves that name and
 * strips it from a function component's props, so the parent would hold nothing.
 */
function SignaturePad({
  id,
  handle,
  onDrawnChange,
}: {
  id: string;
  handle: React.MutableRefObject<SignaturePadHandle | null>;
  onDrawnChange: (drawn: boolean) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [drawn, setDrawn] = useState(false);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    // Backed at twice the drawn size so the stored signature is not a blur on a
    // dispute printed at full page width.
    const scale = 2;
    element.width = element.clientWidth * scale;
    element.height = element.clientHeight * scale;
    const context = element.getContext('2d');
    if (!context) return;
    context.scale(scale, scale);
    context.lineWidth = 1.6;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.strokeStyle =
      getComputedStyle(element).getPropertyValue('--colour-signature-ink').trim() ||
      'black';
  }, []);

  useEffect(() => {
    handle.current = {
      toBase64: () => {
        const element = canvas.current;
        if (!element || !drawn) return null;
        return element.toDataURL('image/png').split(',')[1] ?? null;
      },
      clear: () => {
        const element = canvas.current;
        const context = element?.getContext('2d');
        if (element && context) context.clearRect(0, 0, element.width, element.height);
        setDrawn(false);
        onDrawnChange(false);
      },
    };
  }, [handle, drawn, onDrawnChange]);

  const draw = (event: React.PointerEvent<HTMLCanvasElement>, start: boolean) => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const box = element.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    if (start) {
      context.beginPath();
      context.moveTo(x, y);
      return;
    }
    context.lineTo(x, y);
    context.stroke();
    if (!drawn) {
      setDrawn(true);
      onDrawnChange(true);
    }
  };

  return (
    <div className="signature">
      <canvas
        id={id}
        ref={canvas}
        className="signature__pad"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          draw(event, true);
        }}
        onPointerMove={(event) => {
          if (event.buttons === 0) return;
          draw(event, false);
        }}
      />
      <div className="signature__actions">
        <span className="caption">Sign above.</span>
        <Button tone="plain" size="compact" onClick={() => handle.current?.clear()}>
          Clear
        </Button>
      </div>
    </div>
  );
}
