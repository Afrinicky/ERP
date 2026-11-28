/**
 * The stock ledger: every movement, newest first. Nothing here is edited; an operation
 * that is still the latest in its pool may be reversed, with a reason.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { StockMovementRow } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, money, quantity } from "@/lib/format";
import { useCurrency } from "@/lib/session";

import { MOVEMENT_LABEL, StateChip } from "./shared";

export function Movements({ canReverse, showCost }: { canReverse: boolean; showCost: boolean }) {
  const currency = useCurrency();
  const [reversing, setReversing] = useState<StockMovementRow | null>(null);
  const movements = useQuery({
    queryKey: ["inventory", "movements"],
    queryFn: () => api.inventory.movements({ limit: 300 }),
  });
  const rows = movements.data ?? [];
  const firstOfOperation = new Set<string>();

  return (
    <Card title="Stock ledger" subtitle="Append-only. A correction is a reversing entry." flush>
      {movements.isPending ? (
        <div style={{ padding: "var(--space-4)" }}>
          <Skeleton rows={6} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState title="No stock has moved yet." />
      ) : (
        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: "10%" }}>Date</th>
                <th style={{ width: "16%" }}>Movement</th>
                <th>Item</th>
                <th style={{ width: "10%" }}>Place</th>
                <th style={{ width: "11%" }}>State</th>
                <th style={{ width: "9%" }} className="numeric">
                  Quantity
                </th>
                {showCost ? (
                  <th style={{ width: "11%" }} className="numeric">
                    Value
                  </th>
                ) : null}
                {canReverse ? <th style={{ width: "9%" }} /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const offer =
                  canReverse && row.reversible && !firstOfOperation.has(row.operation_id);
                firstOfOperation.add(row.operation_id);
                return (
                  <tr key={row.id}>
                    <td>{formatDate(row.occurred_on)}</td>
                    <td>
                      {MOVEMENT_LABEL[row.movement_type] ?? row.movement_type}
                      {row.reference ? <span className="cell-note">{row.reference}</span> : null}
                    </td>
                    <td>
                      {row.sku}
                      {row.batch || row.serial ? (
                        <span className="cell-note">
                          {row.batch ? `Batch ${row.batch}` : ""}
                          {row.serial ? `Serial ${row.serial}` : ""}
                        </span>
                      ) : null}
                      {row.override_reason ? (
                        <span className="cell-note">Expiry override: {row.override_reason}</span>
                      ) : null}
                    </td>
                    <td>
                      {row.warehouse}
                      {row.location ? ` · ${row.location}` : ""}
                    </td>
                    <td>
                      <StateChip state={row.state} />
                    </td>
                    <td className="numeric">{quantity(row.quantity)}</td>
                    {showCost ? (
                      <td className="numeric">
                        {row.state === "reserved" ? "—" : money(row.value, currency)}
                      </td>
                    ) : null}
                    {canReverse ? (
                      <td className="table__actions">
                        {offer ? (
                          <Button tone="plain" size="compact" onClick={() => setReversing(row)}>
                            Reverse
                          </Button>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {reversing ? <ReverseDialog row={reversing} onClose={() => setReversing(null)} /> : null}
    </Card>
  );
}

function ReverseDialog({ row, onClose }: { row: StockMovementRow; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const reverse = useMutation({
    mutationFn: () => api.inventory.reverse(row.operation_id, reason),
    onSuccess: () => {
      toast.show("success", "Operation reversed.");
      void client.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
  });
  return (
    <Dialog
      size="sm"
      title="Reverse the operation"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="destructive"
            onClick={() => reverse.mutate()}
            busy={reverse.isPending}
            disabled={!reason.trim()}
          >
            Reverse
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {reverse.isError ? <Alert tone="error" title={describe(reverse.error).message} /> : null}
        <p className="caption">
          {MOVEMENT_LABEL[row.movement_type] ?? row.movement_type} of {row.sku} on{" "}
          {formatDate(row.occurred_on)}. Every movement of the operation, and its journal, is
          reversed.
        </p>
        <Field label="Reason" required>
          {(id) => (
            <TextInput
              id={id}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Recorded against the wrong item"
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
