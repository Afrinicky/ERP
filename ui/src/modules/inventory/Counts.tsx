/**
 * Stock counts. The system quantity is fixed when the count opens; the counter records
 * what is on the shelf, gives a reason for every difference, and submits; an approver
 * posts the variances.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { StockCountSheet } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Skeleton,
  StatusChip,
  TextInput,
  type Tone,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { date as formatDate, quantity } from "@/lib/format";

import { useWarehouses, WarehouseSelect } from "./shared";

const STATE: Record<StockCountSheet["state"], [string, Tone]> = {
  open: ["Counting", "info"],
  submitted: ["Awaiting approval", "warning"],
  posted: ["Posted", "ok"],
  cancelled: ["Cancelled", "neutral"],
};

export function Counts({ canCount, canApprove }: { canCount: boolean; canApprove: boolean }) {
  const [opening, setOpening] = useState(false);
  const [sheetId, setSheetId] = useState<string | null>(null);
  const counts = useQuery({
    queryKey: ["inventory", "counts"],
    queryFn: () => api.inventory.counts(),
  });

  return (
    <div className="stack gap-5">
      <Card
        title="Stock counts"
        actions={
          canCount ? (
            <Button tone="primary" onClick={() => setOpening(true)}>
              Begin a count
            </Button>
          ) : undefined
        }
        flush
      >
        {counts.isPending ? (
          <div style={{ padding: "var(--space-4)" }}>
            <Skeleton rows={3} />
          </div>
        ) : (counts.data ?? []).length === 0 ? (
          <EmptyState title="No count has been taken." />
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "14%" }}>Number</th>
                  <th style={{ width: "12%" }}>Date</th>
                  <th>Warehouse</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Lines
                  </th>
                  <th style={{ width: "16%" }}>Standing</th>
                  <th style={{ width: "12%" }} />
                </tr>
              </thead>
              <tbody>
                {(counts.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <td className="numeric">{item.number ?? "—"}</td>
                    <td>{formatDate(item.counted_on)}</td>
                    <td>
                      {item.warehouse}
                      {item.location ? ` · ${item.location}` : ""}
                    </td>
                    <td className="numeric">{item.lines.length}</td>
                    <td>
                      <StatusChip tone={STATE[item.state][1]}>{STATE[item.state][0]}</StatusChip>
                    </td>
                    <td className="table__actions">
                      <Button tone="plain" size="compact" onClick={() => setSheetId(item.id)}>
                        Open
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {opening ? (
        <OpenDialog
          onClose={() => setOpening(false)}
          onOpened={(id) => {
            setOpening(false);
            setSheetId(id);
          }}
        />
      ) : null}
      {sheetId ? (
        <Sheet
          countId={sheetId}
          canCount={canCount}
          canApprove={canApprove}
          onClose={() => setSheetId(null)}
        />
      ) : null}
    </div>
  );
}

function OpenDialog({
  onClose,
  onOpened,
}: {
  onClose: () => void;
  onOpened: (id: string) => void;
}) {
  const client = useQueryClient();
  const warehouses = useWarehouses();
  const [warehouseId, setWarehouseId] = useState("");
  const open = useMutation({
    mutationFn: () => api.inventory.openCount({ warehouse_id: warehouseId }),
    onSuccess: (count) => {
      void client.invalidateQueries({ queryKey: ["inventory", "counts"] });
      onOpened(count.id);
    },
  });
  return (
    <Dialog
      size="sm"
      title="Begin a count"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            onClick={() => open.mutate()}
            busy={open.isPending}
            disabled={!warehouseId}
          >
            Prepare count sheet
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {open.isError ? <Alert tone="error" title={describe(open.error).message} /> : null}
        <WarehouseSelect
          value={warehouseId}
          onChange={setWarehouseId}
          warehouses={warehouses.data ?? []}
        />
      </div>
    </Dialog>
  );
}

function Sheet({
  countId,
  canCount,
  canApprove,
  onClose,
}: {
  countId: string;
  canCount: boolean;
  canApprove: boolean;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const sheet = useQuery({
    queryKey: ["inventory", "count", countId],
    queryFn: () => api.inventory.count(countId),
  });
  const [entries, setEntries] = useState<Record<string, { counted: string; reason: string }>>({});

  useEffect(() => {
    if (!sheet.data) return;
    setEntries(
      Object.fromEntries(
        sheet.data.lines.map((line) => [
          line.id,
          { counted: line.counted_quantity ?? "", reason: line.reason ?? "" },
        ]),
      ),
    );
  }, [sheet.data]);

  const refresh = (count: StockCountSheet) => {
    client.setQueryData(["inventory", "count", countId], count);
    void client.invalidateQueries({ queryKey: ["inventory"] });
  };

  const save = useMutation({
    mutationFn: () =>
      api.inventory.recordCount(
        countId,
        Object.entries(entries).map(([line_id, entry]) => ({
          line_id,
          counted_quantity: entry.counted === "" ? null : entry.counted,
          reason: entry.reason || null,
        })),
      ),
    onSuccess: refresh,
  });
  const submit = useMutation({
    mutationFn: async () => {
      await save.mutateAsync();
      return api.inventory.submitCount(countId);
    },
    onSuccess: (count) => {
      refresh(count);
      toast.show("success", `${count.number} submitted for approval.`);
    },
  });
  const approve = useMutation({
    mutationFn: () => api.inventory.approveCount(countId),
    onSuccess: (count) => {
      refresh(count);
      toast.show("success", `${count.number} posted.`);
    },
  });

  const count = sheet.data;
  const editable = count?.state === "open" && canCount;
  const failure = save.error ?? submit.error ?? approve.error;

  return (
    <Dialog
      size="lg"
      title={count ? `Count ${count.number ?? ""} · ${count.warehouse}` : "Count sheet"}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Close
          </Button>
          {editable ? (
            <>
              <Button tone="secondary" onClick={() => save.mutate()} busy={save.isPending}>
                Save progress
              </Button>
              <Button
                tone="primary"
                size="transactional"
                onClick={() => submit.mutate()}
                busy={submit.isPending}
              >
                Submit for approval
              </Button>
            </>
          ) : null}
          {count?.state === "submitted" && canApprove ? (
            <Button
              tone="primary"
              size="transactional"
              onClick={() => approve.mutate()}
              busy={approve.isPending}
            >
              Approve and post variances
            </Button>
          ) : null}
        </>
      }
    >
      {!count ? (
        <Skeleton rows={5} />
      ) : (
        <div className="stack gap-4">
          {failure ? <Alert tone="error" title={describe(failure).message} /> : null}
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: "10%" }}>Place</th>
                  <th style={{ width: "12%" }}>Batch</th>
                  <th style={{ width: "10%" }} className="numeric">
                    Recorded
                  </th>
                  <th style={{ width: "14%" }} className="numeric">
                    Counted
                  </th>
                  <th style={{ width: "10%" }} className="numeric">
                    Difference
                  </th>
                  <th style={{ width: "22%" }}>Reason</th>
                </tr>
              </thead>
              <tbody>
                {count.lines.map((line) => {
                  const entry = entries[line.id] ?? { counted: "", reason: "" };
                  return (
                    <tr key={line.id}>
                      <td>
                        <span className="cell-strong">{line.name}</span>
                        <span className="cell-note">{line.sku}</span>
                      </td>
                      <td>{line.location ?? "—"}</td>
                      <td>{line.batch ?? "—"}</td>
                      <td className="numeric">{quantity(line.system_quantity)}</td>
                      <td className="numeric">
                        {editable ? (
                          <TextInput
                            aria-label={`Counted ${line.sku}`}
                            inputMode="decimal"
                            value={entry.counted}
                            onChange={(event) =>
                              setEntries((rows) => ({
                                ...rows,
                                [line.id]: { ...entry, counted: event.target.value },
                              }))
                            }
                          />
                        ) : (
                          quantity(line.counted_quantity)
                        )}
                      </td>
                      <td className="numeric">
                        {line.difference ? quantity(line.difference) : "—"}
                      </td>
                      <td>
                        {editable ? (
                          <TextInput
                            aria-label={`Reason for ${line.sku}`}
                            value={entry.reason}
                            onChange={(event) =>
                              setEntries((rows) => ({
                                ...rows,
                                [line.id]: { ...entry, reason: event.target.value },
                              }))
                            }
                          />
                        ) : (
                          (line.reason ?? "—")
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="caption">
            {STATE[count.state][0]} · prepared {formatDate(count.counted_on)}
          </p>
        </div>
      )}
    </Dialog>
  );
}
