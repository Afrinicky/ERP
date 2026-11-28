/**
 * The financial position at a glance.
 *
 * Every figure is derived from posted journal entries. Where the ledger has nothing
 * to say, the tile says so rather than showing a zero that could be mistaken for a
 * measured result.
 */

import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import {
  Alert,
  Card,
  EmptyState,
  Measure,
  SectionHeader,
  Skeleton,
  StatusChip,
} from '@/components/primitives';
import { isNegative, isZero } from '@/lib/decimal';
import { money } from '@/lib/format';
import { useCurrency, useSession } from '@/lib/session';

export function FinanceOverview() {
  const currency = useCurrency();
  const { can } = useSession();

  const summary = useQuery({
    queryKey: ['ledger', 'summary'],
    queryFn: () => api.financialReports.summary(),
  });
  const variances = useQuery({
    queryKey: ['ledger', 'subledgers'],
    queryFn: () => api.ledger.subledgers(),
  });

  if (summary.isPending) return <Skeleton rows={6} height={64} />;
  if (!summary.data) return <EmptyState title="The ledger is not available." />;

  const figures = summary.data;
  const allControls = variances.data ?? [];
  const disagreeing = allControls.filter((item) => !item.agrees);
  // A control account with nothing on either side tells the reader nothing. Eleven
  // rows of zeros would bury the one row that matters.
  const carrying = allControls.filter(
    (item) => !item.agrees || !isZero(item.control_balance) || !isZero(item.subledger_balance),
  );
  const dormant = allControls.length - carrying.length;
  const outOfBalance = !isZero(figures.trial_balance_difference);

  return (
    <div className="stack gap-6">
      {figures.open_period_label === null ? (
        <Alert tone="warning" title="No accounting period covers today">
          Open the financial year before anything is posted, otherwise a transaction has
          nowhere to land.
        </Alert>
      ) : null}

      {outOfBalance ? (
        <Alert tone="error" title="The trial balance does not sum to zero">
          It is out by {money(figures.trial_balance_difference, currency)}. No period may be
          closed until the difference is found.
        </Alert>
      ) : null}

      {figures.pending_approvals > 0 && can('journal.approve') ? (
        <Alert tone="info" title={`${figures.pending_approvals} entries await a checker`}>
          They affect no balance until approved.
        </Alert>
      ) : null}

      <div className="measure-grid">
        <Measure
          label="Cash and bank"
          value={money(figures.cash_on_hand, currency)}
          period={`As at ${figures.as_at}`}
        />
        <Measure
          label="Receivables"
          value={money(figures.receivables, currency)}
          period="Owed to the business"
        />
        <Measure
          label="Payables"
          value={money(figures.payables, currency)}
          period="Owed by the business"
        />
        <Measure
          label="Result year to date"
          value={money(figures.net_result_year_to_date, currency)}
          period={
            figures.financial_year ? `Financial year ${figures.financial_year}` : 'No year open'
          }
          footer={
            <StatusChip tone={isNegative(figures.net_result_year_to_date) ? 'warning' : 'ok'}>
              {isNegative(figures.net_result_year_to_date) ? 'Loss' : 'Profit'}
            </StatusChip>
          }
        />
      </div>

      <Card title="Position" subtitle="Prepared on the accrual basis.">
        <table className="table table--compact">
          <tbody>
            <tr>
              <td>Assets</td>
              <td className="numeric">{money(figures.total_assets, currency)}</td>
            </tr>
            <tr>
              <td>Liabilities</td>
              <td className="numeric">{money(figures.total_liabilities, currency)}</td>
            </tr>
            <tr>
              <td>Equity, including the result to date</td>
              <td className="numeric">{money(figures.total_equity, currency)}</td>
            </tr>
            <tr>
              <td className="label">Assets less liabilities and equity</td>
              <td className="numeric">
                {money(figures.accounting_equation_difference, currency)}
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <div className="stack gap-3">
        <SectionHeader
          title="Control accounts"
          detail={
            dormant > 0
              ? `Each control account beside the party balances it governs. ${dormant} carry nothing and are not listed.`
              : 'Each control account beside the sum of the party balances it governs.'
          }
        />
        <Card flush>
          {variances.isPending ? (
            <div style={{ padding: 'var(--space-4)' }}>
              <Skeleton rows={3} />
            </div>
          ) : carrying.length === 0 ? (
            <EmptyState
              title="No control account carries a balance."
              detail="Receivables, payables and the other party balances appear here once something is recorded against them."
            />
          ) : (
            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th style={{ width: '10%' }}>Code</th>
                    <th>Account</th>
                    <th style={{ width: '14%' }}>Subledger</th>
                    <th className="numeric" style={{ width: '16%' }}>
                      Control
                    </th>
                    <th className="numeric" style={{ width: '16%' }}>
                      Party balances
                    </th>
                    <th style={{ width: '12%' }}>Agreement</th>
                  </tr>
                </thead>
                <tbody>
                  {carrying.map((item) => (
                    <tr key={item.account_code}>
                      <td className="numeric">{item.account_code}</td>
                      <td className="table__cell--wrap">{item.account_name}</td>
                      <td>{item.subledger}</td>
                      <td className="numeric">{money(item.control_balance, currency)}</td>
                      <td className="numeric">{money(item.subledger_balance, currency)}</td>
                      <td>
                        <StatusChip tone={item.agrees ? 'ok' : 'error'}>
                          {item.agrees ? 'Agrees' : `Out by ${money(item.difference, currency)}`}
                        </StatusChip>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        {disagreeing.length > 0 ? (
          <Alert tone="error" title="A control account disagrees with its subledger">
            A statement of account drawn from the party balances would be wrong. No period
            may be closed until the two agree.
          </Alert>
        ) : null}
      </div>
    </div>
  );
}
