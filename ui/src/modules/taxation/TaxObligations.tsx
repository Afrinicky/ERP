/** Obligations, the payments against them, and the returns lodged. */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { TaxObligation } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
  type Tone,
} from '@/components/primitives';
import { isZero } from '@/lib/decimal';
import { describe, fieldError } from '@/lib/errors';
import { date as formatDate, humanise, money } from '@/lib/format';
import { useCurrency } from '@/lib/session';

const STATE_TONE: Record<string, Tone> = {
  outstanding: 'warning',
  part_paid: 'info',
  paid: 'ok',
  written_off: 'neutral',
};

const FILING_TONE: Record<string, Tone> = {
  due: 'warning',
  prepared: 'info',
  submitted: 'ok',
  accepted: 'ok',
  rejected: 'error',
};

export function TaxObligations({ canPay }: { canPay: boolean }) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [paying, setPaying] = useState<TaxObligation | null>(null);

  const obligations = useQuery({
    queryKey: ['taxation', 'obligations'],
    queryFn: () => api.taxation.obligations(),
  });
  const filings = useQuery({
    queryKey: ['taxation', 'filings'],
    queryFn: () => api.taxation.filings(),
  });
  const payments = useQuery({
    queryKey: ['taxation', 'payments'],
    queryFn: () => api.taxation.payments(),
  });

  const openFiling = useMutation({
    mutationFn: (obligationId: string) => api.taxation.openFiling(obligationId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['taxation'] });
      toast.show('success', 'Return opened.');
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const rows = obligations.data ?? [];

  return (
    <div className="stack gap-5">
      <Card
        title={`${rows.length} obligations`}
        subtitle="Each period’s liability, derived from the assessments that make it up."
        flush
      >
        {obligations.isPending ? (
          <div style={{ padding: 'var(--space-4)' }}>
            <Skeleton rows={5} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No obligation has arisen."
            detail="One opens the first time a transaction is assessed against a rule."
          />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Obligation</th>
                  <th style={{ width: '11%' }}>Due</th>
                  <th className="numeric" style={{ width: '12%' }}>
                    Assessed
                  </th>
                  <th className="numeric" style={{ width: '12%' }}>
                    Recoverable
                  </th>
                  <th className="numeric" style={{ width: '11%' }}>
                    Withheld
                  </th>
                  <th className="numeric" style={{ width: '11%' }}>
                    Paid
                  </th>
                  <th className="numeric" style={{ width: '12%' }}>
                    Payable
                  </th>
                  <th style={{ width: '18%' }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td className="table__cell--wrap">{item.label}</td>
                    <td className="numeric">{formatDate(item.due_on)}</td>
                    <td className="numeric">{money(item.assessed, currency)}</td>
                    <td className="numeric">
                      {isZero(item.recoverable) ? '' : money(item.recoverable, currency)}
                    </td>
                    <td className="numeric">
                      {isZero(item.withheld) ? '' : money(item.withheld, currency)}
                    </td>
                    <td className="numeric">
                      {isZero(item.paid) ? '' : money(item.paid, currency)}
                    </td>
                    <td className="numeric">{money(item.net_payable, currency)}</td>
                    <td className="table__actions">
                      <StatusChip tone={STATE_TONE[item.state] ?? 'neutral'}>
                        {humanise(item.state)}
                      </StatusChip>
                      {canPay && !isZero(item.net_payable) ? (
                        <Button tone="plain" size="compact" onClick={() => setPaying(item)}>
                          Pay
                        </Button>
                      ) : null}
                      <Button
                        tone="plain"
                        size="compact"
                        onClick={() => openFiling.mutate(item.id)}
                      >
                        Return
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Returns and filings" flush>
        {(filings.data ?? []).length === 0 ? (
          <EmptyState title="No return has been opened." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>Reference</th>
                  <th style={{ width: '13%' }}>Due</th>
                  <th className="numeric" style={{ width: '16%' }}>
                    Declared
                  </th>
                  <th style={{ width: '14%' }}>Standing</th>
                  <th>Acknowledgement</th>
                </tr>
              </thead>
              <tbody>
                {(filings.data ?? []).map((filing) => (
                  <tr key={filing.id}>
                    <td className="numeric">{filing.reference ?? '—'}</td>
                    <td className="numeric">{formatDate(filing.due_on)}</td>
                    <td className="numeric">{money(filing.declared_amount, currency)}</td>
                    <td>
                      <StatusChip tone={FILING_TONE[filing.state] ?? 'neutral'}>
                        {humanise(filing.state)}
                      </StatusChip>
                    </td>
                    <td className="caption">{filing.acknowledgement_reference ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Payments" flush>
        {(payments.data ?? []).length === 0 ? (
          <EmptyState title="No payment has been recorded." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '20%' }}>Reference</th>
                  <th style={{ width: '14%' }}>Paid</th>
                  <th className="numeric" style={{ width: '16%' }}>
                    Amount
                  </th>
                  <th className="numeric" style={{ width: '16%' }}>
                    Allocated
                  </th>
                  <th>Method</th>
                </tr>
              </thead>
              <tbody>
                {(payments.data ?? []).map((payment) => (
                  <tr key={payment.id}>
                    <td className="numeric">{payment.reference}</td>
                    <td className="numeric">{formatDate(payment.paid_on)}</td>
                    <td className="numeric">{money(payment.amount, currency)}</td>
                    <td className="numeric">
                      {money(payment.allocated, currency)}
                      {payment.allocated !== payment.amount ? (
                        <span className="caption"> · remainder unapplied</span>
                      ) : null}
                    </td>
                    <td className="caption">{payment.method ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {paying ? <PayDialog obligation={paying} onClose={() => setPaying(null)} /> : null}
    </div>
  );
}

function PayDialog({
  obligation,
  onClose,
}: {
  obligation: TaxObligation;
  onClose: () => void;
}) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const [reference, setReference] = useState('');
  const [amount, setAmount] = useState(obligation.net_payable);
  const [paidOn, setPaidOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [bank, setBank] = useState('');

  const pay = useMutation({
    mutationFn: () =>
      api.taxation.recordPayment({
        reference,
        paid_on: paidOn,
        amount,
        bank_reference: bank || null,
        allocations: [{ obligation_id: obligation.id, amount }],
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['taxation'] });
      toast.show('success', `Payment ${reference} recorded.`);
      onClose();
    },
  });

  return (
    <Dialog
      title="Record a payment"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={pay.isPending}
            disabled={!reference.trim() || isZero(amount)}
            onClick={() => pay.mutate()}
          >
            Record the payment
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <p className="muted">
          {obligation.label} · {money(obligation.net_payable, currency)} payable.
        </p>
        {pay.error ? <Alert tone="error">{describe(pay.error).message}</Alert> : null}
        <div className="form-grid">
          <Field
            label="Reference"
            hint="The authority’s receipt or our own."
            error={fieldError(pay.error, 'reference')}
          >
            {(id) => (
              <TextInput
                id={id}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
              />
            )}
          </Field>
          <Field label="Paid on">
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={paidOn}
                onChange={(event) => setPaidOn(event.target.value)}
              />
            )}
          </Field>
          <Field label="Amount" error={fieldError(pay.error, 'amount')}>
            {(id) => (
              <TextInput
                id={id}
                value={amount}
                numeric
                inputMode="decimal"
                onChange={(event) => setAmount(event.target.value)}
              />
            )}
          </Field>
          <Field label="Bank reference" optional>
            {(id) => (
              <TextInput id={id} value={bank} onChange={(event) => setBank(event.target.value)} />
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
