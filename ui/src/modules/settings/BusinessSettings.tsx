/** Business particulars, branches and stock-holding locations. */

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Branch, Business } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  SectionHeader,
  Select,
  Skeleton,
  StatusChip,
  TextInput,
  useToast,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { dateTime } from '@/lib/format';
import { useSession } from '@/lib/session';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export function BusinessSettings() {
  const { can, refresh } = useSession();
  const client = useQueryClient();
  const toast = useToast();

  const business = useQuery({ queryKey: ['business'], queryFn: api.business.get });
  const [form, setForm] = useState<Partial<Business>>({});
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (business.data) setForm({});
  }, [business.data]);

  const amend = useMutation({
    mutationFn: (changes: Partial<Business>) => api.business.amend(changes),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['business'] });
      await refresh();
      setForm({});
      setError(null);
      toast.show('success', 'Business particulars saved.');
    },
    onError: (caught) => {
      setError(caught);
      toast.show('error', describe(caught).message);
    },
  });

  if (business.isLoading) return <Skeleton rows={8} height={36} />;
  if (!business.data) return <EmptyState title="The business could not be read" />;

  const current = business.data;
  const value = <K extends keyof Business>(key: K): Business[K] =>
    (key in form ? form[key] : current[key]) as Business[K];
  const set = <K extends keyof Business>(key: K, next: Business[K]) =>
    setForm((state) => ({ ...state, [key]: next }));
  const dirty = Object.keys(form).length > 0;
  const editable = can('business.manage');

  return (
    <div className="settings__panel">
      <SectionHeader
        title="Business particulars"
        detail="These appear on every document the business issues."
        actions={
          editable ? (
            <>
              <Button tone="plain" disabled={!dirty} onClick={() => setForm({})}>
                Discard changes
              </Button>
              <Button
                tone="primary"
                disabled={!dirty}
                busy={amend.isPending}
                onClick={() => amend.mutate(form)}
              >
                Save particulars
              </Button>
            </>
          ) : null
        }
      />

      {!editable ? (
        <Alert tone="info">
          You hold read access to these particulars. Amendment requires the business
          administration permission.
        </Alert>
      ) : null}

      <Card title="Identity">
        <div className="form-grid">
          <Field label="Business name" required error={fieldError(error, 'name')}>
            {(id) => (
              <TextInput
                id={id}
                value={value('name') ?? ''}
                disabled={!editable}
                onChange={(event) => set('name', event.target.value)}
              />
            )}
          </Field>
          <Field label="Registered legal name" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('legal_name') ?? ''}
                disabled={!editable}
                onChange={(event) => set('legal_name', event.target.value)}
              />
            )}
          </Field>
          <Field label="Trading name" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('trading_name') ?? ''}
                disabled={!editable}
                onChange={(event) => set('trading_name', event.target.value)}
              />
            )}
          </Field>
          <Field label="Tax identification number" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('tax_identifier') ?? ''}
                disabled={!editable}
                onChange={(event) => set('tax_identifier', event.target.value)}
              />
            )}
          </Field>
          <Field label="Registration number" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('registration_number') ?? ''}
                disabled={!editable}
                onChange={(event) => set('registration_number', event.target.value)}
              />
            )}
          </Field>
        </div>
      </Card>

      <Card title="Correspondence">
        <div className="form-grid">
          <Field label="Electronic mail address" optional>
            {(id) => (
              <TextInput
                id={id}
                type="email"
                value={value('email') ?? ''}
                disabled={!editable}
                onChange={(event) => set('email', event.target.value)}
              />
            )}
          </Field>
          <Field label="Telephone" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('telephone') ?? ''}
                disabled={!editable}
                onChange={(event) => set('telephone', event.target.value)}
              />
            )}
          </Field>
          <Field label="Website" optional>
            {(id) => (
              <TextInput
                id={id}
                value={value('website') ?? ''}
                disabled={!editable}
                onChange={(event) => set('website', event.target.value)}
              />
            )}
          </Field>
        </div>
      </Card>

      <Card title="Currency and reporting">
        <div className="form-grid">
          <Field
            label="Base currency"
            hint="All accounting is conducted in this currency and it cannot be changed."
          >
            {(id, describedBy) => (
              <TextInput id={id} aria-describedby={describedBy} value={current.base_currency} disabled />
            )}
          </Field>
          <Field label="Currency symbol">
            {(id) => (
              <TextInput
                id={id}
                value={value('currency_symbol') ?? ''}
                disabled={!editable}
                onChange={(event) => set('currency_symbol', event.target.value)}
              />
            )}
          </Field>
          <Field label="Decimal places" hint="Applied when amounts are presented.">
            {(id, describedBy) => (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={String(value('currency_precision'))}
                disabled={!editable}
                onChange={(event) => set('currency_precision', Number(event.target.value))}
              >
                {[0, 2, 3, 4].map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Time zone">
            {(id) => (
              <TextInput
                id={id}
                value={value('timezone') ?? ''}
                disabled={!editable}
                onChange={(event) => set('timezone', event.target.value)}
              />
            )}
          </Field>
          <Field label="Financial year commences">
            {(id) => (
              <Select
                id={id}
                value={String(value('financial_year_start_month'))}
                disabled={!editable}
                onChange={(event) =>
                  set('financial_year_start_month', Number(event.target.value))
                }
              >
                {MONTHS.map((month, index) => (
                  <option key={month} value={index + 1}>
                    {month}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Configured">
            {(id) => (
              <TextInput id={id} value={dateTime(current.setup_completed_at)} disabled />
            )}
          </Field>
        </div>
      </Card>

      <Locations />
    </div>
  );
}

function Locations() {
  const { can } = useSession();
  const client = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState<'branch' | 'warehouse' | null>(null);

  const branches = useQuery({ queryKey: ['branches'], queryFn: () => api.business.branches(true) });
  const warehouses = useQuery({ queryKey: ['warehouses'], queryFn: api.business.warehouses });

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['branches'] });
    void client.invalidateQueries({ queryKey: ['warehouses'] });
  };

  return (
    <>
      <SectionHeader
        title="Branches and locations"
        detail="Document numbering and stock are held per branch and per location."
        actions={
          can('business.manage') ? (
            <>
              <Button size="compact" onClick={() => setAdding('branch')}>
                Add branch
              </Button>
              <Button size="compact" onClick={() => setAdding('warehouse')}>
                Add location
              </Button>
            </>
          ) : null
        }
      />

      <Card title="Branches" flush>
        {branches.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={3} height={16} />
          </div>
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '14%' }}>Code</th>
                  <th>Name</th>
                  <th style={{ width: '20%' }}>Telephone</th>
                  <th style={{ width: '18%' }}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {(branches.data ?? []).map((branch) => (
                  <tr key={branch.id}>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {branch.code}
                    </td>
                    <td>{branch.name}</td>
                    <td>{branch.telephone ?? <span className="muted">—</span>}</td>
                    <td>
                      {branch.is_primary ? (
                        <StatusChip tone="accent">Primary</StatusChip>
                      ) : branch.is_active ? (
                        <StatusChip tone="ok">Active</StatusChip>
                      ) : (
                        <StatusChip tone="neutral">Inactive</StatusChip>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Stock-holding locations" flush>
        {warehouses.isLoading ? (
          <div style={{ padding: 'var(--space-5)' }}>
            <Skeleton rows={3} height={16} />
          </div>
        ) : (
          <div className="table-frame">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: '14%' }}>Code</th>
                  <th>Name</th>
                  <th style={{ width: '22%' }}>Branch</th>
                  <th style={{ width: '18%' }}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {(warehouses.data ?? []).map((warehouse) => (
                  <tr key={warehouse.id}>
                    <td className="numeric" style={{ textAlign: 'left' }}>
                      {warehouse.code}
                    </td>
                    <td>{warehouse.name}</td>
                    <td>
                      {branches.data?.find((branch) => branch.id === warehouse.branch_fk)?.name ??
                        '—'}
                    </td>
                    <td>
                      {warehouse.is_default ? (
                        <StatusChip tone="accent">Default</StatusChip>
                      ) : (
                        <StatusChip tone="ok">Active</StatusChip>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {adding === 'branch' ? (
        <BranchDialog
          onClose={() => setAdding(null)}
          onSaved={() => {
            invalidate();
            setAdding(null);
            toast.show('success', 'Branch added.');
          }}
        />
      ) : null}

      {adding === 'warehouse' ? (
        <WarehouseDialog
          branches={branches.data ?? []}
          onClose={() => setAdding(null)}
          onSaved={() => {
            invalidate();
            setAdding(null);
            toast.show('success', 'Location added.');
          }}
        />
      ) : null}
    </>
  );
}

function BranchDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [telephone, setTelephone] = useState('');
  const [error, setError] = useState<unknown>(null);

  const create = useMutation({
    mutationFn: () => api.business.createBranch({ code, name, telephone: telephone || null }),
    onSuccess: onSaved,
    onError: setError,
  });

  return (
    <Dialog
      title="Add branch"
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={code.trim().length < 3 || name.trim().length === 0}
            onClick={() => create.mutate()}
          >
            Add branch
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}
        <Field
          label="Code"
          required
          hint="Three to sixteen characters. Appears in document numbers."
          error={fieldError(error, 'code')}
        >
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              value={code}
              onChange={(event) => setCode(event.target.value.toLowerCase())}
              autoFocus
            />
          )}
        </Field>
        <Field label="Name" required error={fieldError(error, 'name')}>
          {(id) => (
            <TextInput id={id} value={name} onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
        <Field label="Telephone" optional>
          {(id) => (
            <TextInput
              id={id}
              value={telephone}
              onChange={(event) => setTelephone(event.target.value)}
            />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function WarehouseDialog({
  branches,
  onClose,
  onSaved,
}: {
  branches: Branch[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [branchId, setBranchId] = useState(branches[0]?.id ?? '');
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<unknown>(null);

  const create = useMutation({
    mutationFn: () => api.business.createWarehouse({ branch_id: branchId, code, name }),
    onSuccess: onSaved,
    onError: setError,
  });

  return (
    <Dialog
      title="Add stock-holding location"
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={!branchId || code.trim().length < 3 || name.trim().length === 0}
            onClick={() => create.mutate()}
          >
            Add location
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}
        <Field label="Branch" required>
          {(id) => (
            <Select id={id} value={branchId} onChange={(event) => setBranchId(event.target.value)}>
              {branches.map((branch) => (
                <option key={branch.id} value={branch.id}>
                  {branch.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Code" required error={fieldError(error, 'code')}>
          {(id) => (
            <TextInput
              id={id}
              value={code}
              onChange={(event) => setCode(event.target.value.toLowerCase())}
              autoFocus
            />
          )}
        </Field>
        <Field label="Name" required error={fieldError(error, 'name')}>
          {(id) => (
            <TextInput id={id} value={name} onChange={(event) => setName(event.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
