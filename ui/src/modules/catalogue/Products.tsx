/**
 * The product catalogue.
 *
 * Classification governs what a product may do: only a stocked item carries a balance,
 * and only a stocked item may be tracked by batch or serial, per PRD 6.1.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Attribute, Category, Product, Unit } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  SectionHeader,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
  TextArea,
  TextInput,
  useToast,
  type Tone,
} from '@/components/primitives';
import { ContextBar, PageHeading, WorkSurface } from '@/components/shell';
import { describe, fieldError } from '@/lib/errors';
import { money, quantity } from '@/lib/format';
import { useCurrency, useSession } from '@/lib/session';

const CLASS_LABEL: Record<Product['item_class'], string> = {
  stocked: 'Stocked',
  non_stock: 'Procured on demand',
  service: 'Service',
  bundle: 'Bundle',
};

const CLASS_TONE: Record<Product['item_class'], Tone> = {
  stocked: 'ok',
  non_stock: 'info',
  service: 'neutral',
  bundle: 'accent',
};

const CLASS_HINT: Record<Product['item_class'], string> = {
  stocked: 'Held in inventory. Carries a stock balance and a valuation.',
  non_stock: 'Bought against a customer order. Never carries a stock balance.',
  service: 'Labour or another intangible. Never carries a stock balance.',
  bundle: 'A kit. Its components carry the balances, not the kit itself.',
};

const TRACKING_LABEL: Record<string, string> = {
  none: 'None',
  batch: 'Batch or lot',
  serial: 'Serial',
};

export function Products() {
  const { can } = useSession();
  const currency = useCurrency();

  const [term, setTerm] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [itemClass, setItemClass] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Product | null>(null);
  const limit = 50;

  const units = useQuery({ queryKey: ['units'], queryFn: () => api.catalogue.units() });
  const categories = useQuery({
    queryKey: ['categories'],
    queryFn: () => api.catalogue.categories(),
  });

  const products = useQuery({
    queryKey: ['products', term, categoryId, itemClass, includeInactive, offset],
    queryFn: () =>
      api.catalogue.search({
        term: term || undefined,
        category_id: categoryId || undefined,
        item_class: itemClass || undefined,
        include_inactive: includeInactive,
        offset,
        limit,
      }),
  });

  const unitName = useMemo(() => {
    const index = new Map((units.data ?? []).map((unit) => [unit.id, unit.code]));
    return (id: string | null) => (id ? (index.get(id) ?? '—') : '—');
  }, [units.data]);

  const categoryName = useMemo(() => {
    const index = new Map((categories.data ?? []).map((item) => [item.id, item.name]));
    return (id: string | null) => (id ? (index.get(id) ?? '—') : '—');
  }, [categories.data]);

  const total = products.data?.total ?? 0;
  const shown = products.data?.items.length ?? 0;

  return (
    <>
      <ContextBar
        trail={['Products']}
        actions={
          can('product.manage') ? (
            <Button tone="primary" size="compact" onClick={() => setCreating(true)}>
              Add product
            </Button>
          ) : null
        }
      />
      <WorkSurface>
        <PageHeading
          title="Products"
          detail="Products and services, their classification, units, barcodes and attributes."
        />

        <Card>
          <div className="form-grid">
            <Field label="Search" hint="SKU, name, barcode, manufacturer or model.">
              {() => (
                <SearchField
                  label="Search the catalogue"
                  value={term}
                  placeholder="Enter name, SKU or barcode"
                  onChange={(value) => {
                    setTerm(value);
                    setOffset(0);
                  }}
                />
              )}
            </Field>
            <Field label="Category" optional>
              {(id) => (
                <Select
                  id={id}
                  value={categoryId}
                  onChange={(event) => {
                    setCategoryId(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">Every category</option>
                  {(categories.data ?? []).map((category) => (
                    <option key={category.id} value={category.id}>
                      {'— '.repeat(category.depth)}
                      {category.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Classification" optional>
              {(id) => (
                <Select
                  id={id}
                  value={itemClass}
                  onChange={(event) => {
                    setItemClass(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">Every classification</option>
                  {Object.entries(CLASS_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <div style={{ alignSelf: 'end', paddingBottom: 'var(--space-2)' }}>
              <Checkbox
                label="Include withdrawn products"
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
          title={`${total.toLocaleString('en-GB')} ${total === 1 ? 'product' : 'products'}`}
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
          {products.isLoading ? (
            <div style={{ padding: 'var(--space-5)' }}>
              <Skeleton rows={6} height={16} />
            </div>
          ) : shown > 0 ? (
            <div className="table-frame">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: '14%' }}>SKU</th>
                    <th>Name</th>
                    <th style={{ width: '14%' }}>Classification</th>
                    <th style={{ width: '12%' }}>Category</th>
                    <th style={{ width: '8%' }}>Unit</th>
                    <th style={{ width: '10%' }}>Tracking</th>
                    <th style={{ width: '12%' }} className="numeric">
                      List price
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {products.data!.items.map((product) => (
                    <tr
                      key={product.id}
                      onClick={() => setSelected(product)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="numeric" style={{ textAlign: 'left' }}>
                        {product.sku}
                      </td>
                      <td className="table__cell--wrap">
                        {product.name}
                        {product.is_variant_parent ? (
                          <span className="caption"> · variant parent</span>
                        ) : null}
                        {!product.is_active ? (
                          <span className="caption"> · withdrawn</span>
                        ) : null}
                      </td>
                      <td>
                        <StatusChip tone={CLASS_TONE[product.item_class]}>
                          {CLASS_LABEL[product.item_class]}
                        </StatusChip>
                      </td>
                      <td className="muted">{categoryName(product.category_fk)}</td>
                      <td className="muted">{unitName(product.stock_unit_fk)}</td>
                      <td className="muted">
                        {TRACKING_LABEL[product.tracking_policy]}
                        {product.requires_expiry ? ' · expiry' : ''}
                      </td>
                      <td className="numeric">{money(product.list_price, currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title={term ? 'No product matches' : 'The catalogue is empty'}
              detail={
                term
                  ? 'Adjust the search, or clear it to see the whole catalogue.'
                  : 'Add a product, or import a catalogue under Settings.'
              }
              action={
                can('product.manage') && !term ? (
                  <Button tone="primary" onClick={() => setCreating(true)}>
                    Add product
                  </Button>
                ) : undefined
              }
            />
          )}
        </Card>

        {creating ? (
          <ProductDialog
            units={units.data ?? []}
            categories={categories.data ?? []}
            onClose={() => setCreating(false)}
          />
        ) : null}

        {selected ? (
          <ProductDetail
            product={selected}
            units={units.data ?? []}
            onClose={() => setSelected(null)}
          />
        ) : null}
      </WorkSurface>
    </>
  );
}

function ProductDialog({
  units,
  categories,
  onClose,
}: {
  units: Unit[];
  categories: Category[];
  onClose: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const each = units.find((unit) => unit.code === 'ea') ?? units[0];

  const [sku, setSku] = useState('');
  const [name, setName] = useState('');
  const [itemClass, setItemClass] = useState<Product['item_class']>('stocked');
  const [stockUnit, setStockUnit] = useState(each?.id ?? '');
  const [categoryId, setCategoryId] = useState('');
  const [manufacturer, setManufacturer] = useState('');
  const [modelCode, setModelCode] = useState('');
  const [description, setDescription] = useState('');
  const [listPrice, setListPrice] = useState('');
  const [tracking, setTracking] = useState('none');
  const [requiresExpiry, setRequiresExpiry] = useState(false);
  const [barcode, setBarcode] = useState('');
  const [barcodeKind, setBarcodeKind] = useState('ean_13');
  const [error, setError] = useState<unknown>(null);

  const holdsStock = itemClass === 'stocked';

  const create = useMutation({
    mutationFn: () =>
      api.catalogue.create({
        sku,
        name,
        item_class: itemClass,
        stock_unit_id: stockUnit,
        category_id: categoryId || null,
        manufacturer: manufacturer || null,
        model_code: modelCode || null,
        description: description || null,
        list_price: listPrice || null,
        tracking_policy: holdsStock ? tracking : 'none',
        requires_expiry: holdsStock && requiresExpiry,
        barcodes: barcode ? [{ value: barcode, kind: barcodeKind }] : [],
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['products'] });
      toast.show('success', 'Product added.');
      onClose();
    },
    onError: setError,
  });

  const ready = sku.trim().length >= 3 && name.trim().length > 0 && Boolean(stockUnit);

  return (
    <Dialog
      title="Add product"
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
            Add product
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        {error ? <Alert tone="error">{describe(error).message}</Alert> : null}

        <div className="form-grid form-grid--two">
          <Field
            label="SKU"
            required
            hint="Three to forty characters. Identifies the product on import."
            error={fieldError(error, 'sku')}
          >
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={sku}
                onChange={(event) => setSku(event.target.value.toUpperCase())}
                spellCheck={false}
                autoFocus
              />
            )}
          </Field>

          <Field label="Name" required error={fieldError(error, 'name')}>
            {(id) => (
              <TextInput id={id} value={name} onChange={(event) => setName(event.target.value)} />
            )}
          </Field>

          <Field
            label="Classification"
            required
            hint={CLASS_HINT[itemClass]}
            error={fieldError(error, 'item_class')}
          >
            {(id, describedBy) => (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={itemClass}
                onChange={(event) =>
                  setItemClass(event.target.value as Product['item_class'])
                }
              >
                {Object.entries(CLASS_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Stock unit" required hint="The unit stock is held and valued in.">
            {(id, describedBy) => (
              <Select
                id={id}
                aria-describedby={describedBy}
                value={stockUnit}
                onChange={(event) => setStockUnit(event.target.value)}
              >
                {units.map((unit) => (
                  <option key={unit.id} value={unit.id}>
                    {unit.name} ({unit.code})
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Category" optional>
            {(id) => (
              <Select
                id={id}
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
              >
                <option value="">Uncategorised</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {'— '.repeat(category.depth)}
                    {category.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Indicative price" optional hint="The quotation engine computes the actual one.">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                numeric
                value={listPrice}
                onChange={(event) => setListPrice(event.target.value)}
                placeholder="0.00"
              />
            )}
          </Field>

          <Field label="Manufacturer" optional>
            {(id) => (
              <TextInput
                id={id}
                value={manufacturer}
                onChange={(event) => setManufacturer(event.target.value)}
              />
            )}
          </Field>

          <Field label="Model" optional>
            {(id) => (
              <TextInput
                id={id}
                value={modelCode}
                onChange={(event) => setModelCode(event.target.value)}
              />
            )}
          </Field>

          <Field label="Description" optional full>
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

        <fieldset className="fieldset">
          <legend className="fieldset__legend">Barcode</legend>
          <div className="form-grid form-grid--two">
            <Field
              label="Barcode"
              optional
              hint="Checked against its symbology before it is accepted."
              error={fieldError(error, 'barcode')}
            >
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  numeric
                  value={barcode}
                  onChange={(event) => setBarcode(event.target.value)}
                  spellCheck={false}
                />
              )}
            </Field>
            <Field label="Symbology">
              {(id) => (
                <Select
                  id={id}
                  value={barcodeKind}
                  onChange={(event) => setBarcodeKind(event.target.value)}
                >
                  {[
                    ['ean_13', 'EAN-13'],
                    ['ean_8', 'EAN-8'],
                    ['upc_a', 'UPC-A'],
                    ['itf_14', 'ITF-14'],
                    ['code_128', 'CODE-128'],
                    ['code_39', 'CODE-39'],
                    ['internal', 'Internal'],
                  ].map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
        </fieldset>

        {holdsStock ? (
          <fieldset className="fieldset">
            <legend className="fieldset__legend">Stock handling</legend>
            <div className="form-grid form-grid--two">
              <Field label="Tracking" hint="Batch or serial identification of individual units.">
                {(id, describedBy) => (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    value={tracking}
                    onChange={(event) => setTracking(event.target.value)}
                  >
                    {Object.entries(TRACKING_LABEL).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <div style={{ alignSelf: 'end', paddingBottom: 'var(--space-2)' }}>
                <Checkbox
                  label="Requires an expiry date"
                  checked={requiresExpiry}
                  onChange={setRequiresExpiry}
                />
              </div>
            </div>
          </fieldset>
        ) : (
          <Alert tone="info" title={`${CLASS_LABEL[itemClass]} items hold no stock`}>
            {CLASS_HINT[itemClass]} Tracking and expiry do not apply.
          </Alert>
        )}
      </div>
    </Dialog>
  );
}

function ProductDetail({
  product,
  units,
  onClose,
}: {
  product: Product;
  units: Unit[];
  onClose: () => void;
}) {
  const currency = useCurrency();
  const attributes = useQuery({
    queryKey: ['attributes'],
    queryFn: api.catalogue.attributes,
  });
  const suppliers = useQuery({
    queryKey: ['product-suppliers', product.id],
    queryFn: () => api.catalogue.suppliersOf(product.id),
  });

  const unitName = (id: string | null) =>
    id ? (units.find((unit) => unit.id === id)?.name ?? '—') : '—';
  const attributeName = (id: string) =>
    (attributes.data ?? []).find((item: Attribute) => item.id === id)?.name ?? id;

  return (
    <Dialog title={`${product.sku} — ${product.name}`} size="lg" onClose={onClose}>
      <div className="stack gap-5">
        <div className="row gap-2 row--wrap">
          <StatusChip tone={CLASS_TONE[product.item_class]}>
            {CLASS_LABEL[product.item_class]}
          </StatusChip>
          {product.holds_stock ? (
            <StatusChip tone="neutral">Carries a stock balance</StatusChip>
          ) : (
            <StatusChip tone="info">No stock balance</StatusChip>
          )}
          {product.is_variant_parent ? (
            <StatusChip tone="accent">Variant parent</StatusChip>
          ) : null}
          {!product.is_active ? <StatusChip tone="neutral">Withdrawn</StatusChip> : null}
        </div>

        <dl className="details">
          <dt>Description</dt>
          <dd>{product.description ?? <span className="muted">Not recorded</span>}</dd>
          <dt>Manufacturer</dt>
          <dd>{product.manufacturer ?? <span className="muted">Not recorded</span>}</dd>
          <dt>Model</dt>
          <dd>{product.model_code ?? <span className="muted">Not recorded</span>}</dd>
          <dt>Stock unit</dt>
          <dd>{unitName(product.stock_unit_fk)}</dd>
          <dt>Sales unit</dt>
          <dd>{unitName(product.sales_unit_fk)}</dd>
          <dt>Purchase unit</dt>
          <dd>{unitName(product.purchase_unit_fk)}</dd>
          <dt>Tracking</dt>
          <dd>
            {TRACKING_LABEL[product.tracking_policy]}
            {product.requires_expiry ? ' · expiry date required' : ''}
          </dd>
          <dt>Indicative price</dt>
          <dd className="numeric" style={{ textAlign: 'left' }}>
            {money(product.list_price, currency)}
          </dd>
          <dt>Weight</dt>
          <dd className="numeric" style={{ textAlign: 'left' }}>
            {product.weight_kg ? `${quantity(product.weight_kg)} kg` : '—'}
          </dd>
        </dl>

        {product.barcodes.length > 0 ? (
          <div className="stack gap-2">
            <SectionHeader title="Barcodes" />
            <div className="table-frame" style={{ border: '1px solid var(--colour-border-default)' }}>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Value</th>
                    <th style={{ width: '20%' }}>Symbology</th>
                    <th style={{ width: '16%' }}>Standing</th>
                  </tr>
                </thead>
                <tbody>
                  {product.barcodes.map((code) => (
                    <tr key={code.id}>
                      <td className="numeric" style={{ textAlign: 'left' }}>
                        {code.value}
                      </td>
                      <td className="muted">{code.kind.replace('_', '-').toUpperCase()}</td>
                      <td>
                        {code.is_primary ? (
                          <StatusChip tone="accent">Primary</StatusChip>
                        ) : (
                          <span className="muted">Additional</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {product.attribute_values.length > 0 ? (
          <div className="stack gap-2">
            <SectionHeader title="Attributes" />
            <dl className="details">
              {product.attribute_values.map((value) => (
                <div key={value.attribute_fk} style={{ display: 'contents' }}>
                  <dt>{attributeName(value.attribute_fk)}</dt>
                  <dd>{value.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}

        <div className="stack gap-2">
          <SectionHeader
            title="Suppliers"
            detail="What each supplier calls this product, and on what terms."
          />
          {suppliers.isLoading ? (
            <Skeleton rows={2} height={16} />
          ) : suppliers.data && suppliers.data.length > 0 ? (
            <div className="table-frame" style={{ border: '1px solid var(--colour-border-default)' }}>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Supplier reference</th>
                    <th style={{ width: '18%' }} className="numeric">
                      Last price
                    </th>
                    <th style={{ width: '18%' }}>Recorded</th>
                    <th style={{ width: '14%' }} className="numeric">
                      Lead time
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {suppliers.data.map((link) => (
                    <tr key={link.id}>
                      <td>
                        {link.supplier_sku ?? <span className="muted">Not recorded</span>}
                        {link.is_preferred ? <span className="caption"> · preferred</span> : null}
                      </td>
                      <td className="numeric">{money(link.last_price, currency)}</td>
                      <td className="muted">{link.price_recorded_on ?? '—'}</td>
                      <td className="numeric">
                        {link.lead_time_days !== null ? `${link.lead_time_days} days` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="No supplier recorded"
              detail="Link a supplier from the supplier's own record to compare sourcing."
            />
          )}
        </div>
      </div>
    </Dialog>
  );
}
