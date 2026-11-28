/**
 * Taxation: what the business is subject to, what it owes, and what it has paid.
 *
 * Four views over one engine. The rules are configuration, so a rate change is a
 * form rather than a release; the registers are derived from the assessments, so a
 * figure on a dashboard and a figure on a return cannot disagree.
 */

import { useState } from 'react';

import { Tabs } from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { useSession } from '@/lib/session';

import { TaxCalculator } from './TaxCalculator';
import { TaxObligations } from './TaxObligations';
import { TaxOverview } from './TaxOverview';
import { TaxRules } from './TaxRules';

type View = 'overview' | 'rules' | 'obligations' | 'calculator';

const VIEWS: { id: View; label: string; detail: string }[] = [
  {
    id: 'overview',
    label: 'Position',
    detail: 'What is owed, what is recoverable, and what falls due next.',
  },
  {
    id: 'rules',
    label: 'Rules',
    detail: 'Every charge the business is subject to, and when each applies.',
  },
  {
    id: 'obligations',
    label: 'Obligations and filings',
    detail: 'Each period’s liability, the payments against it, and the returns lodged.',
  },
  {
    id: 'calculator',
    label: 'Check a transaction',
    detail: 'Ask the engine what a transaction would be charged, without recording it.',
  },
];

export function Taxation() {
  const { can } = useSession();
  const [view, setView] = useState<View>('overview');
  const current = VIEWS.find((item) => item.id === view) ?? VIEWS[0]!;

  return (
    <>
      <ContextBar trail={['Taxation', current.label]} />
      <WorkSurface>
        <PageHeading title="Taxation" detail={current.detail} />
        <Tabs
          items={VIEWS.map(({ id, label }) => ({ id, label }))}
          active={view}
          onChange={setView}
        />
        {view === 'overview' ? <TaxOverview /> : null}
        {view === 'rules' ? <TaxRules canManage={can('tax_rule.manage')} /> : null}
        {view === 'obligations' ? <TaxObligations canPay={can('tax_payment.manage')} /> : null}
        {view === 'calculator' ? <TaxCalculator /> : null}
      </WorkSurface>
    </>
  );
}
