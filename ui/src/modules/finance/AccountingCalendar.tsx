/**
 * The accounting calendar: periods, their standing, and the gates a close depends on.
 *
 * A close is not a flag. It is a record that five reconciliations were run and passed,
 * and the measurements each returned are kept beside it.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { AccountingPeriod } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
  type Tone,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { date as formatDate, dateTime } from '@/lib/format';
import { useSession } from '@/lib/session';

const STATE_LABEL: Record<string, string> = {
  open: 'Open',
  closed: 'Closed',
  reopened: 'Reopened',
};

const STATE_TONE: Record<string, Tone> = {
  open: 'ok',
  closed: 'neutral',
  reopened: 'warning',
};

export function AccountingCalendar() {
  const client = useQueryClient();
  const toast = useToast();
  const { can } = useSession();
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [closing, setClosing] = useState<AccountingPeriod | null>(null);
  const [reopening, setReopening] = useState<AccountingPeriod | null>(null);
  const [yearEnd, setYearEnd] = useState(false);

  const periods = useQuery({
    queryKey: ['ledger', 'periods', year],
    queryFn: () => api.ledger.periods(year),
  });

  const openYear = useMutation({
    mutationFn: () => api.ledger.openYear(year),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show('success', `Financial year ${year} opened.`);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const reopen = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.ledger.reopenPeriod(id, reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show('success', 'Period reopened. The reason is on the audit trail.');
      setReopening(null);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const closeYear = useMutation({
    mutationFn: (note: string) => api.ledger.closeYear(year, note || undefined),
    onSuccess: (outcome) => {
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show(
        'success',
        `Financial year ${year} closed. ${outcome.accounts_closed} accounts carried to reserves.`,
      );
      setYearEnd(false);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const rows = periods.data ?? [];
  const years = [thisYear - 2, thisYear - 1, thisYear, thisYear + 1];

  return (
    <div className="stack gap-5">
      <Card>
        {/* Two controls, not a form: a full grid column stretched a four-digit year
            select across half the page. */}
        <div className="row gap-4" style={{ alignItems: 'flex-end' }}>
          <div style={{ width: '14rem' }}>
            <Field label="Financial year">
              {(id) => (
                <Select
                  id={id}
                  value={String(year)}
                  onChange={(event) => setYear(Number(event.target.value))}
                >
                  {years.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <div className="row gap-2" style={{ paddingBottom: 'var(--space-1)' }}>
            {rows.length === 0 && can('period.close') ? (
              <Button tone="primary" size="compact" busy={openYear.isPending} onClick={() => openYear.mutate()}>
                Open {year}
              </Button>
            ) : null}
            {rows.length > 0 && can('period.close') ? (
              <Button tone="secondary" size="compact" onClick={() => setYearEnd(true)}>
                Close the year
              </Button>
            ) : null}
          </div>
        </div>
      </Card>

      {periods.isPending ? (
        <Card>
          <Skeleton rows={8} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            title={`Financial year ${year} has not been opened.`}
            detail="Nothing can be posted into a year that has no periods."
          />
        </Card>
      ) : (
        <Card title={`Financial year ${year}`} subtitle="Periods close in order, earliest first." flush>
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '6%' }}>No.</th>
                  <th style={{ width: '20%' }}>Period</th>
                  <th style={{ width: '13%' }}>From</th>
                  <th style={{ width: '13%' }}>To</th>
                  <th style={{ width: '14%' }}>Standing</th>
                  <th>Recorded</th>
                  <th style={{ width: '14%' }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((period) => (
                  <tr key={period.id}>
                    <td className="numeric">{period.sequence}</td>
                    <td>
                      {period.label}
                      {period.is_year_end ? <span className="caption"> · year end</span> : null}
                    </td>
                    <td className="numeric">{formatDate(period.starts_on)}</td>
                    <td className="numeric">{formatDate(period.ends_on)}</td>
                    <td>
                      <StatusChip tone={STATE_TONE[period.state] ?? 'neutral'}>
                        {STATE_LABEL[period.state] ?? period.state}
                      </StatusChip>
                    </td>
                    <td className="caption table__cell--wrap">
                      {period.reopened_at
                        ? `Reopened ${dateTime(period.reopened_at)} — ${period.reopen_reason}`
                        : period.closed_at
                          ? `Closed ${dateTime(period.closed_at)}`
                          : ''}
                    </td>
                    <td className="table__actions">
                      {period.state === 'closed' && can('period.reopen') ? (
                        <Button tone="plain" size="compact" onClick={() => setReopening(period)}>
                          Reopen
                        </Button>
                      ) : can('period.close') ? (
                        <Button tone="plain" size="compact" onClick={() => setClosing(period)}>
                          Close
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {closing ? <CloseDialog period={closing} onClose={() => setClosing(null)} /> : null}

      {reopening ? (
        <ConfirmDialog
          title="Reopen a closed period"
          record={reopening.label}
          consequence="Reopening is recorded against your name with the reason you give, and anyone reviewing the ledger afterwards will read it. Give at least ten characters."
          confirmLabel="Reopen the period"
          requireReason
          busy={reopen.isPending}
          onClose={() => setReopening(null)}
          onConfirm={(reason) => reopen.mutate({ id: reopening.id, reason })}
        />
      ) : null}

      {yearEnd ? (
        <ConfirmDialog
          title={`Close financial year ${year}`}
          record={`Financial year ${year}`}
          consequence="Every revenue and expense account is brought to zero against retained earnings, and the final period is closed. The closing entry can be reversed, but the year cannot simply be reopened."
          confirmLabel="Close the year"
          busy={closeYear.isPending}
          onClose={() => setYearEnd(false)}
          onConfirm={(note) => closeYear.mutate(note)}
        />
      ) : null}
    </div>
  );
}

function CloseDialog({ period, onClose }: { period: AccountingPeriod; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState('');

  const readiness = useQuery({
    queryKey: ['ledger', 'readiness', period.id],
    queryFn: () => api.ledger.readiness(period.id),
  });

  const close = useMutation({
    mutationFn: () => api.ledger.closePeriod(period.id, note || undefined),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show('success', `${period.label} closed.`);
      onClose();
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const ready = readiness.data?.ready ?? false;

  return (
    <Dialog
      title={`Close ${period.label}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={close.isPending}
            disabled={!ready}
            onClick={() => close.mutate()}
          >
            Close the period
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <p className="muted">
          A period closes only when every reconciliation below passes. The results are kept
          with the close, so what was checked is on the record.
        </p>

        {readiness.isPending ? (
          <Skeleton rows={5} />
        ) : (
          <div className="stack gap-3">
            {(readiness.data?.checks ?? []).map((check) => (
              <div key={check.name} className="row gap-3" style={{ alignItems: 'flex-start' }}>
                <StatusChip tone={check.passed ? 'ok' : 'error'}>
                  {check.passed ? 'Passed' : 'Blocked'}
                </StatusChip>
                <span className="grow">{check.detail}</span>
              </div>
            ))}
          </div>
        )}

        {!readiness.isPending && !ready ? (
          <Alert tone="warning" title="The period cannot be closed yet">
            Resolve what is blocked above, then try again.
          </Alert>
        ) : null}

        <Field label="Note" optional hint="Kept with the close record.">
          {(id) => (
            <TextInput id={id} value={note} onChange={(event) => setNote(event.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
