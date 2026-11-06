/**
 * Trial balance, profit and loss, balance sheet and cash flow.
 *
 * Each states the basis it was prepared on. An accrual figure and a cash figure
 * answer different questions, and a statement that does not say which it is cannot
 * be relied upon.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { AccountingBasis, StatementSection } from '@/api/types';
import {
  Alert,
  Card,
  EmptyState,
  Field,
  Measure,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
} from '@/components/primitives';
import { isNegative, isZero } from '@/lib/decimal';
import { date as formatDate, money } from '@/lib/format';
import { useCurrency } from '@/lib/session';

type Statement = 'trial' | 'profit' | 'position' | 'cash';

const STATEMENTS: { id: Statement; label: string }[] = [
  { id: 'trial', label: 'Trial balance' },
  { id: 'profit', label: 'Profit and loss' },
  { id: 'position', label: 'Balance sheet' },
  { id: 'cash', label: 'Cash flow' },
];

function startOfYear(): string {
  return `${new Date().getFullYear()}-01-01`;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function FinancialStatements() {
  const [statement, setStatement] = useState<Statement>('trial');
  const [basis, setBasis] = useState<AccountingBasis>('accrual');
  const [start, setStart] = useState(startOfYear);
  const [end, setEnd] = useState(today);

  // A trial balance and a balance sheet are stated at a date; the other two cover a
  // window. Offering a disabled "from" field on the first two only invited the
  // question of why it was there.
  const dated = statement === 'trial' || statement === 'position';

  return (
    <div className="stack gap-5">
      <Card>
        <div className="form-grid">
          <Field label="Statement">
            {(id) => (
              <Select
                id={id}
                value={statement}
                onChange={(event) => setStatement(event.target.value as Statement)}
              >
                {STATEMENTS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {dated ? null : (
            <Field label="From">
              {(id) => (
                <TextInput
                  id={id}
                  type="date"
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              )}
            </Field>
          )}
          <Field label={dated ? 'As at' : 'To'}>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            )}
          </Field>
          <Field label="Basis" hint="Accrual recognises when earned; cash when the money moves.">
            {(id) => (
              <Select
                id={id}
                value={basis}
                disabled={statement === 'cash'}
                onChange={(event) => setBasis(event.target.value as AccountingBasis)}
              >
                <option value="accrual">Accrual</option>
                <option value="cash">Cash</option>
              </Select>
            )}
          </Field>
        </div>
      </Card>

      {statement === 'trial' ? <TrialBalanceView asAt={end} basis={basis} /> : null}
      {statement === 'profit' ? <ProfitView start={start} end={end} basis={basis} /> : null}
      {statement === 'position' ? <PositionView asAt={end} basis={basis} /> : null}
      {statement === 'cash' ? <CashFlowView start={start} end={end} /> : null}

      <p className="caption">
        Every figure above is derived from posted journal entries
        {statement === 'cash' ? '.' : ` on the ${basis} basis.`} A reversed entry remains,
        beside the entry that reversed it.
      </p>
    </div>
  );
}

function BasisChip({ basis }: { basis: AccountingBasis }) {
  return (
    <StatusChip tone="info">{basis === 'accrual' ? 'Accrual basis' : 'Cash basis'}</StatusChip>
  );
}

function TrialBalanceView({ asAt, basis }: { asAt: string; basis: AccountingBasis }) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ['ledger', 'trial-balance', asAt, basis],
    queryFn: () => api.financialReports.trialBalance({ as_at: asAt, basis }),
  });

  if (report.isPending) return <Card><Skeleton rows={10} /></Card>;
  if (!report.data) return <Card><EmptyState title="The trial balance is not available." /></Card>;

  const balance = report.data;
  return (
    <>
      {!balance.balanced ? (
        <Alert tone="error" title="The trial balance does not sum to zero">
          It is out by {money(balance.difference, currency)}. Find the difference before
          relying on any statement drawn from it.
        </Alert>
      ) : null}
      <Card
        title={`Trial balance at ${formatDate(balance.as_at)}`}
        subtitle={`${balance.rows.length} accounts carrying a balance.`}
        actions={<BasisChip basis={balance.basis} />}
        flush
      >
        {balance.rows.length === 0 ? (
          <EmptyState
            title="No account carries a balance."
            detail="The trial balance fills as entries are posted."
          />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '10%' }}>Code</th>
                  <th>Account</th>
                  <th className="numeric" style={{ width: '18%' }}>Debit</th>
                  <th className="numeric" style={{ width: '18%' }}>Credit</th>
                </tr>
              </thead>
              <tbody>
                {balance.rows.map((row) => (
                  <tr key={row.code}>
                    <td className="numeric">{row.code}</td>
                    <td className="table__cell--wrap">{row.name}</td>
                    <td className="numeric">
                      {isZero(row.debit) ? '' : money(row.debit, currency)}
                    </td>
                    <td className="numeric">
                      {isZero(row.credit) ? '' : money(row.credit, currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th colSpan={2}>Total</th>
                  <th className="numeric">{money(balance.total_debit, currency)}</th>
                  <th className="numeric">{money(balance.total_credit, currency)}</th>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function Sections({ sections }: { sections: StatementSection[] }) {
  const currency = useCurrency();
  return (
    <>
      {sections.map((section) => (
        <tbody key={section.code}>
          <tr>
            <th colSpan={2} className="column-header">
              {section.name}
            </th>
          </tr>
          {section.lines.map((line) => (
            <tr key={line.code || line.name}>
              <td className="table__cell--wrap">
                {line.code ? (
                  <span className="numeric" style={{ marginRight: 'var(--space-3)' }}>
                    {line.code}
                  </span>
                ) : null}
                {line.name}
              </td>
              <td className="numeric">{money(line.amount, currency)}</td>
            </tr>
          ))}
          {/* A section of one line already states its total; repeating it adds a row
              and no information. */}
          {section.lines.length > 1 ? (
            <tr>
              <td className="label">{section.name} total</td>
              <td className="numeric">{money(section.total, currency)}</td>
            </tr>
          ) : null}
        </tbody>
      ))}
    </>
  );
}

function ProfitView({
  start,
  end,
  basis,
}: {
  start: string;
  end: string;
  basis: AccountingBasis;
}) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ['ledger', 'profit-and-loss', start, end, basis],
    queryFn: () => api.financialReports.profitAndLoss(start, end, basis),
  });

  if (report.isPending) return <Card><Skeleton rows={10} /></Card>;
  if (!report.data) return <Card><EmptyState title="The statement is not available." /></Card>;
  const statement = report.data;
  const empty = statement.revenue.length === 0 && statement.expenses.length === 0;

  return (
    <div className="stack gap-5">
      <div className="measure-grid">
        <Measure label="Revenue" value={money(statement.total_revenue, currency)} period="For the period" />
        <Measure label="Gross profit" value={money(statement.gross_profit, currency)} period="After cost of sales" />
        <Measure label="Expenses" value={money(statement.total_expenses, currency)} period="For the period" />
        <Measure
          label="Result"
          value={money(statement.net_result, currency)}
          period={`${formatDate(statement.starts_on)} to ${formatDate(statement.ends_on)}`}
          footer={
            <StatusChip tone={isNegative(statement.net_result) ? 'warning' : 'ok'}>
              {isNegative(statement.net_result) ? 'Loss' : 'Profit'}
            </StatusChip>
          }
        />
      </div>

      <Card
        title="Profit and loss account"
        subtitle={`${formatDate(statement.starts_on)} to ${formatDate(statement.ends_on)}`}
        actions={<BasisChip basis={statement.basis} />}
        flush
      >
        {empty ? (
          <EmptyState title="Nothing was traded in this period." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Account</th>
                  <th className="numeric" style={{ width: '22%' }}>Amount</th>
                </tr>
              </thead>
              <Sections sections={statement.revenue} />
              <Sections sections={statement.expenses} />
              <tfoot>
                <tr>
                  <th>Result for the period</th>
                  <th className="numeric">{money(statement.net_result, currency)}</th>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function PositionView({ asAt, basis }: { asAt: string; basis: AccountingBasis }) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ['ledger', 'balance-sheet', asAt, basis],
    queryFn: () => api.financialReports.balanceSheet(asAt, basis),
  });

  if (report.isPending) return <Card><Skeleton rows={10} /></Card>;
  if (!report.data) return <Card><EmptyState title="The statement is not available." /></Card>;
  const statement = report.data;

  return (
    <div className="stack gap-5">
      {!statement.balanced ? (
        <Alert tone="error" title="The balance sheet does not balance">
          Assets exceed liabilities and equity by {money(statement.difference, currency)}.
        </Alert>
      ) : null}

      <div className="measure-grid">
        <Measure label="Assets" value={money(statement.total_assets, currency)} period={`At ${formatDate(statement.as_at)}`} />
        <Measure label="Liabilities" value={money(statement.total_liabilities, currency)} period={`At ${formatDate(statement.as_at)}`} />
        <Measure label="Equity" value={money(statement.total_equity, currency)} period="Including the result to date" />
        <Measure
          label="Result not yet closed"
          value={money(statement.retained_result, currency)}
          period="Carried into equity"
        />
      </div>

      <Card
        title={`Balance sheet at ${formatDate(statement.as_at)}`}
        actions={<BasisChip basis={statement.basis} />}
        flush
      >
        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Account</th>
                <th className="numeric" style={{ width: '22%' }}>Amount</th>
              </tr>
            </thead>
            <Sections sections={statement.assets} />
            <Sections sections={statement.liabilities} />
            <Sections sections={statement.equity} />
            <tfoot>
              <tr>
                <th>Assets</th>
                <th className="numeric">{money(statement.total_assets, currency)}</th>
              </tr>
              <tr>
                <th>Liabilities and equity</th>
                <th className="numeric">
                  {money(statement.total_liabilities_and_equity, currency)}
                </th>
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>
    </div>
  );
}

function CashFlowView({ start, end }: { start: string; end: string }) {
  const currency = useCurrency();
  const report = useQuery({
    queryKey: ['ledger', 'cash-flow', start, end],
    queryFn: () => api.financialReports.cashFlow(start, end),
  });

  if (report.isPending) return <Card><Skeleton rows={8} /></Card>;
  if (!report.data) return <Card><EmptyState title="The statement is not available." /></Card>;
  const statement = report.data;

  const rows: [string, string][] = [
    ['Cash at the start of the period', statement.opening_cash],
    ['Operating activities', statement.operating],
    ['Investing activities', statement.investing],
    ['Financing activities', statement.financing],
  ];
  if (!isZero(statement.unclassified)) {
    rows.push(['Unclassified', statement.unclassified]);
  }

  return (
    <Card
      title={`Cash flow, ${formatDate(statement.starts_on)} to ${formatDate(statement.ends_on)}`}
      subtitle="Each cash movement is classified by what the money was for, not by the account it moved through."
      actions={
        <StatusChip tone={statement.reconciles ? 'ok' : 'error'}>
          {statement.reconciles ? 'Reconciles' : 'Does not reconcile'}
        </StatusChip>
      }
      flush
    >
      <div className="table-frame">
        <table className="table table--compact">
          <tbody>
            {rows.map(([label, amount]) => (
              <tr key={label}>
                <td>{label}</td>
                <td className="numeric">{money(amount, currency)}</td>
              </tr>
            ))}
            <tr>
              <td className="label">Net movement</td>
              <td className="numeric">{money(statement.net_movement, currency)}</td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th>Cash at the end of the period</th>
              <th className="numeric">{money(statement.closing_cash, currency)}</th>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}
