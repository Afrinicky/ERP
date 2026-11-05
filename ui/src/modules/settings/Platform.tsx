/** Backup, restore, system health and the audit trail. */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Backup, HealthState } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Field,
  Measure,
  SectionHeader,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
  type Tone,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { bytes, dateTime, humanise, relative } from '@/lib/format';
import { useSession } from '@/lib/session';

const TONE: Record<HealthState, Tone> = {
  ok: 'ok',
  degraded: 'warning',
  failed: 'error',
  unavailable: 'neutral',
};

const STATE_LABEL: Record<HealthState, string> = {
  ok: 'Operational',
  degraded: 'Attention required',
  failed: 'Failed',
  unavailable: 'Unavailable',
};

export function BackupSettings() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const backups = useQuery({ queryKey: ['backups'], queryFn: api.platform.backups });
  const [restoring, setRestoring] = useState<Backup | null>(null);

  const run = useMutation({
    mutationFn: () => api.platform.runBackup(true),
    onSuccess: (record) => {
      void client.invalidateQueries({ queryKey: ['backups'] });
      void client.invalidateQueries({ queryKey: ['health'] });
      toast.show(
        record.status === 'verified' ? 'success' : 'error',
        record.status === 'verified'
          ? `Backup taken and verified — ${bytes(record.size_bytes)}.`
          : `Backup did not verify. ${record.verification_note ?? ''}`,
      );
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const restore = useMutation({
    mutationFn: ({ archive, reason }: { archive: string; reason: string }) =>
      api.platform.restore(archive, reason, true),
    onSuccess: (result) => {
      setRestoring(null);
      toast.show('info', result.detail);
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const verified = backups.data?.filter((item) => item.status === 'verified') ?? [];
  const latest = verified[0];

  if (!can('backup.manage') && !can('health.view')) {
    return <EmptyState title="Backup administration is restricted" />;
  }

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Backup and restore"
        detail="The database and the attachment store are backed up together and verified on completion."
        actions={
          can('backup.manage') ? (
            <Button tone="primary" size="compact" busy={run.isPending} onClick={() => run.mutate()}>
              Take backup now
            </Button>
          ) : null
        }
      />

      {!latest ? (
        <Alert tone="warning" title="No verified backup has been taken">
          Take a backup before the business begins transacting in earnest.
        </Alert>
      ) : null}

      <section className="measure-grid">
        <Measure
          label="Last verified backup"
          value={latest ? relative(latest.started_at) : 'None'}
          period={latest ? dateTime(latest.started_at) : 'Not yet taken'}
          text
        />
        <Measure
          label="Archive size"
          value={latest ? bytes(latest.size_bytes) : '—'}
          period="Most recent"
          text
        />
        <Measure
          label="Archives retained"
          value={String(verified.filter((item) => item.storage_key).length)}
          period="Under the retention policy"
        />
      </section>

      <Card title="Backup history" flush>
        {backups.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={4} height={16} />
          </div>
        ) : backups.data && backups.data.length > 0 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '20%' }}>Taken</th>
                  <th style={{ width: '12%' }}>Occasion</th>
                  <th style={{ width: '14%' }}>Outcome</th>
                  <th style={{ width: '12%' }} className="numeric">
                    Size
                  </th>
                  <th>Verification</th>
                  {can('backup.manage') ? <th style={{ width: '10%' }} /> : null}
                </tr>
              </thead>
              <tbody>
                {backups.data.map((record) => (
                  <tr key={record.id}>
                    <td>{dateTime(record.started_at)}</td>
                    <td className="muted">{humanise(record.trigger)}</td>
                    <td>
                      {record.status === 'verified' ? (
                        <StatusChip tone="ok">Verified</StatusChip>
                      ) : record.status === 'failed' ? (
                        <StatusChip tone="error">Failed</StatusChip>
                      ) : (
                        <StatusChip tone="warning">Unverified</StatusChip>
                      )}
                    </td>
                    <td className="numeric">{bytes(record.size_bytes)}</td>
                    <td className="table__cell--wrap muted">
                      {record.failure_reason ?? record.verification_note ?? '—'}
                    </td>
                    {can('backup.manage') ? (
                      <td>
                        <div className="table__actions">
                          {record.status === 'verified' && record.storage_key ? (
                            <Button
                              tone="plain"
                              size="compact"
                              onClick={() => setRestoring(record)}
                            >
                              Restore
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No backups taken"
            detail="A verified backup is required before the business transacts in earnest."
            action={
              can('backup.manage') ? (
                <Button tone="primary" onClick={() => run.mutate()} busy={run.isPending}>
                  Take backup now
                </Button>
              ) : undefined
            }
          />
        )}
      </Card>

      {restoring ? (
        <ConfirmDialog
          title="Restore from backup"
          record={`${restoring.storage_key} — taken ${dateTime(restoring.started_at)}`}
          consequence="The restore is staged now and applied when the application next starts, while nothing holds the database open. The current database is retained alongside it, but every change made since this backup was taken will be absent from the restored data."
          confirmLabel="Stage restore"
          requireReason
          busy={restore.isPending}
          onConfirm={(reason) =>
            restore.mutate({ archive: restoring.storage_key!, reason })
          }
          onClose={() => setRestoring(null)}
        />
      ) : null}
    </div>
  );
}

export function SystemHealth() {
  const health = useQuery({
    queryKey: ['health', 'detail'],
    queryFn: api.platform.healthDetail,
    refetchInterval: 30_000,
  });

  return (
    <div className="settings__panel">
      <SectionHeader
        title="System health"
        detail="Service, database, schema, storage, network, backup and background work."
        actions={
          health.data ? (
            <StatusChip tone={TONE[health.data.state]}>
              {STATE_LABEL[health.data.state]}
            </StatusChip>
          ) : null
        }
      />

      <Alert tone="info" title="Offline operation is expected">
        In standalone deployment the absence of a network is reported as unavailable, not as a
        failure. Core workflows are unaffected; only features requiring connectivity are
        withheld.
      </Alert>

      <Card flush>
        {health.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={7} height={16} />
          </div>
        ) : health.data ? (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '20%' }}>Check</th>
                  <th style={{ width: '16%' }}>State</th>
                  <th>Detail</th>
                  <th style={{ width: '24%' }}>Measurements</th>
                </tr>
              </thead>
              <tbody>
                {health.data.checks.map((check) => (
                  <tr key={check.name}>
                    <td>{check.name}</td>
                    <td>
                      <StatusChip tone={TONE[check.state]}>{STATE_LABEL[check.state]}</StatusChip>
                    </td>
                    <td className="table__cell--wrap muted">{check.detail}</td>
                    <td className="table__cell--wrap caption">
                      {Object.entries(check.measurements)
                        .map(([key, value]) => `${humanise(key)}: ${String(value)}`)
                        .join(' · ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="The health report could not be read" />
        )}
      </Card>
    </div>
  );
}

export function AuditTrail() {
  const { can } = useSession();
  const [action, setAction] = useState('');
  const [entityType, setEntityType] = useState('');
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const audit = useQuery({
    queryKey: ['audit', action, entityType, offset],
    queryFn: () =>
      api.platform.audit({
        action: action || undefined,
        entity_type: entityType || undefined,
        offset,
        limit,
      }),
    enabled: can('audit.view'),
  });

  if (!can('audit.view')) {
    return (
      <EmptyState
        title="The audit trail is restricted"
        detail="You do not hold the permission to read the audit trail."
      />
    );
  }

  const total = audit.data?.total ?? 0;
  const shown = audit.data?.items.length ?? 0;

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Audit trail"
        detail="Append-only. Every entry records who acted, when, on what, and the values before and after."
      />

      <Card>
        <div className="form-grid">
          <Field label="Action" optional hint="Filter by the recorded action.">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={action}
                placeholder="business_amended"
                onChange={(event) => {
                  setAction(event.target.value.trim());
                  setOffset(0);
                }}
                spellCheck={false}
              />
            )}
          </Field>
          <Field label="Record type" optional>
            {(id) => (
              <Select
                id={id}
                value={entityType}
                onChange={(event) => {
                  setEntityType(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">Every record type</option>
                {['business', 'branch', 'warehouse', 'user', 'role', 'session', 'setting',
                  'number_sequence', 'theme_profile', 'custom_field_definition', 'backup'].map(
                  (item) => (
                    <option key={item} value={item}>
                      {humanise(item)}
                    </option>
                  ),
                )}
              </Select>
            )}
          </Field>
        </div>
      </Card>

      <Card
        title={`${total} ${total === 1 ? 'entry' : 'entries'}`}
        subtitle={shown > 0 ? `Showing ${offset + 1} to ${offset + shown}` : undefined}
        flush
        actions={
          <>
            <Button
              tone="plain"
              size="compact"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Previous
            </Button>
            <Button
              tone="plain"
              size="compact"
              disabled={offset + limit >= total}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </>
        }
      >
        {audit.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={8} height={16} />
          </div>
        ) : shown > 0 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '15%' }}>When</th>
                  <th style={{ width: '12%' }}>Who</th>
                  <th style={{ width: '16%' }}>Action</th>
                  <th style={{ width: '18%' }}>Record</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {audit.data!.items.map((event) => (
                  <tr key={event.id}>
                    <td className="muted">{dateTime(event.occurred_at)}</td>
                    <td>{event.actor_username ?? <span className="muted">System</span>}</td>
                    <td>{humanise(event.action)}</td>
                    <td className="table__cell--wrap">
                      {event.entity_label ?? humanise(event.entity_type)}
                    </td>
                    <td className="table__cell--wrap caption">
                      {event.reason ? <div>Reason: {event.reason}</div> : null}
                      {event.after_values
                        ? Object.entries(event.after_values).map(([key, value]) => (
                            <div key={key}>
                              {humanise(key)}:{' '}
                              {event.before_values && key in event.before_values ? (
                                <>
                                  <span className="muted">
                                    {String(event.before_values[key] ?? '—')}
                                  </span>
                                  {' → '}
                                </>
                              ) : null}
                              {String(value ?? '—')}
                            </div>
                          ))
                        : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No entries match"
            detail="Adjust the filters, or clear them to see the whole trail."
          />
        )}
      </Card>
    </div>
  );
}
