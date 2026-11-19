/**
 * Application root: provisioning, authentication and routing.
 */

import { useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { Alert, Button, Card, Skeleton, ToastProvider } from '@/components/primitives';
import { Shell } from '@/components/shell';
import { Products } from '@/modules/catalogue/Products';
import { Documents } from '@/modules/documents/Documents';
import { Finance } from '@/modules/finance/Finance';
import { Taxation } from '@/modules/taxation/Taxation';
import { Dashboard } from '@/modules/dashboard/Dashboard';
import { Affiliates } from '@/modules/affiliates/Affiliates';
import { Parties } from '@/modules/parties/Parties';
import { Sales } from '@/modules/sales/Sales';
import { Dispatch } from '@/modules/dispatch/Dispatch';
import { Inventory } from '@/modules/inventory/Inventory';
import { Purchases } from '@/modules/purchases/Purchases';
import { Placeholder } from '@/modules/Placeholder';
import { SetupWizard } from '@/modules/setup/SetupWizard';
import { SignIn } from '@/modules/session/SignIn';
import { Settings } from '@/modules/settings/Settings';
import { SessionProvider, useSession } from '@/lib/session';

export function App() {
  return (
    <ToastProvider>
      <SessionProvider>
        <Entry />
      </SessionProvider>
    </ToastProvider>
  );
}

function Entry() {
  const client = useQueryClient();
  const { status } = useSession();
  const [provisioned, setProvisioned] = useState(false);

  const setup = useQuery({ queryKey: ['setup'], queryFn: api.setup.status, retry: 1 });

  if (setup.isLoading || status === 'resolving') {
    return <Starting />;
  }

  if (setup.isError) {
    return <Unreachable />;
  }

  if (setup.data?.setup_required && !provisioned) {
    return (
      <SetupWizard
        onComplete={() => {
          setProvisioned(true);
          void client.invalidateQueries({ queryKey: ['setup'] });
        }}
      />
    );
  }

  if (status !== 'signed-in') {
    return <SignIn />;
  }

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/settings/*" element={<Settings />} />
        <Route path="/products/*" element={<Products />} />
        <Route path="/parties/*" element={<Parties />} />

        <Route path="/sales/*" element={<Sales />} />

        <Route
          path="/register/*"
          element={
            <Placeholder
              title="Point of Sale"
              phase="Phase 10"
              stage="Operations"
              purpose="A dedicated register for walk-in sales, with shifts and reconciliation."
              depends="The ledger, the tax engine and inventory, through which every sale posts."
              contents={[
                'Product search by name or barcode, listing unavailable items with the reason stated inline rather than hiding them',
                'A line table carrying product, quantity and line total, with the running total set as the largest figure on screen',
                'Type of sale and tender as radio choices rather than menus, so the current selection is visible without opening anything',
                'Cash, Mobile Money, card, transfer, on-account and split tender',
                'Electronic tender held as pending until the provider confirms it',
                'Cashier shift, drawer reconciliation, returns, refunds and voids under authorisation',
              ]}
            />
          }
        />

        <Route path="/purchases/*" element={<Purchases />} />

        <Route path="/inventory/*" element={<Inventory />} />

        <Route path="/dispatch/*" element={<Dispatch />} />

        <Route path="/affiliates/*" element={<Affiliates />} />

        <Route path="/taxation/*" element={<Taxation />} />

        <Route path="/finance/*" element={<Finance />} />

        <Route path="/documents/*" element={<Documents />} />

        <Route
          path="/reports/*"
          element={
            <Placeholder
              title="Reports"
              phase="Phase 14"
              stage="Financial Command"
              purpose="Operational and management reporting, with period close."
              depends="Every preceding phase, since a report presents what they record."
              contents={[
                'The twelve operational reports of the requirements',
                'Monthly management pack with executive summary and statements',
                'Comparison against prior period, budget and year',
                'Export to PDF, Excel and CSV',
                'Month-end and year-end close with reconciliation gates',
              ]}
            />
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}

function Starting() {
  return (
    <div className="centred">
      <div className="centred__panel">
        <div className="brand-lockup">
          <span className="brand-lockup__mark" aria-hidden="true">
            N
          </span>
          <span className="brand-lockup__text">
            <span className="brand-lockup__product">Nickland ERP</span>
            <span className="brand-lockup__edition">Starting</span>
          </span>
        </div>
        <Card>
          <Skeleton rows={3} height={20} />
        </Card>
      </div>
    </div>
  );
}

function Unreachable() {
  return (
    <div className="centred">
      <div className="centred__panel">
        <div className="brand-lockup">
          <span className="brand-lockup__mark" aria-hidden="true">
            N
          </span>
          <span className="brand-lockup__text">
            <span className="brand-lockup__product">Nickland ERP</span>
            <span className="brand-lockup__edition">Service unavailable</span>
          </span>
        </div>
        <Card title="The application service is not responding">
          <div className="stack gap-4">
            <Alert tone="error">
              The local service could not be reached. It may still be starting, or it may have
              stopped.
            </Alert>
            <Button tone="primary" block onClick={() => window.location.reload()}>
              Try again
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
