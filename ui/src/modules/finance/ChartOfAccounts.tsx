/**
 * The chart of accounts.
 *
 * Grouped as the statements group them, in code order within each group, because the
 * chart is read the way a statement is read.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Account, AccountGroup } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { isZero } from '@/lib/decimal';
import { money } from '@/lib/format';
import { useCurrency, useSession } from '@/lib/session';

const KINDS: [string, string][] = [
  ['asset', 'Assets'],
  ['liability', 'Liabilities'],
  ['equity', 'Equity'],
  ['revenue', 'Revenue'],
  ['expense', 'Expenses'],
];

const CASH_FLOW: [string, string][] = [
  ['none', 'Not a cash movement'],
  ['operating', 'Operating'],
  ['investing', 'Investing'],
  ['financing', 'Financing'],
  ['cash', 'Cash or bank — the balance the statement reconciles to'],
];

export function ChartOfAccounts({
  requested,
  onHandled,
}: {
  requested: boolean;
  onHandled: () => void;
}) {
  const currency = useCurrency();
  const { can } = useSession();
  const [term, setTerm] = useState('');
  const [kind, setKind] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);
  const [inspecting, setInspecting] = useState<Account | null>(null);

  const groups = useQuery({ queryKey: ['ledger', 'groups'], queryFn: () => api.ledger.groups() });
  const accounts = useQuery({
    queryKey: ['ledger', 'accounts', term, kind, includeInactive],
    queryFn: () =>
      api.ledger.accounts({ term: term || undefined, kind: kind || undefined, include_inactive: includeInactive }),
  });

  const byGroup = useMemo(() => {
    const index = new Map<string, AccountGroup>();
    for (const group of groups.data ?? []) index.set(group.id, group);
    const buckets = new Map<string, { group: AccountGroup | null; rows: Account[] }>();
    for (const account of accounts.data ?? []) {
      const group = account.group_fk ? (index.get(account.group_fk) ?? null) : null;
      const key = group?.id ?? 'ungrouped';
      if (!buckets.has(key)) buckets.set(key, { group, rows: [] });
      buckets.get(key)!.rows.push(account);
    }
    return [...buckets.values()].sort(
      (a, b) => (a.group?.display_order ?? 999) - (b.group?.display_order ?? 999),
    );
  }, [accounts.data, groups.data]);

  return (
    <div className="stack gap-5">
      <Card>
        <div className="form-grid">
          <Field label="Search" hint="Code or name.">
            {() => (
              <SearchField
                label="Search the chart"
                value={term}
                placeholder="Enter a code or an account name"
                onChange={setTerm}
              />
            )}
          </Field>
          <Field label="Classification" optional>
            {(id) => (
              <Select id={id} value={kind} onChange={(event) => setKind(event.target.value)}>
                <option value="">Every class</option>
                {KINDS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div style={{ alignSelf: 'end', paddingBottom: 'var(--space-2)' }}>
            <Checkbox
              label="Include withdrawn accounts"
              checked={includeInactive}
              onChange={setIncludeInactive}
            />
          </div>
        </div>
      </Card>

      {accounts.isPending ? (
        <Card>
          <Skeleton rows={8} />
        </Card>
      ) : byGroup.length === 0 ? (
        <Card>
          <EmptyState
            title="No account matches."
            detail="Clear the search, or widen the classification."
          />
        </Card>
      ) : (
        byGroup.map(({ group, rows }) => (
          <Card
            key={group?.id ?? 'ungrouped'}
            title={group?.name ?? 'Ungrouped'}
            subtitle={`${rows.length} ${rows.length === 1 ? 'account' : 'accounts'}`}
            flush
          >
            <div className="table-frame">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th style={{ width: '9%' }}>Code</th>
                    <th>Account</th>
                    <th style={{ width: '16%' }}>Cash flow</th>
                    <th style={{ width: '14%' }}>Standing</th>
                    <th className="numeric" style={{ width: '18%' }}>
                      Balance
                    </th>
                    <th style={{ width: '9%' }} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((account) => (
                    <tr key={account.id}>
                      <td className="numeric">{account.code}</td>
                      <td className="table__cell--wrap">
                        {account.name}
                        {account.control_subledger ? (
                          <span className="caption"> · {account.control_subledger} control</span>
                        ) : null}
                      </td>
                      <td className="caption">
                        {CASH_FLOW.find(([value]) => value === account.cash_flow_category)?.[1]
                          ?.split(' — ')[0] ?? account.cash_flow_category}
                      </td>
                      <td>
                        {!account.is_active ? (
                          <StatusChip tone="neutral">Withdrawn</StatusChip>
                        ) : account.permits_manual_posting ? (
                          <StatusChip tone="ok">In use</StatusChip>
                        ) : (
                          <StatusChip tone="info">Subledger only</StatusChip>
                        )}
                      </td>
                      <td className="numeric">
                        {isZero(account.balance) ? (
                          <span className="muted">—</span>
                        ) : (
                          <>
                            {money(account.balance, currency)}
                            <span className="caption">
                              {' '}
                              {account.presented_side === 'debit' ? 'Dr' : 'Cr'}
                            </span>
                          </>
                        )}
                      </td>
                      <td className="table__actions">
                        <Button tone="plain" size="compact" onClick={() => setInspecting(account)}>
                          Ledger
                        </Button>
                        {can('account.manage') ? (
                          <Button tone="plain" size="compact" onClick={() => setEditing(account)}>
                            Amend
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}

      {requested ? <AccountDialog groups={groups.data ?? []} onClose={onHandled} /> : null}
      {editing ? (
        <AccountDialog
          account={editing}
          groups={groups.data ?? []}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {inspecting ? (
        <AccountLedgerDialog account={inspecting} onClose={() => setInspecting(null)} />
      ) : null}
    </div>
  );
}

function AccountDialog({
  account,
  groups,
  onClose,
}: {
  account?: Account;
  groups: AccountGroup[];
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const amending = account !== undefined;
  const [code, setCode] = useState(account?.code ?? '');
  const [name, setName] = useState(account?.name ?? '');
  const [description, setDescription] = useState(account?.description ?? '');
  const [groupId, setGroupId] = useState(account?.group_fk ?? '');
  const [cashFlow, setCashFlow] = useState(account?.cash_flow_category ?? 'none');
  const [manual, setManual] = useState(account?.permits_manual_posting ?? true);
  const [active, setActive] = useState(account?.is_active ?? true);

  const save = useMutation({
    mutationFn: () =>
      amending
        ? api.ledger.amendAccount(account.id, {
            name,
            description: description || null,
            group_id: groupId || null,
            cash_flow_category: cashFlow,
            permits_manual_posting: manual,
            is_active: active,
          })
        : api.ledger.createAccount({
            code,
            name,
            description: description || null,
            group_id: groupId || null,
            cash_flow_category: cashFlow,
            permits_manual_posting: manual,
          }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['ledger'] });
      toast.show('success', amending ? 'Account amended.' : 'Account created.');
      onClose();
    },
  });

  return (
    <Dialog
      title={amending ? `Amend ${account.code}` : 'Add an account'}
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button tone="primary" busy={save.isPending} onClick={() => save.mutate()}>
            {amending ? 'Save changes' : 'Create account'}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {save.error ? <Alert tone="error">{describe(save.error).message}</Alert> : null}

        <div className="form-grid">
          <Field
            label="Code"
            hint="Four to eight digits. The leading digit sets the class: 1 asset, 2 liability, 3 equity, 4 revenue, 5 to 8 expense."
            error={fieldError(save.error, 'code')}
          >
            {(id) => (
              <TextInput
                id={id}
                value={code}
                disabled={amending}
                inputMode="numeric"
                onChange={(event) => setCode(event.target.value)}
              />
            )}
          </Field>
          <Field label="Name" error={fieldError(save.error, 'name')}>
            {(id) => (
              <TextInput id={id} value={name} onChange={(event) => setName(event.target.value)} />
            )}
          </Field>
          <Field label="Statement group" optional>
            {(id) => (
              <Select id={id} value={groupId} onChange={(event) => setGroupId(event.target.value)}>
                <option value="">Ungrouped</option>
                {groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Cash-flow classification" hint="Where a movement against this account appears on the cash-flow statement.">
            {(id) => (
              <Select id={id} value={cashFlow} onChange={(event) => setCashFlow(event.target.value)}>
                {CASH_FLOW.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="form-grid__full">
            <Field label="Description" optional>
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              )}
            </Field>
          </div>
          <div className="form-grid__full stack gap-2">
            <Checkbox
              label="Accepts a manual journal"
              hint="A control account is written by its subledger, not by hand."
              checked={manual}
              onChange={setManual}
            />
            {amending ? (
              <Checkbox
                label="In use"
                hint="A withdrawn account accepts nothing new. Its history remains readable."
                checked={active}
                onChange={setActive}
              />
            ) : null}
          </div>
        </div>
      </div>
    </Dialog>
  );
}

function AccountLedgerDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const currency = useCurrency();
  const ledger = useQuery({
    queryKey: ['ledger', 'account-ledger', account.id],
    queryFn: () => api.financialReports.accountLedger(account.id, { limit: 200 }),
  });

  return (
    <Dialog title={`${account.code} — ${account.name}`} size="lg" onClose={onClose}>
      {ledger.isPending ? (
        <Skeleton rows={8} />
      ) : !ledger.data || ledger.data.movements.length === 0 ? (
        <EmptyState
          title="Nothing has been posted to this account."
          detail="It will show every entry against it, with a running balance."
        />
      ) : (
        <div className="stack gap-4">
          <div className="table-frame">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th style={{ width: '12%' }}>Date</th>
                  <th style={{ width: '14%' }}>Entry</th>
                  <th>Narration</th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Debit
                  </th>
                  <th className="numeric" style={{ width: '14%' }}>
                    Credit
                  </th>
                  <th className="numeric" style={{ width: '16%' }}>
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody>
                {ledger.data.movements.map((movement, index) => (
                  <tr key={`${movement.entry_id}-${index}`}>
                    <td className="numeric">{movement.posting_date}</td>
                    <td className="numeric">{movement.number ?? '—'}</td>
                    <td className="table__cell--wrap">
                      {movement.line_narration ?? movement.narration}
                      {movement.state === 'reversed' ? (
                        <span className="caption"> · reversed</span>
                      ) : null}
                    </td>
                    <td className="numeric">
                      {isZero(movement.debit) ? '' : money(movement.debit, currency)}
                    </td>
                    <td className="numeric">
                      {isZero(movement.credit) ? '' : money(movement.credit, currency)}
                    </td>
                    <td className="numeric">{money(movement.running_balance, currency)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th colSpan={3}>Closing balance</th>
                  <th className="numeric">{money(ledger.data.total_debit, currency)}</th>
                  <th className="numeric">{money(ledger.data.total_credit, currency)}</th>
                  <th className="numeric">
                    {money(ledger.data.closing_balance, currency)}{' '}
                    {ledger.data.presented_side === 'debit' ? 'Dr' : 'Cr'}
                  </th>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}
    </Dialog>
  );
}
