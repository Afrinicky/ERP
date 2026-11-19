/**
 * Inspection: goods held in quarantine until they pass, and goods rejected for return.
 *
 * Neither is available or reservable. Passing releases stock to usable; failing moves it
 * to rejected, from where it is returned to the supplier.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { StockPosition } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  Stepper,
  TextArea,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, quantity } from "@/lib/format";

export function Inspection({ canInspect }: { canInspect: boolean }) {
  const positions = useQuery({
    queryKey: ["inventory", "positions", "", ""],
    queryFn: () => api.inventory.positions(),
  });
  const history = useQuery({
    queryKey: ["inventory", "inspections"],
    queryFn: () => api.inventory.inspections(),
  });
  const [inspecting, setInspecting] = useState<StockPosition | null>(null);
  const [returning, setReturning] = useState<StockPosition | null>(null);

  const held = (positions.data ?? []).filter((row) => Number(row.quarantine) > 0);
  const rejected = (positions.data ?? []).filter((row) => Number(row.rejected) > 0);

  return (
    <div className="stack gap-5">
      <Card title="Awaiting inspection" subtitle="Received into quarantine." flush>
        {positions.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : held.length === 0 ? (
          <EmptyState title="Nothing is awaiting inspection." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: "12%" }}>Warehouse</th>
                  <th style={{ width: "14%" }} className="numeric">
                    In quarantine
                  </th>
                  {canInspect ? <th style={{ width: "16%" }} /> : null}
                </tr>
              </thead>
              <tbody>
                {held.map((row) => (
                  <tr key={`${row.product_id}-${row.warehouse_id}`}>
                    <td>
                      <span className="cell-strong">{row.name}</span>
                      <span className="cell-note">{row.sku}</span>
                    </td>
                    <td>{row.warehouse}</td>
                    <td className="numeric">{quantity(row.quarantine)}</td>
                    {canInspect ? (
                      <td className="table__actions">
                        <Button tone="secondary" size="touch" onClick={() => setInspecting(row)}>
                          Record inspection
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {rejected.length > 0 ? (
        <Card title="Rejected" subtitle="Held for return to the supplier." flush>
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: "12%" }}>Warehouse</th>
                  <th style={{ width: "14%" }} className="numeric">
                    Rejected
                  </th>
                  {canInspect ? <th style={{ width: "16%" }} /> : null}
                </tr>
              </thead>
              <tbody>
                {rejected.map((row) => (
                  <tr key={`${row.product_id}-${row.warehouse_id}`}>
                    <td>
                      <span className="cell-strong">{row.name}</span>
                      <span className="cell-note">{row.sku}</span>
                    </td>
                    <td>{row.warehouse}</td>
                    <td className="numeric">{quantity(row.rejected)}</td>
                    {canInspect ? (
                      <td className="table__actions">
                        <Button tone="secondary" size="touch" onClick={() => setReturning(row)}>
                          Return to supplier
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <Card title="Inspection record" flush>
        {(history.data ?? []).length === 0 ? (
          <EmptyState title="No inspection has been recorded." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: "12%" }}>Date</th>
                  <th>Item</th>
                  <th style={{ width: "12%" }}>Batch</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Passed
                  </th>
                  <th style={{ width: "10%" }} className="numeric">
                    Failed
                  </th>
                  <th style={{ width: "16%" }}>Inspector</th>
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.inspected_on)}</td>
                    <td>
                      {item.name}
                      {item.findings ? <span className="cell-note">{item.findings}</span> : null}
                    </td>
                    <td>{item.batch ?? "—"}</td>
                    <td className="numeric">{quantity(item.quantity_passed)}</td>
                    <td className="numeric">{quantity(item.quantity_failed)}</td>
                    <td>{item.inspector ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {inspecting ? (
        <InspectDialog position={inspecting} onClose={() => setInspecting(null)} />
      ) : null}
      {returning ? <ReturnDialog position={returning} onClose={() => setReturning(null)} /> : null}
    </div>
  );
}

function useBatches(position: StockPosition) {
  return useQuery({
    queryKey: ["inventory", "batches", position.product_id],
    queryFn: () => api.inventory.batches(position.product_id),
    enabled: position.tracking === "batch",
  });
}

function InspectDialog({ position, onClose }: { position: StockPosition; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const held = Number(position.quarantine);
  const [passed, setPassed] = useState(String(held));
  const [failed, setFailed] = useState("0");
  const [criteria, setCriteria] = useState("");
  const [findings, setFindings] = useState("");
  const [batchId, setBatchId] = useState("");
  const batches = useBatches(position);

  const save = useMutation({
    mutationFn: () =>
      api.inventory.inspect({
        product_id: position.product_id,
        warehouse_id: position.warehouse_id,
        passed,
        failed,
        batch_id: batchId || null,
        criteria: criteria || null,
        findings: findings || null,
      }),
    onSuccess: () => {
      toast.show("success", `Inspection of ${position.sku} recorded.`);
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });

  const anything = Number(passed) > 0 || Number(failed) > 0;

  return (
    <Dialog
      title={`Inspect ${position.sku}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => save.mutate()}
            busy={save.isPending}
            disabled={!anything || (Number(failed) > 0 && !findings.trim())}
          >
            Record the outcome
          </Button>
        </>
      }
    >
      <div className="stack gap-5">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <p className="caption">
          {position.name} · {quantity(position.quarantine)} in quarantine at {position.warehouse}
        </p>
        <div className="form-grid">
          <Field label="Passed">
            {(id) => <Stepper id={id} min={0} max={held} value={passed} onChange={setPassed} />}
          </Field>
          <Field label="Failed">
            {(id) => <Stepper id={id} min={0} max={held} value={failed} onChange={setFailed} />}
          </Field>
          {position.tracking === "batch" ? (
            <Field label="Batch" optional>
              {(id) => (
                <Select
                  id={id}
                  value={batchId}
                  onChange={(event) => setBatchId(event.target.value)}
                >
                  <option value="">Any batch held</option>
                  {(batches.data ?? []).map((item) => (
                    <option key={item.batch_id} value={item.batch_id}>
                      {item.batch}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          ) : null}
          <Field label="Criteria" optional full>
            {(id) => (
              <TextInput
                id={id}
                value={criteria}
                onChange={(event) => setCriteria(event.target.value)}
                placeholder="Seal intact, cold chain within 2–8 °C"
              />
            )}
          </Field>
          <Field
            label="Findings"
            required={Number(failed) > 0}
            optional={Number(failed) === 0}
            full
          >
            {(id) => (
              <TextArea
                id={id}
                rows={3}
                value={findings}
                onChange={(event) => setFindings(event.target.value)}
              />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

function ReturnDialog({ position, onClose }: { position: StockPosition; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const held = Number(position.rejected);
  const [amount, setAmount] = useState(String(held));
  const [reason, setReason] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.inventory.returnToSupplier({
        product_id: position.product_id,
        warehouse_id: position.warehouse_id,
        quantity: amount,
        reason,
      }),
    onSuccess: () => {
      toast.show("success", `${position.sku} returned to the supplier.`);
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });

  return (
    <Dialog
      size="sm"
      title={`Return ${position.sku}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            size="transactional"
            onClick={() => save.mutate()}
            busy={save.isPending}
            disabled={Number(amount) <= 0 || !reason.trim()}
          >
            Return to supplier
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.isError ? <Alert tone="error" title={describe(save.error).message} /> : null}
        <Field label="Quantity">
          {(id) => <Stepper id={id} min={0} max={held} value={amount} onChange={setAmount} />}
        </Field>
        <Field label="Reason" required>
          {(id) => (
            <TextInput
              id={id}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Failed inspection: torn seals"
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
