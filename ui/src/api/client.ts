/**
 * The service client.
 *
 * Holds no business rule. Every figure it displays is computed by the Python service;
 * this module transports and types the result.
 */

import type {
  AffiliateProfile,
  Attribute,
  Backup,
  Barcode,
  Branch,
  Business,
  Category,
  Contact,
  Credit,
  CustomField,
  Health,
  ImportReport,
  ImportRun,
  Page,
  Party,
  Permission,
  Preset,
  PriceList,
  PriceListLine,
  Product,
  ResolvedPrice,
  Role,
  Sequence,
  SetupPayload,
  SetupResult,
  SetupStatus,
  SignInResult,
  SupplierProduct,
  Theme,
  Unit,
  ThemePreview,
  User,
  Warehouse,
  Actor,
  AuditEvent,
  ApiError,
} from './types';

const BASE = '/api/v1';
const SESSION_HEADER = 'X-Nickland-Session';
const LOCAL_HEADER = 'X-Nickland-Local-Token';
const SESSION_STORAGE_KEY = 'nickland.session';

/** A failure reported by the service, carrying its code and offending field. */
export class ServiceError extends Error {
  readonly code: string;
  readonly field: string | null;
  readonly detail: Record<string, string>;
  readonly status: number;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = 'ServiceError';
    this.status = status;
    this.code = error.code;
    this.field = error.field ?? null;
    this.detail = error.detail ?? {};
  }

  /** Whether the session is no longer valid, so the interface should return to sign-in. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

/** Raised when the service cannot be reached at all. */
export class ServiceUnreachableError extends Error {
  constructor(cause: unknown) {
    super(
      'The application service is not responding. It may still be starting.',
    );
    this.name = 'ServiceUnreachableError';
    this.cause = cause;
  }
}

let sessionToken: string | null = readStoredToken();
let localToken: string | null = readLocalToken();
let onUnauthenticated: (() => void) | null = null;

function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function readLocalToken(): string | null {
  const element = document.querySelector<HTMLMetaElement>('meta[name="nickland-local-token"]');
  return element?.content || null;
}

export function setSessionToken(token: string | null): void {
  sessionToken = token;
  try {
    if (token) {
      window.sessionStorage.setItem(SESSION_STORAGE_KEY, token);
    } else {
      window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch {
    // Storage may be unavailable; the token still holds for this page's lifetime.
  }
}

export function getSessionToken(): string | null {
  return sessionToken;
}

export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (sessionToken) headers[SESSION_HEADER] = sessionToken;
  if (localToken) headers[LOCAL_HEADER] = localToken;

  let url = `${BASE}${path}`;
  if (query) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) search.set(key, String(value));
    }
    const encoded = search.toString();
    if (encoded) url += `?${encoded}`;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ServiceUnreachableError(cause);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const envelope = payload as { error?: ApiError } | null;
    const error = new ServiceError(
      response.status,
      envelope?.error ?? {
        code: 'unknown_error',
        message: `The request failed with status ${response.status}.`,
      },
    );
    if (error.isUnauthenticated && onUnauthenticated) onUnauthenticated();
    throw error;
  }

  return payload as T;
}

export const api = {
  setup: {
    status: () => request<SetupStatus>('GET', '/setup'),
    provision: (payload: SetupPayload) => request<SetupResult>('POST', '/setup', payload),
  },

  session: {
    signIn: (username: string, password: string, device?: string) =>
      request<SignInResult>('POST', '/session', { username, password, device }),
    current: () => request<Actor>('GET', '/session/current'),
    signOut: () => request<void>('DELETE', '/session'),
    changePassword: (current_password: string, new_password: string) =>
      request<void>('POST', '/session/password', { current_password, new_password }),
  },

  business: {
    get: () => request<Business>('GET', '/business'),
    amend: (changes: Partial<Business>) => request<Business>('PATCH', '/business', changes),
    branches: (includeInactive = false) =>
      request<Branch[]>('GET', '/branches', undefined, { include_inactive: includeInactive }),
    createBranch: (payload: {
      code: string;
      name: string;
      email?: string | null;
      telephone?: string | null;
      is_primary?: boolean;
    }) => request<Branch>('POST', '/branches', payload),
    warehouses: () => request<Warehouse[]>('GET', '/warehouses'),
    createWarehouse: (payload: {
      branch_id: string;
      code: string;
      name: string;
      is_default?: boolean;
      allows_negative_stock?: boolean;
    }) => request<Warehouse>('POST', '/warehouses', payload),
  },

  numbering: {
    list: () => request<Sequence[]>('GET', '/numbering'),
    amend: (
      kind: string,
      changes: {
        prefix?: string;
        pattern?: string;
        reset_cadence?: string;
        next_value?: number;
      },
    ) => request<Sequence>('PATCH', `/numbering/${kind}`, changes),
    preview: (kind: string, pattern?: string, prefix?: string) =>
      request<string>('GET', `/numbering/${kind}/preview`, undefined, { pattern, prefix }),
  },

  users: {
    list: (includeInactive = true) =>
      request<User[]>('GET', '/users', undefined, { include_inactive: includeInactive }),
    create: (payload: {
      username: string;
      full_name: string;
      password: string;
      role_codes: string[];
      email?: string | null;
      telephone?: string | null;
      branch_id?: string | null;
      must_change_password?: boolean;
    }) => request<User>('POST', '/users', payload),
    amend: (id: string, changes: Partial<Pick<User, 'full_name' | 'email' | 'telephone'>>) =>
      request<User>('PATCH', `/users/${id}`, changes),
    setActive: (id: string, active: boolean, reason: string) =>
      request<User>('POST', `/users/${id}/active`, { active, reason }),
    unlock: (id: string) => request<User>('POST', `/users/${id}/unlock`),
    resetPassword: (id: string, new_password: string, reason: string) =>
      request<void>('POST', `/users/${id}/password`, { new_password, reason }),
    assignRole: (id: string, role_code: string) =>
      request<void>('POST', `/users/${id}/roles`, { role_code }),
    withdrawRole: (id: string, roleCode: string) =>
      request<void>('DELETE', `/users/${id}/roles/${roleCode}`),
  },

  roles: {
    list: () => request<Role[]>('GET', '/roles'),
    setPermissions: (code: string, permission_codes: string[], reason: string) =>
      request<Role>('PUT', `/roles/${code}/permissions`, { permission_codes, reason }),
  },

  permissions: {
    list: () => request<Permission[]>('GET', '/permissions'),
  },

  appearance: {
    presets: () => request<Preset[]>('GET', '/appearance/presets'),
    active: () => request<Theme>('GET', '/appearance'),
    preview: (preset_code: string, overrides: Record<string, string>) =>
      request<ThemePreview>('POST', '/appearance/preview', { preset_code, overrides }),
    apply: (
      preset_code: string,
      overrides: Record<string, string>,
      dark_mode_enabled?: boolean,
    ) => request<Theme>('PUT', '/appearance', { preset_code, overrides, dark_mode_enabled }),
    reset: () => request<Theme>('POST', '/appearance/reset'),
  },

  customFields: {
    catalogue: () => request<{ targets: string[]; types: string[] }>('GET', '/custom-fields/targets'),
    list: (target: string, includeInactive = false) =>
      request<CustomField[]>('GET', `/custom-fields/${target}`, undefined, {
        include_inactive: includeInactive,
      }),
    define: (payload: Record<string, unknown>) =>
      request<CustomField>('POST', '/custom-fields', payload),
    amend: (id: string, changes: Record<string, unknown>) =>
      request<CustomField>('PATCH', `/custom-fields/${id}`, changes),
    withdraw: (id: string, reason: string) =>
      request<CustomField>('POST', `/custom-fields/${id}/withdraw`, { reason }),
  },

  catalogue: {
    units: (includeInactive = false) =>
      request<Unit[]>('GET', '/units', undefined, { include_inactive: includeInactive }),
    createUnit: (payload: Record<string, unknown>) => request<Unit>('POST', '/units', payload),
    convert: (payload: {
      amount: string;
      from_unit_id: string;
      to_unit_id: string;
      product_id?: string | null;
    }) => request<string>('POST', '/units/convert', payload),

    categories: (includeInactive = false) =>
      request<Category[]>('GET', '/categories', undefined, {
        include_inactive: includeInactive,
      }),
    createCategory: (payload: Record<string, unknown>) =>
      request<Category>('POST', '/categories', payload),

    attributes: () => request<Attribute[]>('GET', '/attributes'),
    createAttribute: (payload: Record<string, unknown>) =>
      request<Attribute>('POST', '/attributes', payload),

    search: (params: {
      term?: string;
      category_id?: string;
      item_class?: string;
      sellable_only?: boolean;
      include_inactive?: boolean;
      include_variant_parents?: boolean;
      offset?: number;
      limit?: number;
    }) =>
      request<{ items: Product[]; total: number; offset: number; limit: number }>(
        'GET',
        '/products',
        undefined,
        params,
      ),
    create: (payload: Record<string, unknown>) => request<Product>('POST', '/products', payload),
    read: (id: string) => request<Product>('GET', `/products/${id}`),
    amend: (id: string, changes: Record<string, unknown>) =>
      request<Product>('PATCH', `/products/${id}`, changes),
    byBarcode: (value: string) =>
      request<Product>('GET', `/products/by-barcode/${encodeURIComponent(value)}`),
    setAttributes: (id: string, values: Record<string, string>) =>
      request<Product>('PUT', `/products/${id}/attributes`, { values }),
    addBarcode: (id: string, payload: Record<string, unknown>) =>
      request<Barcode>('POST', `/products/${id}/barcodes`, payload),
    removeBarcode: (barcodeId: string) => request<void>('DELETE', `/barcodes/${barcodeId}`),
    generateVariants: (id: string, combinations: Record<string, string>[]) =>
      request<Product[]>('POST', `/products/${id}/variants`, { combinations }),
    setUnitFactor: (id: string, unit_id: string, factor: string) =>
      request<string>('PUT', `/products/${id}/unit-factor`, { unit_id, factor }),
    suppliersOf: (id: string) => request<SupplierProduct[]>('GET', `/products/${id}/suppliers`),
  },

  pricing: {
    lists: (includeInactive = false) =>
      request<PriceList[]>('GET', '/price-lists', undefined, {
        include_inactive: includeInactive,
      }),
    createList: (payload: Record<string, unknown>) =>
      request<PriceList>('POST', '/price-lists', payload),
    lines: (id: string) => request<PriceListLine[]>('GET', `/price-lists/${id}/lines`),
    setPrice: (id: string, payload: Record<string, unknown>) =>
      request<PriceListLine>('PUT', `/price-lists/${id}/lines`, payload),
    resolve: (params: { product_id: string; customer_id?: string; quantity?: string }) =>
      request<ResolvedPrice>('GET', '/prices/resolve', undefined, params),
  },

  parties: {
    search: (params: {
      term?: string;
      kind?: string;
      include_inactive?: boolean;
      offset?: number;
      limit?: number;
    }) =>
      request<{ items: Party[]; total: number; offset: number; limit: number }>(
        'GET',
        '/parties',
        undefined,
        params,
      ),
    create: (payload: Record<string, unknown>) => request<Party>('POST', '/parties', payload),
    read: (id: string) => request<Party>('GET', `/parties/${id}`),
    amend: (id: string, changes: Record<string, unknown>) =>
      request<Party>('PATCH', `/parties/${id}`, changes),
    setCreditHold: (id: string, on_hold: boolean, reason: string) =>
      request<Party>('POST', `/parties/${id}/credit-hold`, { on_hold, reason }),
    credit: (id: string) => request<Credit>('GET', `/parties/${id}/credit`),
    addContact: (id: string, payload: Record<string, unknown>) =>
      request<Contact>('POST', `/parties/${id}/contacts`, payload),
    removeContact: (contactId: string) => request<void>('DELETE', `/contacts/${contactId}`),
    linkSupplierProduct: (payload: Record<string, unknown>) =>
      request<SupplierProduct>('PUT', '/supplier-products', payload),
    setCustomerPrice: (payload: Record<string, unknown>) =>
      request<{ id: string; price: string }>('PUT', '/customer-prices', payload),
    affiliateProfile: (id: string) =>
      request<AffiliateProfile>('GET', `/parties/${id}/affiliate`),
    setAffiliateProfile: (payload: Record<string, unknown>) =>
      request<AffiliateProfile>('PUT', '/affiliate-profiles', payload),
  },

  imports: {
    template: (kind: string) => `${BASE}/imports/template/${kind}`,
    preview: (kind: string, filename: string, content_base64: string) =>
      request<ImportReport>('POST', '/imports/preview', { kind, filename, content_base64 }),
    commit: (kind: string, filename: string, content_base64: string) =>
      request<ImportReport>('POST', '/imports/commit', { kind, filename, content_base64 }),
    history: (limit = 30) => request<ImportRun[]>('GET', '/imports', undefined, { limit }),
  },

  platform: {
    health: () => request<Health>('GET', '/health'),
    healthDetail: () => request<Health>('GET', '/health/detail'),
    backups: () => request<Backup[]>('GET', '/backups'),
    runBackup: (include_attachments = true) =>
      request<Backup>('POST', '/backups', { include_attachments }),
    restore: (archive_name: string, reason: string, confirm: boolean) =>
      request<{ status: string; detail: string }>('POST', '/backups/restore', {
        archive_name,
        reason,
        confirm,
      }),
    audit: (params: { entity_type?: string; action?: string; offset?: number; limit?: number }) =>
      request<Page<AuditEvent>>('GET', '/audit', undefined, params),
  },
};
