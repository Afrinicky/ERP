/** The taxation position: what is owed, what is overdue, and what is recoverable. */

import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import {
  Alert,
  Card,
  EmptyState,
  Measure,
  Skeleton,
  StatusChip,
} from '@/components/primitives';
import { isZero } from '@/lib/decimal';
import { date as formatDate, money } from '@/lib/format';
import { useCurrency } from '@/lib/session';

export function TaxOverview() {
  const currency = useCurrency();
  const summary = useQuery({
    queryKey: ['taxation', 'summary'],
    queryFn: () => api.taxation.summary(),
  });
  const obligations = useQuery({
    queryKey: ['taxation', 'obligations'],
    queryFn: () => api.taxation.obligations(),
  });

  if (summary.isPending) return <Skeleton rows={6} height={64} />;
  if (!summary.data) return <EmptyState title="The taxation position is not available." />;

  const figures = summary.data;
  const open = (obligations.data ?? []).filter(
    (item) => item.state !== 'paid' && !isZero(item.net_payable),
  );

  return (
    <div className="stack gap-6">
      {figures.obligations_overdue > 0 ? (
        <Alert
          tone="error"
          title={`${figures.obligations_overdue} obligation(s) are past their due date`}
        >
          {money(figures.overdue, currency)} is overdue. An authority charges interest
          from the due date, not from the day the return is filed.
        </Alert>
      ) : null}

      <div className="measure-grid">
        <Measure
          label="Outstanding"
          value={money(figures.outstanding, currency)}
          period={`Across ${figures.obligations_outstanding} obligations`}
        />
        <Measure
          label="Overdue"
          value={money(figures.overdue, currency)}
          period="Past the due date"
          footer={
            figures.obligations_overdue > 0 ? (
              <StatusChip tone="error">Attention required</StatusChip>
            ) : (
              <StatusChip tone="ok">Nothing overdue</StatusChip>
            )
          }
        />
        <Measure
          label="Recoverable"
          value={money(figures.recoverable, currency)}
          period="Input tax and credits"
        />
        <Measure
          label="Paid this year"
          value={money(figures.paid_this_year, currency)}
          period={`As at ${formatDate(figures.as_at)}`}
        />
      </div>

      <Card
        title="Falling due"
        subtitle={`${figures.rules_active} rules in force · ${figures.filings_due} returns outstanding`}
        flush
      >
        {obligations.isPending ? (
          <div style={{ padding: 'var(--space-4)' }}>
            <Skeleton rows={4} />
          </div>
        ) : open.length === 0 ? (
          <EmptyState
            title="Nothing is owed."
            detail="An obligation appears here as soon as a transaction is assessed against a rule."
          />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Obligation</th>
                  <th style={{ width: '13%' }}>Due</th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Assessed
                  </th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Credits
                  </th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Payable
                  </th>
                  <th style={{ width: '13%' }}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {open.map((item) => (
                  <tr key={item.id}>
                    <td className="table__cell--wrap">{item.label}</td>
                    <td className="numeric">{formatDate(item.due_on)}</td>
                    <td className="numeric">{money(item.assessed, currency)}</td>
                    <td className="numeric">
                      {money(
                        String(Number(item.recoverable) + Number(item.withheld)),
                        currency,
                      )}
                    </td>
                    <td className="numeric">{money(item.net_payable, currency)}</td>
                    <td>
                      <StatusChip
                        tone={
                          item.due_on < figures.as_at
                            ? 'error'
                            : item.state === 'part_paid'
                              ? 'warning'
                              : 'neutral'
                        }
                      >
                        {item.due_on < figures.as_at
                          ? 'Overdue'
                          : item.state === 'part_paid'
                            ? 'Part paid'
                            : 'Outstanding'}
                      </StatusChip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
