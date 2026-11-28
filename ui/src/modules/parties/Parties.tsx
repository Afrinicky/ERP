/**
 * Customers, suppliers and affiliates.
 *
 * One organisation may hold several roles at once, so the roles are flags on one
 * record rather than three separate registers.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Party } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  SectionHeader,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TextArea,
  TextInput,
  useToast,
} from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { describe, fieldError } from '@/lib/errors';
import { money } from '@/lib/format';
import { useCurrency, useSession } from '@/lib/session';

type Scope = 'all' | 'customer' | 'supplier' | 'affiliate';

const SCOPES: { id: Scope; label: string }[] = [
  { id: 'all', label: 'Everyone' },
  { id: 'customer', label: 'Customers' },
  { id: 'supplier', label: 'Suppliers' },
  { id: 'affiliate', label: 'Affiliates' },
];

const TERM_KINDS: [string, string][] = [
  ['immediate', 'Due on issue'],
  ['net_days', 'Net days from invoice'],
  ['end_of_month', 'Days after end of month'],
  ['day_of_month', 'A nominated day of the following month'],
];

const TAX_TREATMENTS: [string, string][] = [
  ['standard', 'Standard'],
  ['exempt', 'Exempt — requires a certificate'],
  ['zero_rated', 'Zero rated'],
  ['withholding_agent', 'Withholding agent'],
];

const STANDING_LABEL: Record<string, string> = {
  within_limit: 'Within limit',
  approaching_limit: 'Approaching limit',
  over_limit: 'Over limit',
  overdue: 'Overdue',
  on_hold: 'On hold',
  no_credit: 'Cash only',
};

export function Parties() {
  const { can } = useSession();
  const [scope, setScope] = useState<Scope>('all');
  const [term, setTerm] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Party | null>(null);
  const limit = 50;

  const parties = useQuery({
    queryKey: ['parties', scope, term, includeInactive, offset],
    queryFn: () =>
      api.parties.search({
        term: term || undefined,
        kind: scope === 'all' ? undefined : scope,
        include_inactive: includeInactive,
        offset,
        limit,
      }),
  });

  const total = parties.data?.total ?? 0;
  const shown = parties.data?.items.length ?? 0;
  const mayAdd =
    can('customer.manage') || can('supplier.manage') || can('affiliate.manage');

  return (
    <>
      <ContextBar
        trail={['Customers and Suppliers']}
        actions={
          mayAdd ? (
            <Button tone="primary" size="compact" onClick={() => setCreating(true)}>
              Add party
            </Button>
          ) : null
        }
      />
      <WorkSurface>
        <PageHeading
          title="Customers and Suppliers"
          detail="One organisation may be a customer, a supplier and an affiliate at once, so each is held as one record carrying its roles."
        />

        <Tabs
          items={SCOPES}
          active={scope}
          onChange={(value) => {
            setScope(value);
            setOffset(0);
          }}
        />

        <Card>
          <div className="form-grid">
            <Field label="Search" hint="Name, code, tax identifier, telephone or contact.">
              {() => (
                <SearchField
                  label="Search parties"
                  value={term}
                  placeholder="Enter name, code or tax identifier"
                  onChange={(value) => {
                    setTerm(value);
                    setOffset(0);
                  }}
                />
              )}
            </Field>
            <div style={{ alignSelf: 'end', paddingBottom: 'var(--space-2)' }}>
              <Checkbox
                label="Include withdrawn parties"
                checked={includeInactive}
                onChange={(value) => {
                  setIncludeInactive(value);
                  setOffset(0);
                }}
              />
            </div>
          </div>
        </Card>

        <Card
          title={`${total.toLocaleString('en-GB')} ${total === 1 ? 'party' : 'parties'}`}
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
                disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}
              >
                Next
              </Button>
            </>
          }
        >
          {parties.isLoading ? (
            <div style={{ padding: 'var(--space-5)' }}>
              <Skeleton rows={5} height={16} />
            </div>
          ) : shown > 0 ? (
            <div className="table-frame">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: '12%' }}>Code</th>
                    <th>Name</th>
                    <th style={{ width: '18%' }}>Roles</th>
                    <th style={{ width: '16%' }}>Payment terms</th>
                    <th style={{ width: '14%' }} className="numeric">
                      Credit limit
                    </th>
                    <th style={{ width: '12%' }}>Standing</th>
                  </tr>
                </thead>
                <tbody>
                  {parties.data!.items.map((party) => (
                    <tr
                      key={party.id}
                      onClick={() => setSelected(party)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="numeric" style={{ textAlign: 'left' }}>
                        {party.code}
                      </td>
                      <td className="table__cell--wrap">
                        {party.name}
                        {party.party_type === 'individual' ? (
                          <span className="caption"> · individual</span>
                        ) : null}
                      </td>
                      <td>
                        <div className="row gap-1 row--wrap">
                          {party.is_customer ? (
                            <StatusChip tone="info">Customer</StatusChip>
                          ) : null}
                          {party.is_supplier ? (
                            <StatusChip tone="neutral">Supplier</StatusChip>
                          ) : null}
                          {party.is_affiliate ? (
                            <StatusChip tone="accent">Affiliate</StatusChip>
                          ) : null}
                        </div>
                      </td>
                      <td className="muted">{party.payment_terms_label}</td>
                      <td className="numeric">
                        <CreditLimit party={party} />
                      </td>
                      <td>
                        {party.credit_on_hold ? (
                          <StatusChip tone="error">On hold</StatusChip>
                        ) : !party.is_active ? (
                          <StatusChip tone="neutral">Withdrawn</StatusChip>
                        ) : (
                          <StatusChip tone="ok">Active</StatusChip>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title={term ? 'No party matches' : 'No parties recorded'}
              detail={
                term
                  ? 'Adjust the search, or clear it to see everyone.'
                  : 'Add a customer or supplier, or import them under Settings.'
              }
              action={
                mayAdd && !term ? (
                  <Button tone="primary" onClick={() => setCreating(true)}>
                    Add party
                  </Button>
                ) : undefined
              }
            />
          )}
        </Card>

        {creating ? <PartyDialog onClose={() => setCreating(false)} /> : null}
        {selected ? (
          <PartyDetail party={selected} onClose={() => setSelected(null)} />
        ) : null}
      </WorkSurface>
    </>
  );
}

function CreditLimit({ party }: { party: Party }) {
  const currency = useCurrency();
  if (!party.is_customer) return <span className="muted">—</span>;
  if (!party.credit_limit) return <span className="muted">Cash only</span>;
  return <>{money(party.credit_limit, currency)}</>;
}

function PartyDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();

  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [partyType, setPartyType] = useState('organisation');
  const [isCustomer, setIsCustomer] = useState(true);
  const [isSupplier, setIsSupplier] = useState(false);
  const [isAffiliate, setIsAffiliate] = useState(false);
  const [email, setEmail] = useState('');
  const [telephone, setTelephone] = useState('');
  const [taxIdentifier, setTaxIdentifier] = useState('');
  const [taxTreatment, setTaxTreatment] = useState('standard');
  const [exemptionReference, setExemptionReference] = useState('');
  const [termKind, setTermKind] = useState('immediate');
  const [termDays, setTermDays] = useState('0');
  const [creditLimit, setCreditLimit] = useState('');
  const [contactName, setContactName] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<unknown>(null);

  const create = useMutation({
    mutationFn: () =>
      api.parties.create({
        name,
        code: code || null,
        party_type: partyType,
        is_customer: isCustomer,
        is_supplier: isSupplier,
        is_affiliate: isAffiliate,
        email: email || null,
        telephone: telephone || null,
        tax_identifier: taxIdentifier || null,
        tax_treatment: taxTreatment,
        tax_exemption_reference: exemptionReference || null,
        payment_term_kind: termKind,
        payment_term_days: Number(termDays) || 0,
        credit_limit: creditLimit || null,
        notes: notes || null,
        contacts: contactName ? [{ name: contactName, is_primary: true }] : [],
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['parties'] });
      toast.show('success', 'Party added.');
      onClose();
    },
    onError: setError,
  });

  const takesDays = termKind !== 'immediate';
  const ready = name.trim().length > 0 && (isCustomer || isSupplier || isAffiliate);

  return (
    <Dialog
      title="Add party"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose}>
            Cancel
          </Button>
          <Button
            tone="primary"
            busy={create.isPending}
            disabled={!ready}
            onClick={() => create.mutate()}
          >
            Add party
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

        <fieldset className="fieldset">
          <legend className="fieldset__legend">Roles</legend>
          {fieldError(error, 'roles') ? (
            <span className="field__error">{fieldError(error, 'roles')}</span>
          ) : null}
          <div className="form-grid">
            <Checkbox
              label="Customer"
              hint="Buys from the business."
              checked={isCustomer}
              onChange={setIsCustomer}
            />
            <Checkbox
              label="Supplier"
              hint="The business buys from them."
              checked={isSupplier}
              onChange={setIsSupplier}
            />
            <Checkbox
              label="Affiliate"
              hint="Introduces customers and earns commission."
              checked={isAffiliate}
              onChange={setIsAffiliate}
            />
          </div>
        </fieldset>

        <div className="form-grid form-grid--two">
          <Field label="Name" required error={fieldError(error, 'name')}>
            {(id) => (
              <TextInput
                id={id}
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoFocus
              />
            )}
          </Field>
          <Field label="Code" optional hint="Derived from the name when left blank." error={fieldError(error, 'code')}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={code}
                onChange={(event) => setCode(event.target.value.toUpperCase())}
                spellCheck={false}
              />
            )}
          </Field>
          <Field label="Kind">
            {(id) => (
              <Select
                id={id}
                value={partyType}
                onChange={(event) => setPartyType(event.target.value)}
              >
                <option value="organisation">Organisation</option>
                <option value="individual">Individual</option>
              </Select>
            )}
          </Field>
          <Field label="Tax identification number" optional>
            {(id) => (
              <TextInput
                id={id}
                value={taxIdentifier}
                onChange={(event) => setTaxIdentifier(event.target.value)}
              />
            )}
          </Field>
          <Field label="Electronic mail address" optional error={fieldError(error, 'email')}>
            {(id) => (
              <TextInput
                id={id}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <Field label="Telephone" optional error={fieldError(error, 'telephone')}>
            {(id) => (
              <TextInput
                id={id}
                value={telephone}
                onChange={(event) => setTelephone(event.target.value)}
              />
            )}
          </Field>
          <Field label="Primary contact" optional>
            {(id) => (
              <TextInput
                id={id}
                value={contactName}
                onChange={(event) => setContactName(event.target.value)}
              />
            )}
          </Field>
        </div>

        <fieldset className="fieldset">
          <legend className="fieldset__legend">Commercial terms</legend>
          <div className="form-grid form-grid--two">
            <Field label="Payment terms">
              {(id) => (
                <Select
                  id={id}
                  value={termKind}
                  onChange={(event) => setTermKind(event.target.value)}
                >
                  {TERM_KINDS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {takesDays ? (
              <Field
                label={termKind === 'day_of_month' ? 'Day of the month' : 'Days'}
                hint={
                  termKind === 'day_of_month'
                    ? 'Between 1 and 28, so the term falls in every month.'
                    : undefined
                }
                error={fieldError(error, 'days')}
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    numeric
                    value={termDays}
                    onChange={(event) => setTermDays(event.target.value.replace(/\D/g, ''))}
                  />
                )}
              </Field>
            ) : null}
            <Field
              label="Credit limit"
              optional
              hint="Left blank, the account is cash only."
              error={fieldError(error, 'credit_limit')}
            >
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  numeric
                  value={creditLimit}
                  onChange={(event) => setCreditLimit(event.target.value)}
                  placeholder="0.00"
                />
              )}
            </Field>
            <Field label="Tax treatment">
              {(id) => (
                <Select
                  id={id}
                  value={taxTreatment}
                  onChange={(event) => setTaxTreatment(event.target.value)}
                >
                  {TAX_TREATMENTS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {taxTreatment === 'exempt' ? (
              <Field
                label="Exemption certificate"
                required
                full
                hint="An exemption requires supporting evidence."
                error={fieldError(error, 'tax_exemption_reference')}
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    value={exemptionReference}
                    onChange={(event) => setExemptionReference(event.target.value)}
                  />
                )}
              </Field>
            ) : null}
            <Field label="Notes" optional full>
              {(id) => (
                <TextArea
                  id={id}
                  rows={2}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              )}
            </Field>
          </div>
        </fieldset>
      </div>
    </Dialog>
  );
}

function PartyDetail({ party, onClose }: { party: Party; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const currency = useCurrency();
  const { can } = useSession();
  const [holding, setHolding] = useState(false);

  const credit = useQuery({
    queryKey: ['credit', party.id],
    queryFn: () => api.parties.credit(party.id),
    enabled: party.is_customer,
  });

  const setHold = useMutation({
    mutationFn: ({ on_hold, reason }: { on_hold: boolean; reason: string }) =>
      api.parties.setCreditHold(party.id, on_hold, reason),
    onSuccess: (updated) => {
      void client.invalidateQueries({ queryKey: ['parties'] });
      void client.invalidateQueries({ queryKey: ['credit', party.id] });
      setHolding(false);
      toast.show(
        'success',
        updated.credit_on_hold ? 'Account placed on hold.' : 'Hold released.',
      );
      onClose();
    },
    onError: (error) => toast.show('error', describe(error).message),
  });

  return (
    <>
      <Dialog
        title={`${party.code} — ${party.name}`}
        size="lg"
        onClose={onClose}
        footer={
          party.is_customer && can('customer.manage') ? (
            <Button
              tone={party.credit_on_hold ? 'secondary' : 'destructive'}
              onClick={() => setHolding(true)}
            >
              {party.credit_on_hold ? 'Release hold' : 'Place on hold'}
            </Button>
          ) : null
        }
      >
        <div className="stack gap-5">
          <div className="row gap-2 row--wrap">
            {party.is_customer ? <StatusChip tone="info">Customer</StatusChip> : null}
            {party.is_supplier ? <StatusChip tone="neutral">Supplier</StatusChip> : null}
            {party.is_affiliate ? <StatusChip tone="accent">Affiliate</StatusChip> : null}
            {party.credit_on_hold ? <StatusChip tone="error">On hold</StatusChip> : null}
          </div>

          {party.is_customer && credit.data ? (
            <Alert
              tone={
                credit.data.permits_credit_sale
                  ? credit.data.standing === 'approaching_limit'
                    ? 'warning'
                    : 'info'
                  : 'error'
              }
              title={STANDING_LABEL[credit.data.standing] ?? credit.data.standing}
            >
              {credit.data.detail}
              {party.credit_hold_reason ? ` Reason: ${party.credit_hold_reason}` : ''}
            </Alert>
          ) : null}

          <dl className="details">
            <dt>Registered legal name</dt>
            <dd>{party.legal_name ?? <span className="muted">Not recorded</span>}</dd>
            <dt>Tax identification number</dt>
            <dd>{party.tax_identifier ?? <span className="muted">Not recorded</span>}</dd>
            <dt>Tax treatment</dt>
            <dd>
              {party.tax_treatment.replace(/_/g, ' ')}
              {party.tax_exemption_reference
                ? ` · certificate ${party.tax_exemption_reference}`
                : ''}
            </dd>
            <dt>Electronic mail address</dt>
            <dd>{party.email ?? <span className="muted">Not recorded</span>}</dd>
            <dt>Telephone</dt>
            <dd>{party.telephone ?? <span className="muted">Not recorded</span>}</dd>
            <dt>Payment terms</dt>
            <dd>{party.payment_terms_label}</dd>
            <dt>Credit limit</dt>
            <dd className="numeric" style={{ textAlign: 'left' }}>
              {party.credit_limit ? money(party.credit_limit, currency) : 'Cash only'}
            </dd>
            <dt>Notes</dt>
            <dd>{party.notes ?? <span className="muted">None</span>}</dd>
          </dl>

          <div className="stack gap-2">
            <SectionHeader title="Contacts" />
            {party.contacts.length > 0 ? (
              <div
                className="table-frame"
                style={{ border: '1px solid var(--colour-border-default)' }}
              >
                <table className="table table--compact">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th style={{ width: '22%' }}>Position</th>
                      <th style={{ width: '24%' }}>Address</th>
                      <th style={{ width: '18%' }}>Telephone</th>
                    </tr>
                  </thead>
                  <tbody>
                    {party.contacts.map((contact) => (
                      <tr key={contact.id}>
                        <td>
                          {contact.name}
                          {contact.is_primary ? (
                            <span className="caption"> · primary</span>
                          ) : null}
                        </td>
                        <td className="muted">{contact.position ?? '—'}</td>
                        <td className="muted">{contact.email ?? '—'}</td>
                        <td className="muted">{contact.telephone ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No contact recorded" />
            )}
          </div>
        </div>
      </Dialog>

      {holding ? (
        <ConfirmDialog
          title={party.credit_on_hold ? 'Release credit hold' : 'Place account on hold'}
          record={`${party.code} — ${party.name}`}
          consequence={
            party.credit_on_hold
              ? 'Credit sales to this account may proceed again, subject to the limit.'
              : 'Credit sales to this account will be withheld whatever the balance, until the hold is released.'
          }
          confirmLabel={party.credit_on_hold ? 'Release hold' : 'Place on hold'}
          requireReason
          busy={setHold.isPending}
          onConfirm={(reason) =>
            setHold.mutate({ on_hold: !party.credit_on_hold, reason })
          }
          onClose={() => setHolding(false)}
        />
      ) : null}
    </>
  );
}
