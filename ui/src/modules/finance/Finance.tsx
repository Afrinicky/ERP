/**
 * Financial Management.
 *
 * Five views over one ledger: the position, the chart, the journal, the statements
 * and the accounting calendar. Each reads from the same posted entries, so no figure
 * here is maintained separately from the record it summarises.
 */

import { useState } from 'react';

import { Button, Tabs } from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { useSession } from '@/lib/session';

import { ChartOfAccounts } from './ChartOfAccounts';
import { FinanceOverview } from './FinanceOverview';
import { FinancialStatements } from './FinancialStatements';
import { JournalRegister } from './JournalRegister';
import { AccountingCalendar } from './AccountingCalendar';

type View = 'overview' | 'accounts' | 'journal' | 'statements' | 'calendar';

const VIEWS: { id: View; label: string; permission?: string }[] = [
  { id: 'overview', label: 'Position' },
  { id: 'accounts', label: 'Chart of accounts', permission: 'account.view' },
  { id: 'journal', label: 'Journal', permission: 'journal.view' },
  { id: 'statements', label: 'Statements', permission: 'report.view' },
  { id: 'calendar', label: 'Accounting calendar', permission: 'account.view' },
];

const DETAIL: Record<View, string> = {
  overview: 'The position as the ledger records it, and anything awaiting attention.',
  accounts: 'Every account, its classification and the balance it carries.',
  journal: 'Every entry posted, by whom and against what. Corrections are reversals.',
  statements: 'Trial balance, profit and loss, balance sheet and cash flow.',
  calendar: 'Periods, their standing, and the reconciliations a close depends upon.',
};

export function Finance() {
  const { can } = useSession();
  const [view, setView] = useState<View>('overview');
  // The view's primary action lives in the context bar, where every other module
  // puts it, rather than inside the filter card where it competed with the filters
  // and never quite aligned with them. The request is passed down and consumed.
  const [request, setRequest] = useState(false);
  const available = VIEWS.filter((item) => !item.permission || can(item.permission));

  const action =
    view === 'journal' && can('journal.post')
      ? 'Post a journal'
      : view === 'accounts' && can('account.manage')
        ? 'Add an account'
        : null;

  const show = (next: View) => {
    setRequest(false);
    setView(next);
  };

  return (
    <>
      <ContextBar
        trail={['Financial Management', labelFor(view)]}
        actions={
          action ? (
            <Button tone="primary" size="compact" onClick={() => setRequest(true)}>
              {action}
            </Button>
          ) : undefined
        }
      />

      <WorkSurface>
        <PageHeading title="Financial Management" detail={DETAIL[view]} />

        <Tabs
          items={available.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={show}
        />

        {view === 'overview' ? <FinanceOverview /> : null}
        {view === 'accounts' ? (
          <ChartOfAccounts requested={request} onHandled={() => setRequest(false)} />
        ) : null}
        {view === 'journal' ? (
          <JournalRegister requested={request} onHandled={() => setRequest(false)} />
        ) : null}
        {view === 'statements' ? <FinancialStatements /> : null}
        {view === 'calendar' ? <AccountingCalendar /> : null}
      </WorkSurface>
    </>
  );
}

function labelFor(view: View): string {
  return VIEWS.find((item) => item.id === view)?.label ?? 'Position';
}
