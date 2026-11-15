/**
 * Recording that the customer accepted a quotation, with the evidence.
 *
 * The purchase order they sent is attached as it arrived and kept immutable. A
 * telephone acceptance is admitted and recorded as such: the alternative is a clerk
 * inventing a purchase order number, which is worse than an honest note of the call.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Quotation, SalesOrder } from "@/api/types";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  Field,
  Select,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe, fieldError } from "@/lib/errors";

const METHODS = [
  { value: "purchase_order", label: "Customer purchase order" },
  { value: "signed_quotation", label: "Signed copy of the quotation" },
  { value: "email", label: "Email confirmation" },
  { value: "verbal", label: "Confirmed by telephone" },
];

const MAXIMUM_BYTES = 25 * 1024 * 1024;

export function AcceptanceDialog({
  quotation,
  onClose,
  onConverted,
}: {
  quotation: Quotation;
  onClose: () => void;
  onConverted: (order: SalesOrder) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [method, setMethod] = useState("purchase_order");
  const [reference, setReference] = useState("");
  const [acceptedOn, setAcceptedOn] = useState("");
  const [notes, setNotes] = useState("");
  const [convert, setConvert] = useState(true);
  const [file, setFile] = useState<{ name: string; type: string; base64: string } | null>(
    null,
  );
  const [tooLarge, setTooLarge] = useState(false);

  const take = async (chosen: File | undefined) => {
    setTooLarge(false);
    if (!chosen) {
      setFile(null);
      return;
    }
    if (chosen.size > MAXIMUM_BYTES) {
      setFile(null);
      setTooLarge(true);
      return;
    }
    const bytes = new Uint8Array(await chosen.arrayBuffer());
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    setFile({ name: chosen.name, type: chosen.type || "application/pdf", base64: btoa(binary) });
  };

  const record = useMutation({
    mutationFn: async () => {
      let evidenceId: string | null = null;
      if (file) {
        const stored = await api.attachments.store({
          filename: file.name,
          media_type: file.type,
          content_base64: file.base64,
          owner_type: "quotation",
          owner_id: quotation.id,
          purpose: "acceptance",
          immutable: true,
        });
        evidenceId = stored.id;
      }
      await api.orders.recordAcceptance(quotation.id, {
        method,
        reference: reference || null,
        accepted_on: acceptedOn || null,
        evidence_attachment_id: evidenceId,
        notes: notes || null,
      });
      return convert ? api.orders.convert(quotation.id, {}) : null;
    },
    onSuccess: (order) => {
      void client.invalidateQueries({ queryKey: ["sales"] });
      if (order) {
        toast.show("success", `${quotation.number} accepted and converted to ${order.number}.`);
        onConverted(order);
      } else {
        toast.show("success", `${quotation.number} recorded as accepted.`);
      }
      onClose();
    },
  });

  const verbal = method === "verbal";
  const ready = verbal ? notes.trim().length > 0 : reference.trim().length > 0 || file !== null;

  return (
    <Dialog
      size="md"
      title={`Record acceptance of ${quotation.number}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => record.mutate()} busy={record.isPending} disabled={!ready}>
            {convert ? "Record and convert to order" : "Record acceptance"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {record.isError ? <Alert tone="error" title={describe(record.error).message} /> : null}
        <div className="form-grid">
          <Field label="How it arrived" required>
            {(id) => (
              <Select id={id} value={method} onChange={(event) => setMethod(event.target.value)}>
                {METHODS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Accepted on" optional hint="Today if left blank.">
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={acceptedOn}
                onChange={(event) => setAcceptedOn(event.target.value)}
              />
            )}
          </Field>
          {!verbal ? (
            <Field
              label="Their reference"
              required={!file}
              hint="The customer's purchase order number, or how they refer to it."
              error={fieldError(record.error, "reference")}
            >
              {(id) => (
                <TextInput
                  id={id}
                  value={reference}
                  onChange={(event) => setReference(event.target.value)}
                  placeholder="BH-PO-2026-0441"
                />
              )}
            </Field>
          ) : null}
          {!verbal ? (
            <Field
              label="The document"
              optional={reference.trim().length > 0}
              required={reference.trim().length === 0}
              hint={file ? file.name : "PDF or image, kept unaltered."}
              error={tooLarge ? "That file is larger than 25 MB." : undefined}
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
        </div>
        <Field
          label={verbal ? "Who confirmed, and what was said" : "Notes"}
          required={verbal}
          optional={!verbal}
          error={fieldError(record.error, "notes")}
        >
          {(id) => (
            <TextArea
              id={id}
              rows={3}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder={verbal ? "Dr Owusu confirmed by telephone at 10:15." : ""}
            />
          )}
        </Field>
        <Checkbox
          label="Convert to a sales order straight away"
          hint="Every quoted figure is carried across; nothing is typed again."
          checked={convert}
          onChange={setConvert}
        />
      </div>
    </Dialog>
  );
}
