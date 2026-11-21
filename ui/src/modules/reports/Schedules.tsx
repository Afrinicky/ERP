/** Reports generated on a cadence and delivered to the people named. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ReportSchedule, ReportScheduleInput } from "@/api/types";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  Switch,
  TextInput,
  useToast,
} from "@/components/primitives";
import { describe } from "@/lib/errors";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

import { FORMATS } from "./Runner";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function cadence(item: Pick<ReportSchedule, "frequency" | "run_day" | "run_hour">): string {
  const hour = `${String(item.run_hour).padStart(2, "0")}:00`;
  if (item.frequency === "daily") return `Daily at ${hour}, for the day before`;
  if (item.frequency === "weekly") {
    return `${WEEKDAYS[item.run_day - 1] ?? "Monday"}s at ${hour}, for the week before`;
  }
  return `Day ${item.run_day} of each month at ${hour}, for the month before`;
}

export function Schedules() {
  const { can } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const schedules = useQuery({
    queryKey: ["reports", "schedules"],
    queryFn: api.reports.schedules,
  });
  const [editing, setEditing] = useState<ReportSchedule | "new" | null>(null);
  const [removing, setRemoving] = useState<ReportSchedule | null>(null);
  const manage = can("report.export");
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["reports"] });
  const runNow = useMutation({
    mutationFn: (id: string) => api.reports.runSchedule(id),
    onSuccess: (run) => {
      refresh();
      toast.show("success", `${run.title} for ${run.period_label} generated and delivered.`);
    },
    onError: (error) => toast.show("error", describe(error).message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.reports.deleteSchedule(id),
    onSuccess: () => {
      refresh();
      setRemoving(null);
      toast.show("success", "Schedule deleted.");
    },
    onError: (error) => toast.show("error", describe(error).message),
  });

  return (
    <Card
      title="Scheduled reports"
      subtitle="Generated in the background and delivered to each recipient's inbox."
      actions={
        manage ? (
          <Button tone="primary" size="compact" onClick={() => setEditing("new")}>
            New schedule
          </Button>
        ) : null
      }
      flush
    >
      {schedules.isError ? (
        <Alert tone="error" title={describe(schedules.error).message} />
      ) : schedules.isPending ? (
        <Skeleton rows={4} />
      ) : (schedules.data ?? []).length === 0 ? (
        <EmptyState
          title="No report is scheduled."
          detail="The monthly management report, delivered on the first working morning, is a good start."
        />
      ) : (
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th>Schedule</th>
                <th>Runs</th>
                <th>Recipients</th>
                <th>Next</th>
                <th>Last</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {(schedules.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.name}</strong>
                    <div className="cell-note">
                      {item.report_title} · {item.formats.map((f) => f.toUpperCase()).join(", ")}
                    </div>
                  </td>
                  <td className="table__cell--wrap">{cadence(item)}</td>
                  <td className="table__cell--wrap">{item.recipient_names.join(", ")}</td>
                  <td className="nowrap">
                    {item.is_active ? (
                      dateTime(item.next_run_at)
                    ) : (
                      <StatusChip tone="neutral">Paused</StatusChip>
                    )}
                  </td>
                  <td className="nowrap">
                    {item.last_error ? (
                      <StatusChip tone="error">Failed</StatusChip>
                    ) : (
                      dateTime(item.last_run_at)
                    )}
                  </td>
                  <td className="nowrap">
                    {manage ? (
                      <div className="state-row">
                        <Button
                          size="compact"
                          busy={runNow.isPending}
                          onClick={() => runNow.mutate(item.id)}
                        >
                          Run now
                        </Button>
                        <Button tone="plain" size="compact" onClick={() => setEditing(item)}>
                          Edit
                        </Button>
                        <Button tone="plain" size="compact" onClick={() => setRemoving(item)}>
                          Delete
                        </Button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing ? (
        <ScheduleDialog
          schedule={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            refresh();
            setEditing(null);
          }}
        />
      ) : null}
      {removing ? (
        <ConfirmDialog
          title="Delete this schedule?"
          record={removing.name}
          consequence="No further reports are generated from it. Reports already delivered are kept."
          confirmLabel="Delete schedule"
          busy={remove.isPending}
          onConfirm={() => remove.mutate(removing.id)}
          onClose={() => setRemoving(null)}
        />
      ) : null}
    </Card>
  );
}

function ScheduleDialog({
  schedule,
  onClose,
  onSaved,
}: {
  schedule: ReportSchedule | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const catalogue = useQuery({
    queryKey: ["reports", "catalogue"],
    queryFn: api.reports.catalogue,
  });
  const [form, setForm] = useState<ReportScheduleInput>(
    schedule
      ? {
          name: schedule.name,
          report_id: schedule.report_id,
          frequency: schedule.frequency,
          run_day: schedule.run_day,
          run_hour: schedule.run_hour,
          recipients: schedule.recipients,
          formats: schedule.formats,
          parameters: schedule.parameters,
          is_active: schedule.is_active,
        }
      : {
          name: "Monthly management report",
          report_id: "management_pack",
          frequency: "monthly",
          run_day: 1,
          run_hour: 7,
          recipients: [],
          formats: ["pdf"],
          parameters: { granularity: "month", comparison: "prior", filters: {}, basis: "accrual" },
          is_active: true,
        },
  );
  const recipients = useQuery({
    queryKey: ["reports", "recipients", form.report_id],
    queryFn: () => api.reports.recipients(form.report_id),
  });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      schedule ? api.reports.amendSchedule(schedule.id, form) : api.reports.createSchedule(form),
    onSuccess: onSaved,
    onError: (cause) => setError(describe(cause).message),
  });
  const set = (changes: Partial<ReportScheduleInput>) =>
    setForm((current) => ({ ...current, ...changes }));
  const entry = (catalogue.data ?? []).find((item) => item.id === form.report_id);
  const available = (catalogue.data ?? []).filter((item) => item.exportable);

  return (
    <Dialog
      title={schedule ? "Edit schedule" : "New schedule"}
      size="md"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={form.recipients.length === 0 || !form.name.trim()}
            onClick={() => save.mutate()}
          >
            {schedule ? "Save schedule" : "Create schedule"}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error" title={error} /> : null}
        <Field label="Name">
          {(id) => (
            <TextInput
              id={id}
              value={form.name}
              maxLength={120}
              onChange={(event) => set({ name: event.target.value })}
            />
          )}
        </Field>
        <Field label="Report">
          {(id) => (
            <Select
              id={id}
              value={form.report_id}
              onChange={(event) => set({ report_id: event.target.value, recipients: [] })}
            >
              {available.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="form-grid form-grid--three">
          <Field label="Frequency">
            {(id) => (
              <Select
                id={id}
                value={form.frequency}
                onChange={(event) =>
                  set({
                    frequency: event.target.value as ReportScheduleInput["frequency"],
                    run_day: 1,
                  })
                }
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </Select>
            )}
          </Field>
          {form.frequency === "daily" ? null : (
            <Field label={form.frequency === "weekly" ? "Weekday" : "Day of the month"}>
              {(id) => (
                <Select
                  id={id}
                  value={form.run_day}
                  onChange={(event) => set({ run_day: Number(event.target.value) })}
                >
                  {(form.frequency === "weekly"
                    ? WEEKDAYS.map((label, index) => ({ value: index + 1, label }))
                    : Array.from({ length: 28 }, (_, index) => ({
                        value: index + 1,
                        label: String(index + 1),
                      }))
                  ).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          <Field label="Hour">
            {(id) => (
              <Select
                id={id}
                value={form.run_hour}
                onChange={(event) => set({ run_hour: Number(event.target.value) })}
              >
                {Array.from({ length: 24 }, (_, hour) => (
                  <option key={hour} value={hour}>
                    {`${String(hour).padStart(2, "0")}:00`}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        {entry && entry.comparisons.length > 0 ? (
          <Field label="Compare with">
            {(id) => (
              <Select
                id={id}
                value={form.parameters.comparison}
                onChange={(event) =>
                  set({
                    parameters: {
                      ...form.parameters,
                      comparison: event.target
                        .value as ReportScheduleInput["parameters"]["comparison"],
                    },
                  })
                }
              >
                <option value="none">No comparison</option>
                {entry.comparisons.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : null}
        <fieldset className="reports__fieldset">
          <legend>Recipients</legend>
          {recipients.isPending ? (
            <Skeleton rows={2} />
          ) : (recipients.data ?? []).length === 0 ? (
            <p className="caption">No one may read this report.</p>
          ) : (
            (recipients.data ?? []).map((person) => (
              <Checkbox
                key={person.id}
                label={person.name}
                checked={form.recipients.includes(person.id)}
                onChange={(checked) =>
                  set({
                    recipients: checked
                      ? [...form.recipients, person.id]
                      : form.recipients.filter((id) => id !== person.id),
                  })
                }
              />
            ))
          )}
          <p className="caption">Only people who may read the report are offered.</p>
        </fieldset>
        <fieldset className="reports__fieldset">
          <legend>Attached formats</legend>
          <div className="state-row">
            {FORMATS.map((format) => (
              <Checkbox
                key={format.id}
                label={format.label}
                checked={form.formats.includes(format.id)}
                onChange={(checked) =>
                  set({
                    formats: checked
                      ? [...form.formats, format.id]
                      : form.formats.filter((item) => item !== format.id),
                  })
                }
              />
            ))}
          </div>
        </fieldset>
        <Switch
          label="Active"
          checked={form.is_active}
          onChange={(is_active) => set({ is_active })}
        />
      </div>
    </Dialog>
  );
}
