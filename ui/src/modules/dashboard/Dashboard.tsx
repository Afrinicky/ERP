/**
 * Dashboard.
 *
 * Phase 1 has no transactional data, so this reports configuration and platform state
 * rather than displaying placeholder figures. Commercial and financial measures arrive
 * with the phases that produce them.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api } from '@/api/client';
import type { HealthState } from '@/api/types';
import {
  Alert,
  Card,
  EmptyState,
  Measure,
  SectionHeader,
  Skeleton,
  StatusChip,
  type Tone,
} from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { addressAs, bytes, dateTime, humanise, relative } from '@/lib/format';
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

export function Dashboard() {
  const { actor, business, can } = useSession();

  const health = useQuery({
    queryKey: ['health', 'detail'],
    queryFn: api.platform.healthDetail,
    enabled: can('health.view'),
    refetchInterval: 60_000,
  });

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => api.users.list(),
    enabled: can('user.view'),
  });

  const branches = useQuery({
    queryKey: ['branches'],
    queryFn: () => api.business.branches(),
    enabled: can('business.view'),
  });

  const backups = useQuery({
    queryKey: ['backups'],
    queryFn: api.platform.backups,
    enabled: can('health.view'),
  });

  const latestBackup = backups.data?.find((item) => item.status === 'verified');
  const activeUsers = users.data?.filter((item) => item.is_active).length ?? 0;

  return (
    <>
      <ContextBar
        trail={['Dashboard']}
        actions={
          can('business.view') ? (
            <Link to="/settings" className="button button--secondary button--compact">
              Settings
            </Link>
          ) : null
        }
      />
      <WorkSurface>
        <PageHeading
          title={`Good day, ${addressAs(actor?.full_name)}`}
          detail={`${business?.name ?? ''} · configuration and platform state.`}
        />

        <Alert tone="info" title="Foundation phase">
          The platform, identity, audit, appearance and backup capabilities are in place.
          Commercial measures appear once the catalogue, quotation and financial phases are
          delivered. No figures are shown here that the system has not yet recorded.
        </Alert>

        <section className="measure-grid">
          <Measure
            label="Branches"
            value={String(branches.data?.length ?? 0)}
            period="Configured"
          />
          <Measure
            label="Active users"
            value={String(activeUsers)}
            period={`${users.data?.length ?? 0} in total`}
          />
          <Measure
            label="Base currency"
            value={business?.base_currency ?? '—'}
            period={business?.currency_symbol}
            text
          />
          <Measure
            label="Last verified backup"
            value={latestBackup ? relative(latestBackup.started_at) : 'None'}
            period={latestBackup ? bytes(latestBackup.size_bytes) : 'Take one before transacting'}
            text
          />
        </section>

        {can('health.view') ? (
          <div className="stack gap-4">
            <SectionHeader
              title="Platform state"
              detail="Service, database, storage, network, backup and background work."
              actions={
                health.data ? (
                  <StatusChip tone={TONE[health.data.state]}>
                    {STATE_LABEL[health.data.state]}
                  </StatusChip>
                ) : null
              }
            />
            <Card flush>
              {health.isLoading ? (
                <div style={{ padding: 'var(--space-5)' }}>
                  <Skeleton rows={6} height={16} />
                </div>
              ) : health.data ? (
                <div className="table-frame">
                  <table className="table table--compact">
                    <thead>
                      <tr>
                        <th style={{ width: '22%' }}>Check</th>
                        <th style={{ width: '14%' }}>State</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {health.data.checks.map((check) => (
                        <tr key={check.name}>
                          <td>{check.name}</td>
                          <td>
                            <StatusChip tone={TONE[check.state]}>
                              {STATE_LABEL[check.state]}
                            </StatusChip>
                          </td>
                          <td className="table__cell--wrap muted">{check.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="Platform state is not available"
                  detail="The health report could not be read."
                />
              )}
            </Card>
          </div>
        ) : null}

        {can('audit.view') ? <RecentActivity /> : null}
      </WorkSurface>
    </>
  );
}

function RecentActivity() {
  const audit = useQuery({
    queryKey: ['audit', 'recent'],
    queryFn: () => api.platform.audit({ limit: 12 }),
  });

  return (
    <div className="stack gap-4">
      <SectionHeader
        title="Recent activity"
        detail="Drawn from the audit trail, which is append-only."
        actions={
          <Link to="/settings/audit" className="button button--plain button--compact">
            View the full trail
          </Link>
        }
      />
      <Card flush>
        {audit.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={5} height={16} />
          </div>
        ) : audit.data && audit.data.items.length > 0 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>When</th>
                  <th style={{ width: '16%' }}>Who</th>
                  <th style={{ width: '24%' }}>Action</th>
                  <th>Record</th>
                </tr>
              </thead>
              <tbody>
                {audit.data.items.map((event) => (
                  <tr key={event.id}>
                    <td className="muted">{dateTime(event.occurred_at)}</td>
                    <td>{event.actor_username ?? <span className="muted">System</span>}</td>
                    <td>{humanise(event.action)}</td>
                    <td className="table__cell--wrap">
                      {event.entity_label ?? humanise(event.entity_type)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No activity recorded"
            detail="Entries appear here as records are created and amended."
          />
        )}
      </Card>
    </div>
  );
}
