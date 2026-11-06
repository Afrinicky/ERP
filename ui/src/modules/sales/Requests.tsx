/**
 * Customer requests: what was asked for, and what each enquiry is waiting for.
 *
 * The state is shown as a standing rather than a bare status word, because "open"
 * and "sourcing" mean different things to the person deciding what to do next.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { QuotationRequest } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from "@/components/primitives";
import { describe, fieldError } from "@/lib/errors";
import { date as formatDate, humanise, quantity } from "@/lib/format";

const STATE_TONE: Record<string, Tone> = {
  draft: "neutral",
  open: "info",
  sourcing: "warning",
  quoted: "ok",
  closed: "neutral",
  cancelled: "error",
};

const NEXT_STATE: Record<string, { state: string; label: string }> = {
  draft: { state: "open", label: "Open" },
  open: { state: "sourcing", label: "Sourcing" },
  quoted: { state: "closed", label: "Close" },
};

interface LineForm {
  description: string;
  quantity: string;
  specification: string;
}

export function Requests({
  canManage,
  onSource,
}: {
  canManage: boolean;
  onSource: (requestId: string) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [state, setState] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [detail, setDetail] = useState<QuotationRequest | null>(null);

  const requests = useQuery({
    queryKey: ["sales", "requests", state],
    queryFn: () =>
      api.sourcing.requests({ state: state || undefined, limit: 50 }),
  });

  const advance = useMutation({
    mutationFn: ({ id, next }: { id: string; next: string }) =>
      api.sourcing.setRequestState(id, next),
    onSuccess: (updated) => {
      toast.show(
        "success",
        `${updated.number} is now ${humanise(updated.state)}.`,
      );
      void client.invalidateQueries({ queryKey: ["sales"] });
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <div className="stack gap-5">
      <Card
        title={`${requests.data?.total ?? 0} requests`}
        subtitle="Every enquiry received, newest first."
        actions={
          <div className="row gap-2">
            <Select
              aria-label="Filter by state"
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="">Every state</option>
              <option value="draft">Draft</option>
              <option value="open">Open</option>
              <option value="sourcing">Sourcing</option>
              <option value="quoted">Quoted</option>
              <option value="closed">Closed</option>
            </Select>
            {canManage ? (
              <Button size="compact" onClick={() => setCapturing(true)}>
                Record a request
              </Button>
            ) : null}
          </div>
        }
        flush
      >
        {requests.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={5} />
          </div>
        ) : (requests.data?.items ?? []).length === 0 ? (
          <EmptyState
            title="No request has been recorded."
            detail="A request is the customer’s enquiry in their own words. Recording it first means the quotation answers what was asked."
          />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "13%" }}>Number</th>
                  <th>Customer</th>
                  <th style={{ width: "12%" }}>Received</th>
                  <th style={{ width: "12%" }}>Required by</th>
                  <th className="numeric" style={{ width: "7%" }}>
                    Lines
                  </th>
                  <th style={{ width: "11%" }}>Standing</th>
                  <th style={{ width: "20%" }} />
                </tr>
              </thead>
              <tbody>
                {(requests.data?.items ?? []).map((item) => {
                  const next = NEXT_STATE[item.state];
                  return (
                    <tr key={item.id}>
                      <td>
                        {item.number}
                        {item.customer_reference ? (
                          <span className="cell-note">
                            Their ref {item.customer_reference}
                          </span>
                        ) : null}
                      </td>
                      <td className="table__cell--wrap">
                        {item.customer_name ?? "—"}
                      </td>
                      <td>{formatDate(item.received_on)}</td>
                      <td>
                        {item.required_by ? formatDate(item.required_by) : "—"}
                      </td>
                      <td className="numeric">{item.lines.length}</td>
                      <td>
                        <StatusChip tone={STATE_TONE[item.state] ?? "neutral"}>
                          {humanise(item.state)}
                        </StatusChip>
                      </td>
                      <td className="table__actions">
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => setDetail(item)}
                        >
                          Lines
                        </Button>
                        <Button
                          tone="plain"
                          size="compact"
                          onClick={() => onSource(item.id)}
                        >
                          Source
                        </Button>
                        {canManage && next ? (
                          <Button
                            tone="plain"
                            size="compact"
                            onClick={() =>
                              advance.mutate({ id: item.id, next: next.state })
                            }
                          >
                            {next.label}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {detail ? (
        <Dialog
          size="lg"
          title={`${detail.number} — ${detail.customer_name ?? "Customer"}`}
          onClose={() => setDetail(null)}
        >
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th className="numeric" style={{ width: "8%" }}>
                    #
                  </th>
                  <th>Item</th>
                  <th className="numeric" style={{ width: "18%" }}>
                    Quantity
                  </th>
                  <th style={{ width: "20%" }}>Required by</th>
                </tr>
              </thead>
              <tbody>
                {detail.lines.map((line) => (
                  <tr key={line.id}>
                    <td className="numeric">{line.position}</td>
                    <td className="table__cell--wrap">
                      {line.description}
                      {line.specification ? (
                        <span className="cell-note">{line.specification}</span>
                      ) : null}
                    </td>
                    <td className="numeric">{quantity(line.quantity)}</td>
                    <td>
                      {line.required_by ? formatDate(line.required_by) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Dialog>
      ) : null}

      {capturing ? (
        <CaptureRequest
          onClose={() => setCapturing(false)}
          onSaved={() => {
            setCapturing(false);
            void client.invalidateQueries({ queryKey: ["sales"] });
          }}
        />
      ) : null}
    </div>
  );
}

function CaptureRequest({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const customers = useQuery({
    queryKey: ["parties", "customers"],
    queryFn: () => api.parties.search({ kind: "customer", limit: 200 }),
  });
  const options = customers.data?.items ?? [];

  const [customerId, setCustomerId] = useState("");
  const [reference, setReference] = useState("");
  const [requiredBy, setRequiredBy] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<LineForm[]>([
    { description: "", quantity: "1", specification: "" },
  ]);

  const chosen = customerId || options[0]?.id || "";

  const create = useMutation({
    mutationFn: () =>
      api.sourcing.createRequest({
        customer_id: chosen,
        customer_reference: reference || null,
        required_by: requiredBy || null,
        notes: notes || null,
        lines: lines
          .filter((line) => line.description.trim())
          .map((line) => ({
            description: line.description.trim(),
            quantity: line.quantity,
            specification: line.specification || null,
          })),
      }),
    onSuccess: (created) => {
      toast.show("success", `Request ${created.number} recorded.`);
      onSaved();
    },
  });

  const amend = (index: number, patch: Partial<LineForm>) =>
    setLines((rows) =>
      rows.map((row, position) =>
        position === index ? { ...row, ...patch } : row,
      ),
    );

  return (
    <Dialog
      size="lg"
      title="Record a customer request"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => create.mutate()}
            busy={create.isPending}
            disabled={!chosen}
          >
            Record request
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {create.isError ? (
          <Alert tone="error" title={describe(create.error).message} />
        ) : null}
        <div className="form-grid">
          <Field
            label="Customer"
            required
            error={fieldError(create.error, "customer_id")}
          >
            {(id) => (
              <Select
                id={id}
                value={chosen}
                onChange={(event) => setCustomerId(event.target.value)}
              >
                {options.map((party) => (
                  <option key={party.id} value={party.id}>
                    {party.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field
            label="Their reference"
            optional
            hint="How the customer will refer to this enquiry."
          >
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="BH/2026/114"
              />
            )}
          </Field>
          <Field label="Required by" optional>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={requiredBy}
                onChange={(event) => setRequiredBy(event.target.value)}
              />
            )}
          </Field>
        </div>

        <div className="stack gap-3">
          <span className="column-header">Requested items</span>
          {lines.map((line, index) => (
            <div key={index} className="form-grid">
              <Field label={`Item ${index + 1}`}>
                {(id) => (
                  <TextInput
                    id={id}
                    value={line.description}
                    onChange={(event) =>
                      amend(index, { description: event.target.value })
                    }
                    placeholder="Haematology analyser"
                  />
                )}
              </Field>
              <Field label="Quantity">
                {(id) => (
                  <TextInput
                    id={id}
                    numeric
                    inputMode="decimal"
                    value={line.quantity}
                    onChange={(event) =>
                      amend(index, { quantity: event.target.value })
                    }
                  />
                )}
              </Field>
              <Field label="Specification" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={line.specification}
                    onChange={(event) =>
                      amend(index, { specification: event.target.value })
                    }
                    placeholder="5-part differential"
                  />
                )}
              </Field>
            </div>
          ))}
          <div>
            <Button
              tone="plain"
              size="compact"
              onClick={() =>
                setLines((rows) => [
                  ...rows,
                  { description: "", quantity: "1", specification: "" },
                ])
              }
            >
              Add a line
            </Button>
          </div>
        </div>

        <Field label="Notes" optional full>
          {(id) => (
            <TextArea
              id={id}
              rows={2}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
