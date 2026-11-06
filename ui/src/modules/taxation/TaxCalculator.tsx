/**
 * Ask the engine what a transaction would be charged, without recording anything.
 *
 * It exists because a tax engine is only trustworthy if you can interrogate it. An
 * administrator who has just written a condition needs to see it fire, and a user
 * querying an invoice needs to see which rule produced which figure and why.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { TaxEvaluation, TaxOutcome } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Field,
  Select,
  StatusChip,
  TextInput,
  type Tone,
} from '@/components/primitives';
import { isZero } from '@/lib/decimal';
import { describe } from '@/lib/errors';
import { money } from '@/lib/format';
import { useCurrency } from '@/lib/session';

const OUTCOME_TONE: Record<TaxOutcome, Tone> = {
  applied: 'ok',
  not_applicable: 'neutral',
  exempt: 'info',
  needs_review: 'warning',
  overridden: 'accent',
};

const OUTCOME_LABEL: Record<TaxOutcome, string> = {
  applied: 'Applied',
  not_applicable: 'Not applicable',
  exempt: 'Exempt',
  needs_review: 'Needs review',
  overridden: 'Overridden',
};

/**
 * The reason, without the rule's own name repeated.
 *
 * The detail is written to stand alone — it goes into an audit entry and onto a
 * document, where the rule is not otherwise named. Beside the name in a table it
 * would say it twice.
 */
function withoutName(item: { rule_name: string; detail: string }): string {
  const trimmed = item.detail.startsWith(item.rule_name)
    ? item.detail.slice(item.rule_name.length).trimStart()
    : item.detail;
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

export function TaxCalculator() {
  const currency = useCurrency();
  const [net, setNet] = useState('1000.00');
  const [direction, setDirection] = useState<'sales' | 'purchases'>('sales');
  const [treatment, setTreatment] = useState('standard');
  const [itemClass, setItemClass] = useState('');
  const [taxCode, setTaxCode] = useState('');
  const [agent, setAgent] = useState(false);
  const [evidence, setEvidence] = useState('');
  const [result, setResult] = useState<TaxEvaluation | null>(null);

  const evaluate = useMutation({
    mutationFn: () =>
      api.taxation.evaluate({
        transaction_kind: direction === 'sales' ? 'invoice' : 'supplier_invoice',
        on: new Date().toISOString().slice(0, 10),
        direction,
        party_tax_treatment: treatment,
        party_is_withholding_agent: agent,
        exemption_evidence: evidence || null,
        lines: [
          {
            reference: 'Line 1',
            net,
            item_class: itemClass || null,
            product_tax_code: taxCode || null,
          },
        ],
      }),
    onSuccess: setResult,
  });

  const line = result?.lines[0];

  return (
    <div className="stack gap-5">
      <Card
        title="Check a transaction"
        subtitle="Nothing is recorded. The same engine answers here as answers at posting."
        footer={
          <>
            <div className="grow" />
            <Button tone="primary" busy={evaluate.isPending} onClick={() => evaluate.mutate()}>
              Evaluate
            </Button>
          </>
        }
      >
        <div className="form-grid">
          <Field label="Net amount">
            {(id) => (
              <TextInput
                id={id}
                value={net}
                numeric
                inputMode="decimal"
                onChange={(event) => setNet(event.target.value)}
              />
            )}
          </Field>
          <Field label="Direction">
            {(id) => (
              <Select
                id={id}
                value={direction}
                onChange={(event) =>
                  setDirection(event.target.value as 'sales' | 'purchases')
                }
              >
                <option value="sales">A sale to a customer</option>
                <option value="purchases">A purchase from a supplier</option>
              </Select>
            )}
          </Field>
          <Field label="Party tax treatment">
            {(id) => (
              <Select
                id={id}
                value={treatment}
                onChange={(event) => setTreatment(event.target.value)}
              >
                <option value="standard">Standard</option>
                <option value="exempt">Exempt</option>
                <option value="zero_rated">Zero rated</option>
              </Select>
            )}
          </Field>
          <Field label="Item class" optional hint="Goods and services withhold differently.">
            {(id) => (
              <Select
                id={id}
                value={itemClass}
                onChange={(event) => setItemClass(event.target.value)}
              >
                <option value="">Not stated</option>
                <option value="stocked">Stocked goods</option>
                <option value="non_stock">Non-stock goods</option>
                <option value="service">Service</option>
                <option value="bundle">Bundle</option>
              </Select>
            )}
          </Field>
          <Field label="Product tax code" optional>
            {(id) => (
              <TextInput
                id={id}
                value={taxCode}
                onChange={(event) => setTaxCode(event.target.value)}
              />
            )}
          </Field>
          <Field label="Exemption evidence" optional hint="Required to relieve an exempt party.">
            {(id) => (
              <TextInput
                id={id}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
              />
            )}
          </Field>
          <div style={{ alignSelf: 'end', paddingBottom: 'var(--space-2)' }}>
            <Checkbox
              label="The customer is a withholding agent"
              hint="They deduct tax from what they pay us."
              checked={agent}
              onChange={setAgent}
            />
          </div>
        </div>
      </Card>

      {evaluate.error ? (
        <Alert tone="error">{describe(evaluate.error).message}</Alert>
      ) : null}

      {line ? (
        <>
          {result && result.needs_review > 0 ? (
            <Alert
              tone="warning"
              title={`${result.needs_review} decision(s) cannot be settled`}
            >
              A transaction in this state is blocked from posting unless an authorised
              exception is recorded with a reason.
            </Alert>
          ) : null}

          <Card
            title="What the engine concluded"
            subtitle="Every rule reports, including the ones that charge nothing."
            flush
          >
            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Charge</th>
                    <th style={{ width: '14%' }}>Outcome</th>
                    <th className="numeric" style={{ width: '14%' }}>
                      Base
                    </th>
                    <th className="numeric" style={{ width: '10%' }}>
                      Rate
                    </th>
                    <th className="numeric" style={{ width: '14%' }}>
                      Amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {line.assessments.map((item) => (
                    <tr key={item.rule_code}>
                      <td className="table__cell--wrap">
                        {item.rule_name}
                        <span className="cell-note">{withoutName(item)}</span>
                      </td>
                      <td>
                        <StatusChip tone={OUTCOME_TONE[item.outcome]}>
                          {OUTCOME_LABEL[item.outcome]}
                        </StatusChip>
                      </td>
                      <td className="numeric">
                        {isZero(item.base) ? '' : money(item.base, currency)}
                      </td>
                      <td className="numeric">
                        {isZero(item.rate)
                          ? ''
                          : `${(Number(item.rate) * 100).toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}%`}
                      </td>
                      <td className="numeric">
                        {isZero(item.amount) ? '' : money(item.amount, currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th colSpan={4}>Charged on the invoice</th>
                    <th className="numeric">{money(line.charged, currency)}</th>
                  </tr>
                  {!isZero(line.withheld) ? (
                    <tr>
                      <th colSpan={4}>Withheld at source</th>
                      <th className="numeric">({money(line.withheld, currency)})</th>
                    </tr>
                  ) : null}
                </tfoot>
              </table>
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}
