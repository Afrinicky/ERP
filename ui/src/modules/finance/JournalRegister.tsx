/**
 * The journal register and the posting form.
 *
 * A posted entry is never amended. The register offers reversal, which leaves both
 * the original and the correction on the record where an auditor can see them.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { DraftLine, JournalEntry } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  SearchField,
  Select,
  StatusChip,
  TableSkeleton,
  TextArea,
  TextInput,
  TotalDisplay,
  useToast,
  type Tone,
} from '@/components/primitives';
import { difference, isNegative, isZero, magnitude, sum } from '@/lib/decimal';
import { describe, fieldError } from '@/lib/errors';
import { date as formatDate, money } from '@/lib/format';
import { useCurrency, useSession } from '@/lib/session';

const STATE_LABEL: Record<string, string> = {
  draft: 'Draft',
  pending_approval: 'Awaiting a checker',
  posted: 'Posted',
  reversed: 'Reversed',
  rejected: 'Declined',
};

const STATE_TONE: Record<string, Tone> = {
  draft: 'neutral',
  pending_approval: 'warning',
  posted: 'ok',
  reversed: 'info',
  rejected: 'error',
};

const SOURCE_LABEL: Record<string, string> = {
  manual: 'Manual journal',
  sales: 'Sales',
  point_of_sale: 'Point of sale',
  purchases: 'Purchases',
  inventory: 'Inventory',
  taxation: 'Taxation',
  payment: 'Payment',
  receipt: 'Receipt',
  commission: 'Commission',
  financing: 'Financing',
  opening_balance: 'Opening balance',
  year_end: 'Year end',
  reversal: 'Reversal',
};

export function JournalRegister({
  requested,
  onHandled,
}: {
  requested: boolean;
  onHandled: () => void;
}) {
  const currency = useCurrency();
  const [term, setTerm] = useState('');
  const [state, setState] = useState('');
  const [source, setSource] = useState('');
  const [offset, setOffset] = useState(0);
  const [viewing, setViewing] = useState<JournalEntry | null>(null);

  const limit = 25;
  const register = useQuery({
    queryKey: ['journals', term, state, source, offset],
    queryFn: () =>
      api.journals.list({
        term: term || undefined,
        state: state || undefined,
        source: source || undefined,
        offset,
        limit,
      }),
  });

  const total = register.data?.total ?? 0;
  const shown = register.data?.entries.length ?? 0;

  return (
    <div className="stack gap-5">
      <Card>
        <div className="form-grid">
          <Field label="Search" hint="Number, narration or reference.">
            {() => (
              <SearchField
                label="Search the journal"
                value={term}
                placeholder="Enter a number, narration or reference"
                onChange={(value) => {
                  setTerm(value);
                  setOffset(0);
                }}
              />
            )}
          </Field>
          <Field label="Standing" optional>
            {(id) => (
              <Select
                id={id}
                value={state}
                onChange={(event) => {
                  setState(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">Every standing</option>
                {Object.entries(STATE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Raised by" optional>
            {(id) => (
              <Select
                id={id}
                value={source}
                onChange={(event) => {
                  setSource(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">Everything</option>
                {Object.entries(SOURCE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
      </Card>

      <Card
        title={`${total.toLocaleString('en-GB')} ${total === 1 ? 'entry' : 'entries'}`}
        subtitle={shown > 0 ? `Showing ${offset + 1} to ${offset + shown}` : undefined}
        flush
        actions={
          <>
            <Button
              tone="plain"
              size="compact"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Previous
            </Button>
            <Button
              tone="plain"
              size="compact"
              disabled={offset + shown >= total}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </>
        }
      >
        <div className="table-frame">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '13%' }}>Number</th>
                <th style={{ width: '11%' }}>Date</th>
                <th>Narration</th>
                <th style={{ width: '13%' }}>Raised by</th>
                <th style={{ width: '14%' }}>Standing</th>
                <th className="numeric" style={{ width: '15%' }}>
                  Amount
                </th>
              </tr>
            </thead>
            {register.isPending ? (
              <TableSkeleton columns={6} />
            ) : (
              <tbody>
                {(register.data?.entries ?? []).map((entry) => (
                  <tr
                    key={entry.id}
                    onClick={() => setViewing(entry)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="numeric">{entry.number ?? '—'}</td>
                    <td className="numeric">{formatDate(entry.posting_date)}</td>
                    <td className="table__cell--wrap">{entry.narration}</td>
                    <td className="caption">{SOURCE_LABEL[entry.source] ?? entry.source}</td>
                    <td>
                      <StatusChip tone={STATE_TONE[entry.state] ?? 'neutral'}>
                        {STATE_LABEL[entry.state] ?? entry.state}
                      </StatusChip>
                    </td>
                    <td className="numeric">{money(entry.total_debit, currency)}</td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        </div>
        {!register.isPending && shown === 0 ? (
          <EmptyState
            title="No entry matches."
            detail="Clear the filters, or post the first journal."
          />
        ) : null}
      </Card>

      {requested ? <PostJournalDialog onClose={onHandled} /> : null}
      {viewing ? <EntryDialog entry={viewing} onClose={() => setViewing(null)} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ Posting */

interface FormLine {
  key: string;
  account_code: string;
  side: 'debit' | 'credit';
  amount: string;
  narration: string;
}

function blank(side: 'debit' | 'credit' = 'debit'): FormLine {
  return {
    key: Math.random().toString(36).slice(2),
    account_code: '',
    side,
    amount: '',
    narration: '',
  };
}

function PostJournalDialog({ onClose }: { onClose: () => void }) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();

  const accounts = useQuery({
    queryKey: ['ledger', 'accounts', 'postable'],
    queryFn: () => api.ledger.accounts({ with_balances: false }),
  });
  const postable = useMemo(
    () => (accounts.data ?? []).filter((item) => item.is_active && item.permits_manual_posting),
    [accounts.data],
  );

  const [postingDate, setPostingDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [narration, setNarration] = useState('');
  const [lines, setLines] = useState<FormLine[]>([blank('debit'), blank('credit')]);

  // Summed exactly on the digits, never through a JavaScript number: the whole
  // point of carrying an amount as a string is lost the moment it is parsed as a
  // float, and an entry that balances would be reported as out by a fraction.
  const debits = sum(lines.filter((line) => line.side === 'debit').map((line) => line.amount));
  const credits = sum(lines.filter((line) => line.side === 'credit').map((line) => line.amount));
  const outstanding = difference(debits, credits);
  const balanced = isZero(outstanding) && !isZero(debits);

  const post = useMutation({
    mutationFn: () =>
      api.journals.post({
        posting_date: postingDate,
        narration,
        lines: lines
          .filter((line) => line.account_code && !isZero(line.amount))
          .map<DraftLine>((line) => ({
            account_code: line.account_code,
            side: line.side,
            amount: line.amount,
            narration: line.narration || null,
          })),
      }),
    onSuccess: (outcome) => {
      void client.invalidateQueries({ queryKey: ['journals'] });
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show(
        'success',
        outcome.awaiting_approval
          ? 'The entry exceeds the approval threshold and awaits a checker.'
          : `Entry ${outcome.entry.number} posted.`,
      );
      onClose();
    },
  });

  const update = (key: string, patch: Partial<FormLine>) =>
    setLines((current) =>
      current.map((line) => (line.key === key ? { ...line, ...patch } : line)),
    );

  return (
    <Dialog
      title="Post a journal entry"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <TotalDisplay
            compact
            label={balanced ? 'Balanced' : 'Out of balance'}
            value={money(debits, currency)}
            caption={
              balanced
                ? 'Debits equal credits.'
                : `${isNegative(outstanding) ? 'Credits' : 'Debits'} exceed the other side by ${money(
                    magnitude(outstanding),
                    currency,
                  )}.`
            }
            tone={balanced ? 'settled' : 'due'}
          />
          <div className="grow" />
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={post.isPending}
            disabled={!balanced || !narration.trim()}
            onClick={() => post.mutate()}
          >
            Post entry
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {post.error ? <Alert tone="error">{describe(post.error).message}</Alert> : null}

        <div className="form-grid">
          <Field label="Posting date" error={fieldError(post.error, 'posting_date')}>
            {(id) => (
              <TextInput
                id={id}
                type="date"
                value={postingDate}
                onChange={(event) => setPostingDate(event.target.value)}
              />
            )}
          </Field>
          <div className="form-grid__full">
            <Field
              label="Narration"
              hint="What the entry records, in terms someone reading the ledger next year would follow."
              error={fieldError(post.error, 'narration')}
            >
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={narration}
                  onChange={(event) => setNarration(event.target.value)}
                />
              )}
            </Field>
          </div>
        </div>

        <div className="table-frame" style={{ border: '1px solid var(--colour-border-default)' }}>
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: '32%' }}>Account</th>
                <th style={{ width: '15%' }}>Side</th>
                <th className="numeric" style={{ width: '17%' }}>
                  Amount
                </th>
                <th>Line narration</th>
                <th style={{ width: '10%' }} />
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.key}>
                  <td>
                    <Select
                      value={line.account_code}
                      aria-label="Account"
                      onChange={(event) => update(line.key, { account_code: event.target.value })}
                    >
                      <option value="">Choose an account</option>
                      {postable.map((account) => (
                        <option key={account.id} value={account.code}>
                          {account.code} — {account.name}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <Select
                      value={line.side}
                      aria-label="Side"
                      onChange={(event) =>
                        update(line.key, { side: event.target.value as 'debit' | 'credit' })
                      }
                    >
                      <option value="debit">Debit</option>
                      <option value="credit">Credit</option>
                    </Select>
                  </td>
                  <td>
                    <TextInput
                      value={line.amount}
                      numeric
                      inputMode="decimal"
                      aria-label="Amount"
                      onChange={(event) => update(line.key, { amount: event.target.value })}
                    />
                  </td>
                  <td>
                    <TextInput
                      value={line.narration}
                      aria-label="Line narration"
                      onChange={(event) => update(line.key, { narration: event.target.value })}
                    />
                  </td>
                  <td className="table__actions">
                    <Button
                      tone="plain"
                      size="compact"
                      disabled={lines.length <= 2}
                      onClick={() =>
                        setLines((current) => current.filter((item) => item.key !== line.key))
                      }
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="row gap-2">
          <Button
            tone="secondary"
            size="compact"
            onClick={() => setLines((current) => [...current, blank('debit')])}
          >
            Add a debit
          </Button>
          <Button
            tone="secondary"
            size="compact"
            onClick={() => setLines((current) => [...current, blank('credit')])}
          >
            Add a credit
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ One entry */

function EntryDialog({ entry, onClose }: { entry: JournalEntry; onClose: () => void }) {
  const currency = useCurrency();
  const client = useQueryClient();
  const toast = useToast();
  const { can } = useSession();
  const [reversing, setReversing] = useState(false);
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['journals'] });
    void client.invalidateQueries({ queryKey: ['ledger'] });
  };

  const reverse = useMutation({
    mutationFn: (reason: string) => api.journals.reverse(entry.id, reason),
    onSuccess: (created) => {
      refresh();
      toast.show('success', `Reversed by ${created.number}.`);
      onClose();
    },
    onError: (error) => toast.show('error', describe(error).message),
  });
  const decide = useMutation({
    mutationFn: (reason: string) =>
      decision === 'approve'
        ? api.journals.approve(entry.id, reason || undefined)
        : api.journals.reject(entry.id, reason),
    onSuccess: () => {
      refresh();
      toast.show(
        'success',
        decision === 'approve' ? 'Entry approved and posted.' : 'Entry declined.',
      );
      onClose();
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  const pending = entry.state === 'pending_approval';

  return (
    <Dialog
      title={entry.number ?? 'Journal entry'}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <StatusChip tone={STATE_TONE[entry.state] ?? 'neutral'}>
            {STATE_LABEL[entry.state] ?? entry.state}
          </StatusChip>
          <div className="grow" />
          <Button tone="plain" onClick={onClose}>
            Close
          </Button>
          {pending && can('journal.approve') ? (
            <>
              <Button tone="destructive" onClick={() => setDecision('reject')}>
                Decline
              </Button>
              <Button tone="primary" onClick={() => setDecision('approve')}>
                Approve
              </Button>
            </>
          ) : null}
          {entry.state === 'posted' && can('journal.reverse') ? (
            <Button tone="secondary" onClick={() => setReversing(true)}>
              Reverse
            </Button>
          ) : null}
        </>
      }
    >
      <div className="stack gap-4">
        {entry.state === 'reversed' ? (
          <Alert tone="info" title="This entry has been reversed">
            {entry.reversal_reason}
          </Alert>
        ) : null}
        {entry.state === 'rejected' ? (
          <Alert tone="warning" title="This entry was declined">
            {entry.rejection_reason}
          </Alert>
        ) : null}

        <p>{entry.narration}</p>

        <div className="table-frame">
          <table className="table table--compact">
            <thead>
              <tr>
                <th style={{ width: '10%' }}>Code</th>
                <th>Account</th>
                <th>Line narration</th>
                <th className="numeric" style={{ width: '16%' }}>
                  Debit
                </th>
                <th className="numeric" style={{ width: '16%' }}>
                  Credit
                </th>
              </tr>
            </thead>
            <tbody>
              {entry.lines.map((line) => (
                <tr key={line.id}>
                  <td className="numeric">{line.account_code}</td>
                  <td className="table__cell--wrap">{line.account_name}</td>
                  <td className="table__cell--wrap caption">{line.narration ?? ''}</td>
                  <td className="numeric">
                    {isZero(line.debit) ? '' : money(line.debit, currency)}
                  </td>
                  <td className="numeric">
                    {isZero(line.credit) ? '' : money(line.credit, currency)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th colSpan={3}>Total</th>
                <th className="numeric">{money(entry.total_debit, currency)}</th>
                <th className="numeric">{money(entry.total_credit, currency)}</th>
              </tr>
            </tfoot>
          </table>
        </div>

        <dl className="details">
          <dt>Posting date</dt>
          <dd>{formatDate(entry.posting_date)}</dd>
          <dt>Raised by</dt>
          <dd>{SOURCE_LABEL[entry.source] ?? entry.source}</dd>
          {entry.source_reference ? (
            <>
              <dt>Reference</dt>
              <dd>{entry.source_reference}</dd>
            </>
          ) : null}
        </dl>
      </div>

      {reversing ? (
        <ConfirmDialog
          title="Reverse this entry"
          record={`${entry.number} · ${money(entry.total_debit, currency)}`}
          consequence="A reversing entry mirrors this one. Both stay on the record, so the correction is visible beside what it corrected."
          confirmLabel="Post the reversal"
          requireReason
          busy={reverse.isPending}
          onClose={() => setReversing(false)}
          onConfirm={(reason) => reverse.mutate(reason)}
        />
      ) : null}

      {decision ? (
        <ConfirmDialog
          title={decision === 'approve' ? 'Approve this entry' : 'Decline this entry'}
          record={`${entry.number ?? 'Pending entry'} · ${money(entry.total_debit, currency)}`}
          consequence={
            decision === 'approve'
              ? 'Approving posts the entry. It cannot then be amended, only reversed.'
              : 'A declined entry affects no balance and stays on the record.'
          }
          confirmLabel={decision === 'approve' ? 'Approve and post' : 'Decline'}
          requireReason={decision === 'reject'}
          busy={decide.isPending}
          onClose={() => setDecision(null)}
          onConfirm={(reason) => decide.mutate(reason)}
        />
      ) : null}
    </Dialog>
  );
}
