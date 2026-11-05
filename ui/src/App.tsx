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
import { Dashboard } from '@/modules/dashboard/Dashboard';
import { Parties } from '@/modules/parties/Parties';
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

        <Route
          path="/sales/*"
          element={
            <Placeholder
              title="Sales"
              phase="Phase 5"
              stage="Commercial Core"
              purpose="Requests for quotation, quotations, pro formas, sales orders, invoices and returns."
              depends="The accounting ledger and the tax engine, which govern how a sale is priced and posted."
              contents={[
                'Capture a customer request with its requested lines and due date',
                'Record supplier quotations and compare price, availability and lead time',
                'Build the cost up through allocation, margin, commission and financing',
                'Issue a versioned quotation and record the customer’s acceptance',
                'Convert an accepted quotation to a sales order without re-entry',
              ]}
            />
          }
        />

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

        <Route
          path="/purchases/*"
          element={
            <Placeholder
              title="Purchases"
              phase="Phase 7"
              stage="Commercial Core"
              purpose="Supplier requests, comparisons, purchase orders, invoices and payments."
              depends="The quotation and costing engine, from which procurement need arises."
              contents={[
                'Raise supplier purchase orders from an accepted sales order',
                'Fulfil one customer order from several suppliers',
                'Match supplier invoice against purchase order and goods received note',
                'Approve quantity and price variance',
                'Report cost variance between quoted and actual procurement cost',
              ]}
            />
          }
        />

        <Route
          path="/inventory/*"
          element={
            <Placeholder
              title="Inventory"
              phase="Phase 8"
              stage="Operations"
              purpose="Stock, receiving, inspection, assembly, batches, expiry, transfers and counts."
              depends="The catalogue and procurement, which supply what is received."
              contents={[
                'Append-only stock ledger with reversals rather than amendments',
                'On hand, available, reserved, quarantined, incoming and in transit',
                'Goods receipt, inspection and quarantine of rejected stock',
                'Moving weighted average valuation over a layer-capable ledger',
                'Assembly workspace and stock counts with variance approval',
              ]}
            />
          }
        />

        <Route
          path="/dispatch/*"
          element={
            <Placeholder
              title="Dispatch"
              phase="Phase 9"
              stage="Operations"
              purpose="Picking, packing, waybills, delivery and proof of receipt."
              depends="Inventory and the document designer."
              contents={[
                'Pick lists and packing lists from assembled orders',
                'Waybills and delivery notes, several per order where needed',
                'Carrier, own vehicle, courier and customer collection',
                'Signed or scanned proof of delivery, preserved unaltered',
                'Failed, short and damaged delivery, with their consequences posted',
              ]}
            />
          }
        />

        <Route
          path="/affiliates/*"
          element={
            <Placeholder
              title="Affiliates"
              phase="Phase 5"
              stage="Commercial Core"
              purpose="Referrers, eligibility, rates, accruals and payouts."
              depends="The costing and pricing engine, which computes commission."
              contents={[
                'Referrer profiles and payment details',
                'Eligibility by product, category or selected lines',
                'Fixed or percentage commission with effective-dated rates',
                'Accrued, approved, payable and paid states',
                'Commission payable only once the customer has paid',
              ]}
            />
          }
        />

        <Route
          path="/taxation/*"
          element={
            <Placeholder
              title="Taxation"
              phase="Phase 4"
              stage="Fiscal Core"
              purpose="Tax rules, obligations, registers, filings and reconciliation."
              depends="The accounting ledger, to which every tax amount posts."
              contents={[
                'Configurable rules with effective dates and applicability conditions',
                'Applied, Not applicable, Exempt, Needs review and Overridden results',
                'Outstanding, paid, withheld and recoverable registers',
                'Import duty, port and clearing charges in landed cost',
                'Returns, filings and reconciliation of assessed against paid',
              ]}
            />
          }
        />

        <Route
          path="/finance/*"
          element={
            <Placeholder
              title="Financial Management"
              phase="Phases 3 and 11 to 14"
              stage="Fiscal Core and Financial Command"
              purpose="Ledger, treasury, receivables, payables, debt, budgets and reporting."
              depends="Nothing. The accounting ledger is the next phase to be built."
              contents={[
                'Double-entry ledger with a configurable chart of accounts',
                'Periods, locking and audited reopening',
                'Cash, bank and Mobile Money treasury with reconciliation',
                'Receivables and payables with ageing and allocation',
                'Debt, loans and amortisation schedules',
                'Budgets, cash forecasts and the monthly management pack',
              ]}
            />
          }
        />

        <Route
          path="/documents/*"
          element={
            <Placeholder
              title="Documents"
              phase="Phase 6"
              stage="Commercial Core"
              purpose="Generated and uploaded documents, templates and the visual designer."
              depends="The quotation and costing engine, which supplies what documents present."
              contents={[
                'Visual designer for every document type, without developer intervention',
                'Field palette grouped by business, party, transaction, product and tax',
                'Conditional visibility and constrained calculated fields',
                'Internal and customer-facing templates held separately',
                'Issued documents reproducible after a template is amended',
              ]}
            />
          }
        />

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
