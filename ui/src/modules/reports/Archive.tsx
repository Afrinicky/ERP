/** Saved runs and the report inbox: reports as they were generated, with their notes. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ReportRunSummary } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Skeleton,
  StatusChip,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

import { ReportView } from "./ReportView";
import { FORMATS, fileName, saveFile } from "./Runner";

export function RunList({
  runs,
  onOpen,
  unread,
}: {
  runs: (ReportRunSummary & { read_at?: string | null; delivered_at?: string })[];
  onOpen: (id: string) => void;
  unread?: boolean;
}) {
  return (
    <div className="table-frame">
      <table className="table">
        <thead>
          <tr>
            <th>Report</th>
            <th>Period</th>
            <th>{unread ? "Delivered" : "Generated"}</th>
            <th>By</th>
            <th>Standing</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <button type="button" className="text-button" onClick={() => onOpen(run.id)}>
                  {run.title}
                </button>
                {unread && !run.read_at ? <span className="reports__unread">New</span> : null}
              </td>
              <td className="nowrap">{run.period_label}</td>
              <td className="nowrap">{dateTime(run.delivered_at ?? run.generated_at)}</td>
              <td>{run.generated_by ?? "—"}</td>
              <td>
                <div className="state-row">
                  {run.report_id === "management_pack" ? (
                    <StatusChip tone={run.reconciled ? "ok" : "error"}>
                      {run.reconciled ? "Reconciled" : "Does not reconcile"}
                    </StatusChip>
                  ) : null}
                  {run.scheduled ? <StatusChip tone="info">Scheduled</StatusChip> : null}
                  {run.notes ? (
                    <StatusChip tone="neutral">
                      {run.notes} {run.notes === 1 ? "note" : "notes"}
                    </StatusChip>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SavedRun({ runId, onBack }: { runId: string; onBack: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useSession();
  const run = useQuery({
    queryKey: ["reports", "saved", runId],
    queryFn: () => api.reports.savedRun(runId),
  });
  const [confirming, setConfirming] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: () => api.reports.removeRun(runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reports"] });
      toast.show("success", "Saved report removed.");
      onBack();
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const note = async (section: string, text: string | null) => {
    const updated = await api.reports.annotate(runId, section, text);
    queryClient.setQueryData(["reports", "saved", runId], updated);
    void queryClient.invalidateQueries({ queryKey: ["reports", "runs"] });
  };

  if (run.isError) return <Alert tone="error" title={describe(run.error).message} />;
  if (run.isPending) return <Skeleton rows={10} />;
  const data = run.data;
  const exportable = can("report.export");

  return (
    <div className="stack gap-4">
      <div className="reports__actions reports__actions--split">
        <Button tone="plain" size="compact" onClick={onBack}>
          Back to the list
        </Button>
        <div className="state-row">
          {exportable
            ? FORMATS.map((format) => (
                <Button
                  key={format.id}
                  size="compact"
                  busy={exporting === format.id}
                  onClick={async () => {
                    setExporting(format.id);
                    try {
                      saveFile(
                        await api.reports.exportRun(runId, format.id),
                        fileName(data.report, format.id),
                      );
                    } catch (error) {
                      toast.show("error", describe(error).message);
                    } finally {
                      setExporting(null);
                    }
                  }}
                >
                  {format.label}
                </Button>
              ))
            : null}
          {exportable ? (
            <Button tone="destructive" size="compact" onClick={() => setConfirming(true)}>
              Remove
            </Button>
          ) : null}
        </div>
      </div>
      <Alert tone="info">
        Kept as generated on {dateTime(data.generated_at)}. The figures do not change; notes may be
        added beneath any section.
      </Alert>
      <ReportView
        report={data.report}
        notes={data.section_notes}
        onNote={exportable ? note : undefined}
      />
      {confirming ? (
        <ConfirmDialog
          title="Remove this saved report?"
          record={`${data.title}, ${data.period_label}`}
          consequence="The saved figures and their notes are removed. The ledger is not affected."
          confirmLabel="Remove"
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onClose={() => setConfirming(false)}
        />
      ) : null}
    </div>
  );
}

export function Saved() {
  const [open, setOpen] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["reports", "runs"], queryFn: () => api.reports.runs() });
  if (open) return <SavedRun runId={open} onBack={() => setOpen(null)} />;
  return (
    <Card title="Saved reports" subtitle="Each kept exactly as it was generated." flush>
      {runs.isError ? (
        <Alert tone="error" title={describe(runs.error).message} />
      ) : runs.isPending ? (
        <Skeleton rows={5} />
      ) : (runs.data ?? []).length === 0 ? (
        <EmptyState
          title="No report has been saved yet."
          detail="Run a report and choose Save this run."
        />
      ) : (
        <RunList runs={runs.data ?? []} onOpen={setOpen} />
      )}
    </Card>
  );
}

export function Inbox() {
  const [open, setOpen] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const items = useQuery({ queryKey: ["reports", "inbox"], queryFn: api.reports.inbox });
  if (open) {
    return (
      <SavedRun
        runId={open}
        onBack={() => {
          setOpen(null);
          void queryClient.invalidateQueries({ queryKey: ["reports", "inbox"] });
        }}
      />
    );
  }
  return (
    <Card title="Delivered to you" subtitle="Scheduled reports, as they were generated." flush>
      {items.isError ? (
        <Alert tone="error" title={describe(items.error).message} />
      ) : items.isPending ? (
        <Skeleton rows={5} />
      ) : (items.data ?? []).length === 0 ? (
        <EmptyState title="Nothing has been delivered to you." />
      ) : (
        <RunList runs={items.data ?? []} onOpen={setOpen} unread />
      )}
    </Card>
  );
}
