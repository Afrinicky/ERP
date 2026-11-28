/**
 * The rules: every charge the business is subject to.
 *
 * A rate is changed from a date, never in place. The versions a rule has carried are
 * listed beside it, because the question "what were we charging in March" is asked
 * far more often than "what are we charging now".
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { TaxRule } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  Details,
  Dialog,
  EmptyState,
  Field,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
  type Tone,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { date as formatDate, humanise } from '@/lib/format';

const CATEGORY_TONE: Record<string, Tone> = {
  value_added: 'info',
  levy: 'info',
  withholding: 'warning',
  import_duty: 'neutral',
  excise: 'neutral',
  income_tax: 'accent',
  payroll: 'neutral',
  statutory_fee: 'neutral',
  other: 'neutral',
};

/** A rate is held as a fraction; a reader wants a percentage. */
function percent(rate: string | null): string {
  if (rate === null) return '—';
  const scaled = (Number(rate) * 100).toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return `${scaled}%`;
}

export function TaxRules({ canManage }: { canManage: boolean }) {
  const [withdrawn, setWithdrawn] = useState(false);
  const [raising, setRaising] = useState<TaxRule | null>(null);
  const [withdrawing, setWithdrawing] = useState<TaxRule | null>(null);

  const rules = useQuery({
    queryKey: ['taxation', 'rules', withdrawn],
    queryFn: () => api.taxation.rules(withdrawn),
  });

  return (
    <div className="stack gap-5">
      <Card
        title={`${(rules.data ?? []).length} rules`}
        subtitle="In the order they are calculated. A rule that compounds is charged on the ones before it."
        actions={
          <Button tone="plain" size="compact" onClick={() => setWithdrawn(!withdrawn)}>
            {withdrawn ? 'Hide withdrawn' : 'Include withdrawn'}
          </Button>
        }
        flush
      >
        {rules.isPending ? (
          <div style={{ padding: 'var(--space-4)' }}>
            <Skeleton rows={6} />
          </div>
        ) : (rules.data ?? []).length === 0 ? (
          <EmptyState title="No tax rules are configured." />
        ) : (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th className="numeric" style={{ width: '6%' }}>
                    Order
                  </th>
                  <th>Charge</th>
                  <th style={{ width: '11%' }}>Category</th>
                  <th style={{ width: '10%' }}>Applies to</th>
                  <th className="numeric" style={{ width: '8%' }}>
                    Rate
                  </th>
                  <th style={{ width: '10%' }}>Standing</th>
                  <th style={{ width: '19%' }} />
                </tr>
              </thead>
              <tbody>
                {(rules.data ?? []).map((rule) => (
                  <tr key={rule.id}>
                    <td className="numeric">{rule.cascade_order}</td>
                    <td className="table__cell--wrap">
                      {rule.name}
                      {rule.compounds_on_preceding || rule.conditions.length > 0 ? (
                        <span className="cell-note">
                          {[
                            rule.compounds_on_preceding ? 'Compounds on the charges before it' : null,
                            rule.conditions.length > 0
                              ? `${rule.conditions.length} condition${
                                  rule.conditions.length === 1 ? '' : 's'
                                }`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <StatusChip tone={CATEGORY_TONE[rule.category] ?? 'neutral'}>
                        {humanise(rule.category)}
                      </StatusChip>
                    </td>
                    <td className="caption table__cell--wrap">
                      {[
                        rule.applies_to_sales ? 'Sales' : null,
                        rule.applies_to_purchases ? 'Purchases' : null,
                      ]
                        .filter(Boolean)
                        .join(', ')}
                    </td>
                    <td className="numeric">{percent(rule.current_rate)}</td>
                    <td>
                      {rule.is_active ? (
                        <StatusChip tone="ok">In force</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Withdrawn</StatusChip>
                      )}
                    </td>
                    <td className="table__actions">
                      {canManage && rule.is_active ? (
                        <>
                          <Button tone="plain" size="compact" onClick={() => setRaising(rule)}>
                            Change rate
                          </Button>
                          <Button
                            tone="plain"
                            size="compact"
                            onClick={() => setWithdrawing(rule)}
                          >
                            Withdraw
                          </Button>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {(rules.data ?? []).map((rule) =>
        rule.versions.length > 1 || rule.conditions.length > 0 ? (
          <RuleDetail key={rule.id} rule={rule} />
        ) : null,
      )}

      {raising ? <RateDialog rule={raising} onClose={() => setRaising(null)} /> : null}
      {withdrawing ? (
        <WithdrawDialog rule={withdrawing} onClose={() => setWithdrawing(null)} />
      ) : null}
    </div>
  );
}

function RuleDetail({ rule }: { rule: TaxRule }) {
  return (
    <Card title={rule.name} subtitle={rule.description ?? undefined}>
      <div className="stack gap-4">
        {rule.conditions.length > 0 ? (
          <Details
            rows={rule.conditions.map((condition) => [
              humanise(condition.field),
              `${humanise(condition.operator).toLowerCase()} ${
                condition.value ?? (condition.values ?? []).join(', ')
              }`,
            ])}
          />
        ) : null}
        {rule.versions.length > 1 ? (
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '18%' }}>From</th>
                  <th style={{ width: '18%' }}>To</th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Rate
                  </th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {rule.versions.map((version) => (
                  <tr key={version.id}>
                    <td className="numeric">{formatDate(version.effective_from)}</td>
                    <td className="numeric">
                      {version.effective_to ? formatDate(version.effective_to) : 'In force'}
                    </td>
                    <td className="numeric">{percent(version.rate)}</td>
                    <td className="caption table__cell--wrap">{version.note ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function RateDialog({ rule, onClose }: { rule: TaxRule; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [percentage, setPercentage] = useState(
    rule.current_rate ? String(Number(rule.current_rate) * 100) : '',
  );
  const [from, setFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState('');

  const save = useMutation({
    mutationFn: () =>
      api.taxation.setRate(rule.id, {
        // The form takes a percentage because that is how a rate is published; the
        // engine holds a fraction because that is how it is multiplied.
        rate: String(Number(percentage) / 100),
        effective_from: from,
        note: note || null,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['taxation'] });
      toast.show('success', `${rule.name} changes from ${from}.`);
      onClose();
    },
  });

  return (
    <Dialog
      title={`Change the rate of ${rule.name}`}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={save.isPending}
            disabled={percentage === ''}
            onClick={() => save.mutate()}
          >
            Publish the new rate
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <p className="muted">
          A rate changes from a date. Transactions already posted keep the rate they
          were charged, and the version they were charged under stays readable.
        </p>
        {save.error ? <Alert tone="error">{describe(save.error).message}</Alert> : null}
        <div className="form-grid">
          <Field label="Rate" hint="As a percentage." error={fieldError(save.error, 'rate')}>
            {(id) => (
              <TextInput
                id={id}
                value={percentage}
                numeric
                inputMode="decimal"
                onChange={(event) => setPercentage(event.target.value)}
              />
            )}
          </Field>
          <Field
            label="Effective from"
            error={fieldError(save.error, 'effective_from')}
          >
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              />
            )}
          </Field>
          <div className="form-grid__full">
            <Field label="Note" optional hint="Why the rate changed. Kept with the version.">
              {(id) => (
                <TextInput
                  id={id}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
              )}
            </Field>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

function WithdrawDialog({ rule, onClose }: { rule: TaxRule; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const withdraw = useMutation({
    mutationFn: (reason: string) => api.taxation.withdrawRule(rule.id, reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['taxation'] });
      toast.show('success', `${rule.name} withdrawn.`);
      onClose();
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  return (
    <ConfirmDialog
      title="Withdraw this rule"
      record={`${rule.code} — ${rule.name}`}
      consequence="It stops applying to new transactions. Everything already charged under it stays charged, and the rule stays readable so those charges stay explained."
      confirmLabel="Withdraw the rule"
      requireReason
      busy={withdraw.isPending}
      onClose={onClose}
      onConfirm={(reason) => withdraw.mutate(reason)}
    />
  );
}
