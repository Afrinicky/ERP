/**
 * The service client.
 *
 * Holds no business rule. Every figure it displays is computed by the Python service;
 * this module transports and types the result.
 */

import type {
  Acceptance,
  AccessPolicy,
  AccessPolicyTerms,
  Account,
  AccountGroup,
  AccountLedger,
  AccountingBasis,
  AccountingPeriod,
  Acknowledgement,
  Actor,
  AffiliateProfile,
  AgeingRow,
  AlertRecord,
  AnalysisCode,
  ApiError,
  ApprovalThreshold,
  AssemblyStepInput,
  AssemblyWorkspace,
  Attachment,
  Attribute,
  AuditEvent,
  Backup,
  BalanceSheet,
  BankStatementRecord,
  Barcode,
  Branch,
  BudgetComparison,
  BudgetDetail,
  BudgetInput,
  BudgetLineInput,
  BudgetRequestRecord,
  BudgetSummary,
  Business,
  CashClassRecord,
  CashFlow,
  CashForecast,
  Cashbook,
  Category,
  ChartData,
  CloseReadiness,
  CollectionNote,
  CommandLayout,
  CommissionAccrual,
  CommissionTotals,
  CommitmentRecord,
  Comparison,
  ComposedDefinition,
  Contact,
  Costing,
  Credit,
  CreditNote,
  CreditNoteRecord,
  CustomField,
  CustomerDeposit,
  CustomerReceipt,
  DebtLineRecord,
  DebtSummary,
  DeliveryInput,
  DiscountLimit,
  DispatchInput,
  DocumentTemplate,
  DocumentType,
  DocumentTypeField,
  Draft,
  DraftLine,
  ErosionReport,
  ExpenseInput,
  ExpenseRecord,
  ExpressionCheck,
  FinancialAccount,
  FinancialAccountKindName,
  ForecastAssumptions,
  ForecastScenario,
  GoodsReceipt,
  Health,
  ImportReport,
  ImportRun,
  InspectionInput,
  InstalmentPlan,
  InventorySettings,
  InvoiceInput,
  InvoiceableLine,
  IssuedDocument,
  JournalApproval,
  JournalEntry,
  JournalPage,
  LandedCharge,
  LedgerItem,
  LedgerSummary,
  LoanDetail,
  LoanInput,
  LoanMovementInput,
  LoanSecurityRecord,
  LoanSummary,
  MarginPolicy,
  MatchVariance,
  MaturityRecord,
  MeasureDrill,
  MeasureRecord,
  OperatorInfo,
  OrderVariance,
  OtherDebtInput,
  OtherDebtRecord,
  Page,
  PageFormatCode,
  PageFormatInfo,
  PaletteField,
  Party,
  PartyStatement,
  PayablesRegister,
  PaymentBatchRecord,
  PaymentInput,
  Permission,
  PlannedCashItem,
  PosCountPreview,
  PosRefund,
  PosRefundInput,
  PosSale,
  PosSaleSummary,
  PosShift,
  PosShiftSummary,
  PosSupervisor,
  PostingOutcome,
  PostingRecord,
  Preset,
  PriceList,
  PriceListLine,
  Product,
  ProfitAndLoss,
  ProfitabilityReport,
  PurchaseOrder,
  PurchaseOrderInput,
  Quotation,
  QuotationPage,
  QuotationRequest,
  QuotationSnapshot,
  ReceiptInput,
  RecordedAssessment,
  RecurringExpenseRecord,
  RequestPage,
  ResolvedPrice,
  RevisionInput,
  Role,
  SaleFact,
  SalesInvoice,
  SalesOrder,
  SalesSummary,
  Schedule,
  SchedulePreview,
  SectionKindInfo,
  SecurityInput,
  Sequence,
  SettlementQuote,
  SetupPayload,
  SetupResult,
  SetupStatus,
  Shipment,
  ShippableLine,
  SignInResult,
  SourceRow,
  StatementLineRecord,
  StockBatch,
  StockBin,
  StockCountSheet,
  StockInspection,
  StockLineInput,
  StockLocation,
  StockMovementRow,
  StockOperation,
  StockPosition,
  StockReconciliation,
  StockReservation,
  StockTransfer,
  StockValuation,
  StockWarehouse,
  SubledgerVariance,
  SuggestedOrder,
  SupplierInvoice,
  SupplierPayment,
  SupplierProduct,
  SupplierQuotation,
  SupplierRequest,
  TaxAuthority,
  TaxCondition,
  TaxEvaluation,
  TaxFiling,
  TaxObligation,
  TaxPayment,
  TaxRule,
  TaxSummary,
  TaxableLineInput,
  TemplateDefinition,
  TenderMethodName,
  Theme,
  ThemePreview,
  ThermalPreview,
  Tolerance,
  TreasuryMovement,
  TreasuryMovementKindName,
  TrialBalance,
  Unit,
  User,
  UserAccess,
  UserSession,
  Warehouse,
  Widget,
  WorklistItem,
  YearEndOutcome,
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
    super('The application service is not responding. It may still be starting.');
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

/**
 * Fetch markup rather than JSON, for the designer's preview.
 *
 * The session is carried in a header, so an `<iframe src>` navigation arrives
 * unauthenticated. Putting the token in the URL instead would send it to the
 * browser's history and to every log between here and the service, for a preview.
 * The markup is fetched like any other call and handed to the frame as `srcdoc`.
 */
async function requestText(
  path: string,
  query?: Record<string, string | number | boolean | undefined>,
): Promise<string> {
  const headers: Record<string, string> = { Accept: 'text/html' };
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
    response = await fetch(url, { method: 'GET', headers });
  } catch (cause) {
    throw new ServiceUnreachableError(cause);
  }
  if (!response.ok) {
    const error = new ServiceError(response.status, {
      code: 'preview_failed',
      message: `The preview could not be rendered (status ${response.status}).`,
    });
    if (error.isUnauthenticated && onUnauthenticated) onUnauthenticated();
    throw error;
  }
  return response.text();
}

/**
 * A file, fetched with the session header and handed back as a blob URL.
 *
 * A plain link cannot carry the header, and putting the token in the query string
 * would write it into the browser history and into any log in front of the service.
 * So the bytes are fetched like any other request and opened from memory. The caller
 * revokes the URL once the viewer has it.
 */
async function requestFile(
  path: string,
  query?: Record<string, string | number | boolean | undefined>,
): Promise<string> {
  const headers: Record<string, string> = { Accept: 'application/pdf' };
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
    response = await fetch(url, { method: 'GET', headers });
  } catch (cause) {
    throw new ServiceUnreachableError(cause);
  }
  if (!response.ok) {
    const error = new ServiceError(response.status, {
      code: 'document_unavailable',
      message: `The document could not be produced (status ${response.status}).`,
    });
    if (error.isUnauthenticated && onUnauthenticated) onUnauthenticated();
    throw error;
  }
  return URL.createObjectURL(await response.blob());
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
    access: (id: string) => request<UserAccess>('GET', `/users/${id}/access`),
    setExceptions: (
      id: string,
      exceptions: { permission_code: string; effect: 'grant' | 'withhold' }[],
      reason: string,
    ) => request<UserAccess>('PUT', `/users/${id}/access`, { exceptions, reason }),
    sessions: (id: string) => request<UserSession[]>('GET', `/users/${id}/sessions`),
    endSessions: (id: string, reason: string) =>
      request<{ ended: number }>('POST', `/users/${id}/sessions/end`, { reason }),
  },

  roles: {
    list: () => request<Role[]>('GET', '/roles'),
    setPermissions: (code: string, permission_codes: string[], reason: string) =>
      request<Role>('PUT', `/roles/${code}/permissions`, { permission_codes, reason }),
    create: (payload: { name: string; description?: string | null; copy_from?: string | null }) =>
      request<Role>('POST', '/roles', payload),
    amend: (code: string, payload: { name: string; description?: string | null }) =>
      request<Role>('PATCH', `/roles/${code}`, payload),
    remove: (code: string, reason: string) =>
      request<void>('POST', `/roles/${code}/remove`, { reason }),
  },

  accessPolicy: {
    get: () => request<AccessPolicy>('GET', '/access-policy'),
    set: (terms: AccessPolicyTerms, reason: string) =>
      request<AccessPolicy>('PUT', '/access-policy', { ...terms, reason }),
  },

  permissions: {
    list: () => request<Permission[]>('GET', '/permissions'),
  },

  appearance: {
    presets: () => request<Preset[]>('GET', '/appearance/presets'),
    active: () => request<Theme>('GET', '/appearance'),
    preview: (preset_code: string, overrides: Record<string, string>) =>
      request<ThemePreview>('POST', '/appearance/preview', { preset_code, overrides }),
    apply: (preset_code: string, overrides: Record<string, string>, dark_mode_enabled?: boolean) =>
      request<Theme>('PUT', '/appearance', { preset_code, overrides, dark_mode_enabled }),
    reset: () => request<Theme>('POST', '/appearance/reset'),
  },

  customFields: {
    catalogue: () =>
      request<{ targets: string[]; types: string[] }>('GET', '/custom-fields/targets'),
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
    affiliateProfile: (id: string) => request<AffiliateProfile>('GET', `/parties/${id}/affiliate`),
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

  ledger: {
    groups: () => request<AccountGroup[]>('GET', '/ledger/groups'),
    accounts: (
      params: {
        term?: string;
        kind?: string;
        include_inactive?: boolean;
        with_balances?: boolean;
        as_at?: string;
      } = {},
    ) => request<Account[]>('GET', '/ledger/accounts', undefined, params),
    account: (id: string) => request<Account>('GET', `/ledger/accounts/${id}`),
    createAccount: (payload: {
      code: string;
      name: string;
      group_id?: string | null;
      description?: string | null;
      cash_flow_category?: string;
      currency_code?: string | null;
      permits_manual_posting?: boolean;
      display_order?: number;
    }) => request<Account>('POST', '/ledger/accounts', payload),
    amendAccount: (id: string, changes: Record<string, unknown>) =>
      request<Account>('PATCH', `/ledger/accounts/${id}`, changes),

    periods: (financial_year?: number) =>
      request<AccountingPeriod[]>('GET', '/ledger/periods', undefined, { financial_year }),
    openYear: (financial_year: number, start_month?: number) =>
      request<AccountingPeriod[]>('POST', '/ledger/periods', { financial_year, start_month }),
    readiness: (periodId: string) =>
      request<CloseReadiness>('GET', `/ledger/periods/${periodId}/readiness`),
    closePeriod: (periodId: string, note?: string) =>
      request<CloseReadiness>('POST', `/ledger/periods/${periodId}/close`, { note }),
    reopenPeriod: (periodId: string, reason: string, approved_by_id?: string) =>
      request<AccountingPeriod>('POST', `/ledger/periods/${periodId}/reopen`, {
        reason,
        approved_by_id,
      }),
    closeYear: (financial_year: number, note?: string) =>
      request<YearEndOutcome>('POST', '/ledger/year-end', { financial_year, note }),

    subledgers: () => request<SubledgerVariance[]>('GET', '/ledger/reconciliation/subledgers'),
  },

  journals: {
    list: (
      params: {
        term?: string;
        state?: string;
        source?: string;
        account_id?: string;
        party_id?: string;
        start?: string;
        end?: string;
        offset?: number;
        limit?: number;
      } = {},
    ) => request<JournalPage>('GET', '/journals', undefined, params),
    read: (id: string) => request<JournalEntry>('GET', `/journals/${id}`),
    post: (payload: {
      posting_date: string;
      narration: string;
      lines: DraftLine[];
      source?: string;
      source_reference?: string | null;
      idempotency_key?: string | null;
      currency_code?: string | null;
    }) => request<PostingOutcome>('POST', '/journals', payload),
    pending: () => request<JournalEntry[]>('GET', '/journals/pending'),
    approvals: (id: string) => request<JournalApproval[]>('GET', `/journals/${id}/approvals`),
    approve: (id: string, note?: string) =>
      request<JournalEntry>('POST', `/journals/${id}/approve`, { note }),
    reject: (id: string, reason: string) =>
      request<JournalEntry>('POST', `/journals/${id}/reject`, { reason }),
    reverse: (id: string, reason: string, posting_date?: string) =>
      request<JournalEntry>('POST', `/journals/${id}/reverse`, { reason, posting_date }),
    threshold: () => request<ApprovalThreshold | null>('GET', '/journals/threshold'),
    setThreshold: (amount: string | null, requires_distinct_checker = true) =>
      request<ApprovalThreshold | null>('PUT', '/journals/threshold', {
        amount,
        requires_distinct_checker,
      }),
  },

  financialReports: {
    summary: (as_at?: string, basis: AccountingBasis = 'accrual') =>
      request<LedgerSummary>('GET', '/reports/ledger/summary', undefined, { as_at, basis }),
    trialBalance: (params: { as_at?: string; start?: string; basis?: AccountingBasis } = {}) =>
      request<TrialBalance>('GET', '/reports/ledger/trial-balance', undefined, params),
    accountLedger: (
      accountId: string,
      params: { start?: string; end?: string; basis?: AccountingBasis; limit?: number } = {},
    ) => request<AccountLedger>('GET', `/reports/ledger/accounts/${accountId}`, undefined, params),
    profitAndLoss: (start: string, end: string, basis: AccountingBasis = 'accrual') =>
      request<ProfitAndLoss>('GET', '/reports/ledger/profit-and-loss', undefined, {
        start,
        end,
        basis,
      }),
    balanceSheet: (as_at?: string, basis: AccountingBasis = 'accrual') =>
      request<BalanceSheet>('GET', '/reports/ledger/balance-sheet', undefined, {
        as_at,
        basis,
      }),
    cashFlow: (start: string, end: string) =>
      request<CashFlow>('GET', '/reports/ledger/cash-flow', undefined, { start, end }),
  },

  documents: {
    formats: () => request<PageFormatInfo[]>('GET', '/documents/formats'),
    types: (includeInactive = false) =>
      request<DocumentType[]>('GET', '/documents/types', undefined, {
        include_inactive: includeInactive,
      }),
    template: (typeId: string) =>
      request<DocumentTemplate>('GET', `/documents/types/${typeId}/template`),
    amendTemplate: (typeId: string, changes: Partial<TemplateDefinition>) =>
      request<DocumentTemplate>('PATCH', `/documents/types/${typeId}/template`, changes),
    /** Fetched with the session header, then opened from memory. */
    preview: (typeId: string, page?: PageFormatCode) =>
      requestFile(`/documents/types/${typeId}/preview`, { page }),
    previewThermal: (typeId: string, width = 42) =>
      request<ThermalPreview>('GET', `/documents/types/${typeId}/preview/thermal`, undefined, {
        width,
      }),
    issue: (payload: {
      type_code: string;
      source_type?: string;
      source_id: string;
      page?: string | null;
    }) => request<IssuedDocument>('POST', '/documents/issue', payload),
    issued: (kind?: string, limit = 50) =>
      request<IssuedDocument[]>('GET', '/documents/issued', undefined, { kind, limit }),
    file: (documentId: string) => requestFile(`/documents/issued/${documentId}/file`),
    acknowledgements: (documentId: string) =>
      request<Acknowledgement[]>('GET', `/documents/issued/${documentId}/acknowledgements`),
    acknowledge: (documentId: string, payload: Record<string, unknown>) =>
      request<Acknowledgement>('POST', `/documents/issued/${documentId}/acknowledgements`, payload),
  },

  attachments: {
    store: (payload: {
      filename: string;
      media_type: string;
      content_base64: string;
      owner_type?: string | null;
      owner_id?: string | null;
      purpose?: string | null;
      immutable?: boolean;
    }) => request<Attachment>('POST', '/attachments', payload),
    forOwner: (ownerType: string, ownerId: string) =>
      request<Attachment[]>('GET', '/attachments', undefined, {
        owner_type: ownerType,
        owner_id: ownerId,
      }),
    file: (attachmentId: string) => requestFile(`/attachments/${attachmentId}/file`),
    remove: (attachmentId: string) => request<void>('DELETE', `/attachments/${attachmentId}`),
  },

  orders: {
    recordAcceptance: (
      quotationId: string,
      payload: {
        method: string;
        reference?: string | null;
        accepted_on?: string | null;
        evidence_attachment_id?: string | null;
        notes?: string | null;
      },
    ) => request<Acceptance>('POST', `/orders/quotations/${quotationId}/acceptance`, payload),
    acceptances: (quotationId: string) =>
      request<Acceptance[]>('GET', `/orders/quotations/${quotationId}/acceptance`),
    convert: (
      quotationId: string,
      payload: { required_by?: string | null; notes?: string | null },
    ) => request<SalesOrder>('POST', `/orders/quotations/${quotationId}/convert`, payload),
    list: (state?: string, limit = 50) =>
      request<SalesOrder[]>('GET', '/orders', undefined, { state, limit }),
    read: (orderId: string) => request<SalesOrder>('GET', `/orders/${orderId}`),
    cancelLine: (orderId: string, lineId: string, payload: { quantity: string; reason: string }) =>
      request<SalesOrder>('POST', `/orders/${orderId}/lines/${lineId}/cancel`, payload),
    cancel: (orderId: string, reason: string) =>
      request<SalesOrder>('POST', `/orders/${orderId}/cancel`, { reason }),
    substitute: (
      orderId: string,
      lineId: string,
      payload: { product_id: string; reason: string },
    ) => request<SalesOrder>('POST', `/orders/${orderId}/lines/${lineId}/substitute`, payload),
    costVariance: (orderId: string) =>
      request<OrderVariance>('GET', `/orders/${orderId}/cost-variance`),
  },

  treasury: {
    accounts: (include_inactive = false) =>
      request<FinancialAccount[]>('GET', '/treasury/accounts', undefined, { include_inactive }),
    createAccount: (payload: {
      kind: FinancialAccountKindName;
      name: string;
      institution?: string | null;
      number?: string | null;
      branch_name?: string | null;
      custodian_id?: string | null;
    }) => request<FinancialAccount>('POST', '/treasury/accounts', payload),
    amendAccount: (
      accountId: string,
      payload: {
        name?: string;
        institution?: string | null;
        number?: string | null;
        branch_name?: string | null;
        custodian_id?: string | null;
        is_active?: boolean;
      },
    ) => request<FinancialAccount>('PATCH', `/treasury/accounts/${accountId}`, payload),
    cashbook: (accountId: string, query: { start?: string; end?: string } = {}) =>
      request<Cashbook>('GET', `/treasury/accounts/${accountId}/cashbook`, undefined, query),
    movements: () => request<TreasuryMovement[]>('GET', '/treasury/movements'),
    record: (payload: {
      kind: TreasuryMovementKindName;
      amount: string;
      reason: string;
      from_account_id?: string | null;
      to_account_id?: string | null;
      charge?: string;
      occurred_on?: string | null;
      reference?: string | null;
    }) => request<TreasuryMovement>('POST', '/treasury/movements', payload),
  },

  receivables: {
    invoiceable: (orderId: string) =>
      request<InvoiceableLine[]>('GET', `/receivables/orders/${orderId}/invoiceable`),
    issueInvoice: (payload: {
      sales_order_id: string;
      lines?: { sales_order_line_id: string; quantity: string }[];
      basis?: 'delivered' | 'ordered';
      customer_reference?: string | null;
      credit_override_reason?: string | null;
    }) => request<SalesInvoice>('POST', '/receivables/invoices', payload),
    invoices: (query: { customer_id?: string; order_id?: string; open_only?: boolean } = {}) =>
      request<SalesInvoice[]>('GET', '/receivables/invoices', undefined, query),
    invoice: (invoiceId: string) =>
      request<SalesInvoice>('GET', `/receivables/invoices/${invoiceId}`),
    credit: (
      invoiceId: string,
      payload: { lines: { invoice_line_id: string; quantity: string }[]; reason: string },
    ) => request<CreditNoteRecord>('POST', `/receivables/invoices/${invoiceId}/credit`, payload),
    provide: (invoiceId: string, amount: string, reason: string) =>
      request<SalesInvoice>('POST', `/receivables/invoices/${invoiceId}/provision`, {
        amount,
        reason,
      }),
    writeOff: (invoiceId: string, reason: string) =>
      request<SalesInvoice>('POST', `/receivables/invoices/${invoiceId}/write-off`, { reason }),
    receive: (payload: {
      customer_id: string;
      amount: string;
      financial_account_id?: string | null;
      received_on?: string | null;
      reference?: string | null;
      allocations?: { invoice_id: string; amount: string }[] | null;
      notes?: string | null;
    }) => request<CustomerReceipt>('POST', '/receivables/receipts', payload),
    receipts: (customer_id?: string) =>
      request<CustomerReceipt[]>('GET', '/receivables/receipts', undefined, { customer_id }),
    allocate: (receiptId: string, invoice_id: string, amount: string) =>
      request<CustomerReceipt>('POST', `/receivables/receipts/${receiptId}/allocate`, {
        invoice_id,
        amount,
      }),
    refundCredit: (
      receiptId: string,
      payload: { amount: string; reason: string; financial_account_id?: string | null },
    ) => request<CustomerReceipt>('POST', `/receivables/receipts/${receiptId}/refund`, payload),
    takeDeposit: (payload: {
      customer_id: string;
      amount: string;
      financial_account_id?: string | null;
      sales_order_id?: string | null;
      condition?: 'on_invoice' | 'on_delivery';
      reference?: string | null;
    }) => request<CustomerDeposit>('POST', '/receivables/deposits', payload),
    deposits: (query: { customer_id?: string; held_only?: boolean } = {}) =>
      request<CustomerDeposit[]>('GET', '/receivables/deposits', undefined, query),
    applyDeposit: (depositId: string, invoice_id: string, amount?: string | null) =>
      request<CustomerDeposit>('POST', `/receivables/deposits/${depositId}/apply`, {
        invoice_id,
        amount,
      }),
    refundDeposit: (
      depositId: string,
      payload: { amount: string; reason: string; financial_account_id?: string | null },
    ) => request<CustomerDeposit>('POST', `/receivables/deposits/${depositId}/refund`, payload),
    ageing: () => request<AgeingRow[]>('GET', '/receivables/ageing'),
    worklist: () => request<WorklistItem[]>('GET', '/receivables/worklist'),
    addNote: (
      customerId: string,
      payload: {
        kind: CollectionNote['kind'];
        note: string;
        invoice_id?: string | null;
        promised_on?: string | null;
        promised_amount?: string | null;
      },
    ) => request<CollectionNote>('POST', `/receivables/customers/${customerId}/notes`, payload),
    notes: (customerId: string) =>
      request<CollectionNote[]>('GET', `/receivables/customers/${customerId}/notes`),
    statement: (customerId: string, start: string, end?: string) =>
      request<PartyStatement>('GET', `/receivables/customers/${customerId}/statement`, undefined, {
        start,
        end,
      }),
  },

  expenses: {
    list: (state?: string) => request<ExpenseRecord[]>('GET', '/expenses', undefined, { state }),
    record: (payload: ExpenseInput) => request<ExpenseRecord>('POST', '/expenses', payload),
    approve: (expenseId: string) =>
      request<ExpenseRecord>('POST', `/expenses/${expenseId}/approve`),
    reject: (expenseId: string, reason: string) =>
      request<ExpenseRecord>('POST', `/expenses/${expenseId}/reject`, { reason }),
    settle: (expenseId: string, financial_account_id: string) =>
      request<ExpenseRecord>('POST', `/expenses/${expenseId}/settle`, { financial_account_id }),
    threshold: () => request<{ amount: string | null }>('GET', '/expenses/threshold'),
    setThreshold: (amount: string | null) =>
      request<{ amount: string | null }>('PUT', '/expenses/threshold', { amount }),
    recurring: () => request<RecurringExpenseRecord[]>('GET', '/expenses/recurring'),
    schedule: (payload: {
      name: string;
      payee_name: string;
      category_code: string;
      description: string;
      net: string;
      frequency: 'weekly' | 'monthly' | 'quarterly';
      next_due: string;
      settlement?: 'paid' | 'on_credit';
      financial_account_id?: string | null;
    }) => request<RecurringExpenseRecord>('POST', '/expenses/recurring', payload),
    raiseDue: () => request<ExpenseRecord[]>('POST', '/expenses/recurring/raise'),
  },

  reconciliation: {
    statements: (account_id?: string) =>
      request<BankStatementRecord[]>('GET', '/reconciliation/statements', undefined, {
        account_id,
      }),
    importStatement: (payload: {
      financial_account_id: string;
      content: string;
      closing_balance: string;
      reference: string;
    }) => request<BankStatementRecord>('POST', '/reconciliation/statements', payload),
    statement: (statementId: string) =>
      request<BankStatementRecord>('GET', `/reconciliation/statements/${statementId}`),
    autoMatch: (statementId: string) =>
      request<{ matched: number }>('POST', `/reconciliation/statements/${statementId}/auto-match`),
    complete: (statementId: string) =>
      request<BankStatementRecord>('POST', `/reconciliation/statements/${statementId}/complete`),
    suggestions: (lineId: string) =>
      request<{ item: LedgerItem; score: number }[]>(
        'GET',
        `/reconciliation/lines/${lineId}/suggestions`,
      ),
    match: (lineId: string, journal_line_ids: string[]) =>
      request<StatementLineRecord>('POST', `/reconciliation/lines/${lineId}/match`, {
        journal_line_ids,
      }),
    unmatch: (lineId: string) =>
      request<StatementLineRecord>('POST', `/reconciliation/lines/${lineId}/unmatch`),
    exclude: (lineId: string, reason: string) =>
      request<StatementLineRecord>('POST', `/reconciliation/lines/${lineId}/exclude`, { reason }),
    post: (lineId: string, kind: TreasuryMovementKindName, reason: string) =>
      request<StatementLineRecord>('POST', `/reconciliation/lines/${lineId}/post`, {
        kind,
        reason,
      }),
    unreconciled: (accountId: string) =>
      request<LedgerItem[]>('GET', `/reconciliation/accounts/${accountId}/unreconciled`),
    sources: (start: string, end: string) =>
      request<SourceRow[]>('GET', '/reconciliation/sources', undefined, { start, end }),
  },

  financing: {
    loans: (direction?: string, state?: string) =>
      request<LoanSummary[]>('GET', '/financing/loans', undefined, { direction, state }),
    preview: (payload: LoanInput) =>
      request<SchedulePreview>('POST', '/financing/loans/preview', payload),
    create: (payload: LoanInput) => request<LoanDetail>('POST', '/financing/loans', payload),
    loan: (loanId: string) => request<LoanDetail>('GET', `/financing/loans/${loanId}`),
    amend: (loanId: string, payload: LoanInput) =>
      request<LoanDetail>('PUT', `/financing/loans/${loanId}`, payload),
    submit: (loanId: string) => request<LoanDetail>('POST', `/financing/loans/${loanId}/submit`),
    approve: (loanId: string, note: string | null) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/approve`, { note }),
    reject: (loanId: string, reason: string) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/reject`, { reason }),
    drawdown: (loanId: string, payload: LoanMovementInput) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/drawdowns`, payload),
    repay: (loanId: string, payload: LoanMovementInput) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/repayments`, payload),
    quote: (loanId: string, on?: string) =>
      request<SettlementQuote>('GET', `/financing/loans/${loanId}/quote`, undefined, { on }),
    settle: (
      loanId: string,
      payload: {
        on?: string | null;
        financial_account_id?: string | null;
        reference?: string | null;
      },
    ) => request<LoanDetail>('POST', `/financing/loans/${loanId}/settle`, payload),
    refinance: (loanId: string, replacement_id: string, on?: string | null) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/refinance`, { replacement_id, on }),
    writeOff: (loanId: string, reason: string) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/write-off`, { reason }),
    penalty: (loanId: string, reason: string, amount?: string | null) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/penalty`, { reason, amount }),
    propose: (loanId: string, payload: RevisionInput) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/revisions`, payload),
    decide: (loanId: string, approve: boolean, note: string | null) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/revisions/decision`, {
        approve,
        note,
      }),
    addSecurity: (loanId: string, payload: SecurityInput) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/securities`, payload),
    releaseSecurity: (securityId: string) =>
      request<LoanSecurityRecord>('POST', `/financing/securities/${securityId}/release`),
    dispute: (loanId: string, disputed: boolean, note: string) =>
      request<LoanDetail>('POST', `/financing/loans/${loanId}/dispute`, { disputed, note }),
    accrue: (as_of?: string | null) =>
      request<{ accrued: number }>('POST', '/financing/accrue', { as_of }),
    threshold: () => request<{ amount: string | null }>('GET', '/financing/threshold'),
    setThreshold: (amount: string | null) =>
      request<{ amount: string | null }>('PUT', '/financing/threshold', { amount }),
    register: (params: {
      side: 'receivable' | 'payable';
      kind?: string;
      standing?: string;
      counterparty?: string;
      due_from?: string;
      due_to?: string;
      include_settled?: boolean;
    }) =>
      request<DebtLineRecord[]>('GET', '/financing/register', undefined, {
        ...params,
        include_settled: params.include_settled ? 'true' : undefined,
      }),
    summary: (window_days = 30) =>
      request<DebtSummary>('GET', '/financing/summary', undefined, {
        window_days: String(window_days),
      }),
    calendar: (days = 90) =>
      request<CommitmentRecord[]>('GET', '/financing/calendar', undefined, { days: String(days) }),
    maturity: () => request<MaturityRecord[]>('GET', '/financing/maturity'),
    cashFlow: (start: string, end: string) =>
      request<CashClassRecord[]>('GET', '/financing/cash-flow', undefined, { start, end }),
    recordOther: (payload: OtherDebtInput) =>
      request<OtherDebtRecord>('POST', '/financing/other-debts', payload),
    settleOther: (
      debtId: string,
      payload: { amount: string; financial_account_id: string; on?: string | null },
    ) => request<OtherDebtRecord>('POST', `/financing/other-debts/${debtId}/settlements`, payload),
  },

  planning: {
    codes: (kind?: string) =>
      request<AnalysisCode[]>('GET', '/planning/analysis-codes', undefined, { kind }),
    createCode: (payload: { kind: AnalysisCode['kind']; code: string; name: string }) =>
      request<AnalysisCode>('POST', '/planning/analysis-codes', payload),
    amendCode: (codeId: string, payload: { name?: string; is_active?: boolean }) =>
      request<AnalysisCode>('PATCH', `/planning/analysis-codes/${codeId}`, payload),
    budgets: () => request<BudgetSummary[]>('GET', '/planning/budgets'),
    createBudget: (payload: BudgetInput) =>
      request<BudgetDetail>('POST', '/planning/budgets', payload),
    budget: (budgetId: string) => request<BudgetDetail>('GET', `/planning/budgets/${budgetId}`),
    amendBudget: (budgetId: string, payload: BudgetInput) =>
      request<BudgetDetail>('PUT', `/planning/budgets/${budgetId}`, payload),
    setLines: (budgetId: string, lines: BudgetLineInput[]) =>
      request<BudgetDetail>('PUT', `/planning/budgets/${budgetId}/lines`, { lines }),
    submit: (budgetId: string) =>
      request<BudgetDetail>('POST', `/planning/budgets/${budgetId}/submit`),
    approve: (budgetId: string, note: string | null) =>
      request<BudgetDetail>('POST', `/planning/budgets/${budgetId}/approve`, { note }),
    reject: (budgetId: string, reason: string) =>
      request<BudgetDetail>('POST', `/planning/budgets/${budgetId}/reject`, { reason }),
    revise: (budgetId: string) =>
      request<BudgetDetail>('POST', `/planning/budgets/${budgetId}/revise`),
    comparison: (budgetId: string) =>
      request<BudgetComparison>('GET', `/planning/budgets/${budgetId}/comparison`),
    budgetDrill: (budgetId: string, line_id: string, period_key: string) =>
      request<PostingRecord[]>('GET', `/planning/budgets/${budgetId}/drill`, undefined, {
        line_id,
        period_key,
      }),
    request: (
      budgetId: string,
      payload: {
        kind: 'transfer' | 'overspend';
        line_id: string;
        from_line_id?: string | null;
        period_key: string;
        amount: string;
        reason: string;
      },
    ) => request<BudgetRequestRecord>('POST', `/planning/budgets/${budgetId}/requests`, payload),
    requests: () => request<BudgetRequestRecord[]>('GET', '/planning/requests'),
    decide: (requestId: string, approve: boolean, note: string | null) =>
      request<BudgetRequestRecord>('POST', `/planning/requests/${requestId}/decision`, {
        approve,
        note,
      }),
    control: () => request<{ enabled: boolean }>('GET', '/planning/budget-control'),
    setControl: (enabled: boolean) =>
      request<{ enabled: boolean }>('PUT', '/planning/budget-control', { enabled }),
    scenarios: () => request<ForecastScenario[]>('GET', '/planning/scenarios'),
    createScenario: (payload: {
      name: string;
      assumptions: ForecastAssumptions;
      notes?: string | null;
    }) => request<ForecastScenario>('POST', '/planning/scenarios', payload),
    amendScenario: (
      scenarioId: string,
      payload: { name: string; assumptions: ForecastAssumptions; notes?: string | null },
    ) => request<ForecastScenario>('PUT', `/planning/scenarios/${scenarioId}`, payload),
    forecast: (horizon_days: number, scenario_id?: string) =>
      request<CashForecast>('GET', '/planning/forecast', undefined, {
        horizon_days: String(horizon_days),
        scenario_id,
      }),
    whatIf: (payload: {
      horizon_days: number;
      scenario_id?: string | null;
      assumptions: ForecastAssumptions;
    }) => request<CashForecast>('POST', '/planning/forecast', payload),
    planned: () => request<PlannedCashItem[]>('GET', '/planning/planned'),
    plan: (payload: {
      expected_on: string;
      amount: string;
      inward: boolean;
      label: string;
      scenario_id?: string | null;
    }) => request<PlannedCashItem>('POST', '/planning/planned', payload),
    unplan: (itemId: string) => request<void>('DELETE', `/planning/planned/${itemId}`),
    threshold: () => request<{ amount: string | null }>('GET', '/planning/cash-threshold'),
    setThreshold: (amount: string | null) =>
      request<{ amount: string | null }>('PUT', '/planning/cash-threshold', { amount }),
    alerts: () => request<AlertRecord[]>('GET', '/planning/alerts'),
  },

  analytics: {
    charts: () => request<{ id: string; title: string }[]>('GET', '/analytics/charts'),
    chart: (chartId: string, start: string, end: string, interval = 'month', compare = true) =>
      request<ChartData>('GET', `/analytics/charts/${chartId}`, undefined, {
        start,
        end,
        interval,
        compare: compare ? 'true' : 'false',
      }),
    drill: (measure: string, start: string, end: string) =>
      request<PostingRecord[]>('GET', '/analytics/drill', undefined, { measure, start, end }),
    profitability: (dimension: string, start: string, end: string, allocate = false) =>
      request<ProfitabilityReport>('GET', '/analytics/profitability', undefined, {
        dimension,
        start,
        end,
        allocate: allocate ? 'true' : 'false',
      }),
    records: (dimension: string, key: string, start: string, end: string) =>
      request<SaleFact[]>('GET', '/analytics/profitability/records', undefined, {
        dimension,
        key,
        start,
        end,
      }),
    erosion: (start: string, end: string) =>
      request<ErosionReport>('GET', '/analytics/erosion', undefined, { start, end }),
  },

  commandCentre: {
    measures: (start: string, end: string) =>
      request<MeasureRecord[]>('GET', '/command-centre/measures', undefined, { start, end }),
    drill: (code: string, start: string, end: string) =>
      request<MeasureDrill>('GET', `/command-centre/measures/${code}/lines`, undefined, {
        start,
        end,
      }),
    revenue: (dimension: string, start: string, end: string) =>
      request<{ key: string; label: string; amount: string }[]>(
        'GET',
        `/command-centre/revenue/${dimension}`,
        undefined,
        { start, end },
      ),
    layout: () => request<CommandLayout>('GET', '/command-centre/layout'),
    saveLayout: (widgets: Widget[], preset: string, role_code?: string | null) =>
      request<CommandLayout>('PUT', '/command-centre/layout', { widgets, preset, role_code }),
    resetLayout: () => request<CommandLayout>('POST', '/command-centre/layout/reset'),
    presets: () =>
      request<{ code: string; label: string; widgets: Widget[] }[]>(
        'GET',
        '/command-centre/presets',
      ),
  },

  purchases: {
    suggest: (salesOrderId: string) =>
      request<SuggestedOrder[]>('GET', `/purchases/orders/suggest/${salesOrderId}`),
    raise: (payload: PurchaseOrderInput) =>
      request<PurchaseOrder>('POST', '/purchases/orders', payload),
    orders: (query: { state?: string; supplier_id?: string; limit?: number } = {}) =>
      request<PurchaseOrder[]>('GET', '/purchases/orders', undefined, query),
    order: (orderId: string) => request<PurchaseOrder>('GET', `/purchases/orders/${orderId}`),
    approve: (orderId: string) =>
      request<PurchaseOrder>('POST', `/purchases/orders/${orderId}/approve`),
    issue: (orderId: string) =>
      request<PurchaseOrder>('POST', `/purchases/orders/${orderId}/issue`),
    cancelOrder: (orderId: string, reason: string) =>
      request<PurchaseOrder>('POST', `/purchases/orders/${orderId}/cancel`, { reason }),
    close: (orderId: string) =>
      request<PurchaseOrder>('POST', `/purchases/orders/${orderId}/close`),

    receive: (payload: ReceiptInput) =>
      request<GoodsReceipt>('POST', '/purchases/receipts', payload),
    receipts: (query: { order_id?: string; limit?: number } = {}) =>
      request<GoodsReceipt[]>('GET', '/purchases/receipts', undefined, query),

    tolerance: () => request<Tolerance>('GET', '/purchases/tolerance'),
    setTolerance: (payload: Tolerance) =>
      request<Tolerance>('PUT', '/purchases/tolerance', payload),
    recordInvoice: (payload: InvoiceInput) =>
      request<SupplierInvoice>('POST', '/purchases/invoices', payload),
    invoices: (query: { state?: string; supplier_id?: string; limit?: number } = {}) =>
      request<SupplierInvoice[]>('GET', '/purchases/invoices', undefined, query),
    invoice: (invoiceId: string) =>
      request<SupplierInvoice>('GET', `/purchases/invoices/${invoiceId}`),
    amendInvoice: (invoiceId: string, payload: InvoiceInput) =>
      request<SupplierInvoice>('PUT', `/purchases/invoices/${invoiceId}`, payload),
    postInvoice: (invoiceId: string) =>
      request<SupplierInvoice>('POST', `/purchases/invoices/${invoiceId}/post`),
    cancelInvoice: (invoiceId: string, reason: string) =>
      request<SupplierInvoice>('POST', `/purchases/invoices/${invoiceId}/cancel`, { reason }),
    pendingVariances: () => request<MatchVariance[]>('GET', '/purchases/variances'),
    decideVariance: (varianceId: string, payload: { approve: boolean; reason: string }) =>
      request<SupplierInvoice>('POST', `/purchases/variances/${varianceId}/decide`, payload),
    creditNote: (payload: {
      invoice_id: string;
      supplier_reference: string;
      amount: string;
      reason: string;
      credited_on?: string | null;
    }) => request<CreditNote>('POST', '/purchases/credit-notes', payload),

    payables: (query: { as_at?: string; supplier_id?: string } = {}) =>
      request<PayablesRegister>('GET', '/purchases/payables', undefined, query),
    setPriority: (invoiceId: string, priority: 'urgent' | 'normal' | 'hold', reason: string) =>
      request<unknown>('POST', `/purchases/payables/invoices/${invoiceId}/priority`, {
        priority,
        reason,
      }),
    supplierStatement: (supplierId: string, start: string, end?: string) =>
      request<PartyStatement>(
        'GET',
        `/purchases/payables/suppliers/${supplierId}/statement`,
        undefined,
        { start, end },
      ),
    batches: () => request<PaymentBatchRecord[]>('GET', '/purchases/payables/batches'),
    prepareBatch: (payload: {
      financial_account_id: string;
      items: { supplier_id: string; amount: string; invoice_ids?: string[] }[];
      pay_on?: string | null;
    }) => request<PaymentBatchRecord>('POST', '/purchases/payables/batches', payload),
    approveBatch: (batchId: string) =>
      request<PaymentBatchRecord>('POST', `/purchases/payables/batches/${batchId}/approve`),
    rejectBatch: (batchId: string, reason: string) =>
      request<PaymentBatchRecord>('POST', `/purchases/payables/batches/${batchId}/reject`, {
        reason,
      }),
    pay: (payload: PaymentInput) =>
      request<SupplierPayment>('POST', '/purchases/payments', payload),
    payments: (query: { supplier_id?: string; limit?: number } = {}) =>
      request<SupplierPayment[]>('GET', '/purchases/payments', undefined, query),
  },

  inventory: {
    settings: () => request<InventorySettings>('GET', '/inventory/settings'),
    setSettings: (payload: InventorySettings) =>
      request<InventorySettings>('PUT', '/inventory/settings', payload),
    warehouses: () => request<StockWarehouse[]>('GET', '/inventory/warehouses'),
    addLocation: (warehouseId: string, payload: { code: string; name: string }) =>
      request<StockLocation>('POST', `/inventory/warehouses/${warehouseId}/locations`, payload),
    addBin: (locationId: string, payload: { code: string; name?: string | null }) =>
      request<StockBin>('POST', `/inventory/locations/${locationId}/bins`, payload),

    positions: (query: { warehouse_id?: string; q?: string } = {}) =>
      request<StockPosition[]>('GET', '/inventory/positions', undefined, query),
    lowStock: () => request<StockPosition[]>('GET', '/inventory/low-stock'),
    expiry: (withinDays?: number) =>
      request<StockBatch[]>('GET', '/inventory/expiry', undefined, { within_days: withinDays }),
    batches: (productId: string) =>
      request<StockBatch[]>('GET', `/inventory/products/${productId}/batches`),
    movements: (
      query: {
        product_id?: string;
        warehouse_id?: string;
        operation_id?: string;
        limit?: number;
      } = {},
    ) => request<StockMovementRow[]>('GET', '/inventory/movements', undefined, query),
    valuation: () => request<StockValuation>('GET', '/inventory/valuation'),
    reconciliation: () => request<StockReconciliation>('GET', '/inventory/reconciliation'),

    opening: (payload: {
      warehouse_id: string;
      lines: StockLineInput[];
      reference?: string | null;
    }) => request<StockOperation>('POST', '/inventory/opening', payload),
    adjust: (payload: {
      warehouse_id: string;
      kind: 'gain' | 'loss' | 'damage' | 'expiry';
      reason: string;
      lines: StockLineInput[];
    }) => request<StockOperation>('POST', '/inventory/adjustments', payload),
    relocate: (payload: {
      product_id: string;
      warehouse_id: string;
      quantity: string;
      to_location_id: string;
      to_bin_id?: string | null;
      from_location_id?: string | null;
      batch_id?: string | null;
    }) => request<StockOperation>('POST', '/inventory/relocations', payload),
    reverse: (operationId: string, reason: string) =>
      request<StockOperation>('POST', `/inventory/operations/${operationId}/reverse`, { reason }),
    issue: (payload: {
      product_id: string;
      warehouse_id: string;
      quantity: string;
      batch_id?: string | null;
      serials?: string[];
      reservation_id?: string | null;
      override_reason?: string | null;
      reference?: string | null;
    }) => request<StockOperation>('POST', '/inventory/issues', payload),

    transfers: () => request<StockTransfer[]>('GET', '/inventory/transfers'),
    raiseTransfer: (payload: {
      from_warehouse_id: string;
      to_warehouse_id: string;
      lines: { product_id: string; quantity: string; batch_id?: string | null }[];
      notes?: string | null;
    }) => request<StockTransfer>('POST', '/inventory/transfers', payload),
    dispatchTransfer: (transferId: string) =>
      request<StockTransfer>('POST', `/inventory/transfers/${transferId}/dispatch`),
    receiveTransfer: (transferId: string) =>
      request<StockTransfer>('POST', `/inventory/transfers/${transferId}/receive`),
    cancelTransfer: (transferId: string) =>
      request<StockTransfer>('POST', `/inventory/transfers/${transferId}/cancel`),

    inspections: () => request<StockInspection[]>('GET', '/inventory/inspections'),
    inspect: (payload: InspectionInput) =>
      request<StockInspection>('POST', '/inventory/inspections', payload),
    returnToSupplier: (payload: {
      product_id: string;
      warehouse_id: string;
      quantity: string;
      reason: string;
      batch_id?: string | null;
      receipt_line_id?: string | null;
    }) => request<StockOperation>('POST', '/inventory/returns/supplier', payload),
    customerReturn: (payload: {
      issue_operation_id: string;
      product_id: string;
      quantity: string;
      reason: string;
    }) => request<StockOperation>('POST', '/inventory/returns/customer', payload),

    reservations: () => request<StockReservation[]>('GET', '/inventory/reservations'),
    reserve: (payload: {
      product_id: string;
      warehouse_id: string;
      quantity: string;
      sales_order_line_id?: string | null;
      batch_id?: string | null;
      notes?: string | null;
    }) => request<StockReservation>('POST', '/inventory/reservations', payload),
    release: (reservationId: string, quantity?: string | null) =>
      request<StockReservation>('POST', `/inventory/reservations/${reservationId}/release`, {
        quantity: quantity ?? null,
      }),

    counts: () => request<StockCountSheet[]>('GET', '/inventory/counts'),
    openCount: (payload: {
      warehouse_id: string;
      location_id?: string | null;
      notes?: string | null;
    }) => request<StockCountSheet>('POST', '/inventory/counts', payload),
    count: (countId: string) => request<StockCountSheet>('GET', `/inventory/counts/${countId}`),
    recordCount: (
      countId: string,
      lines: { line_id: string; counted_quantity: string | null; reason?: string | null }[],
    ) => request<StockCountSheet>('PUT', `/inventory/counts/${countId}/lines`, { lines }),
    submitCount: (countId: string) =>
      request<StockCountSheet>('POST', `/inventory/counts/${countId}/submit`),
    approveCount: (countId: string) =>
      request<StockCountSheet>('POST', `/inventory/counts/${countId}/approve`),
    cancelCount: (countId: string) =>
      request<StockCountSheet>('POST', `/inventory/counts/${countId}/cancel`),

    landedCharge: (payload: {
      receipt_id: string;
      category: string;
      amount: string;
      method?: 'by_value' | 'by_quantity';
      reference?: string | null;
    }) => request<LandedCharge>('POST', '/inventory/landed-charges', payload),
    landedCharges: (receiptId: string) =>
      request<LandedCharge[]>('GET', `/inventory/receipts/${receiptId}/landed-charges`),

    assembly: (orderId: string) =>
      request<AssemblyWorkspace>('GET', `/inventory/assembly/${orderId}`),
    assemblyReserve: (orderId: string, lineId: string, payload: AssemblyStepInput) =>
      request<AssemblyWorkspace>(
        'POST',
        `/inventory/assembly/${orderId}/lines/${lineId}/reserve`,
        payload,
      ),
    assemblyRelease: (orderId: string, lineId: string, payload: AssemblyStepInput) =>
      request<AssemblyWorkspace>(
        'POST',
        `/inventory/assembly/${orderId}/lines/${lineId}/release`,
        payload,
      ),
    assemble: (orderId: string, lineId: string, payload: AssemblyStepInput) =>
      request<AssemblyWorkspace>(
        'POST',
        `/inventory/assembly/${orderId}/lines/${lineId}/assemble`,
        payload,
      ),
  },

  dispatch: {
    shipments: (query: { order_id?: string; state?: string } = {}) =>
      request<Shipment[]>('GET', '/dispatch/shipments', undefined, query),
    shipment: (shipmentId: string) => request<Shipment>('GET', `/dispatch/shipments/${shipmentId}`),
    shippable: (orderId: string) =>
      request<ShippableLine[]>('GET', `/dispatch/orders/${orderId}/lines`),
    prepare: (payload: {
      sales_order_id: string;
      lines: { sales_order_line_id: string; quantity: string; serials?: string[] }[];
      destination?: string | null;
      notes?: string | null;
    }) => request<Shipment>('POST', '/dispatch/shipments', payload),
    pack: (shipmentId: string, packageCount: number) =>
      request<Shipment>('POST', `/dispatch/shipments/${shipmentId}/pack`, {
        package_count: packageCount,
      }),
    dispatch: (shipmentId: string, payload: DispatchInput) =>
      request<Shipment>('POST', `/dispatch/shipments/${shipmentId}/dispatch`, payload),
    deliver: (shipmentId: string, payload: DeliveryInput) =>
      request<Shipment>('POST', `/dispatch/shipments/${shipmentId}/deliver`, payload),
    cancel: (shipmentId: string, reason: string) =>
      request<Shipment>('POST', `/dispatch/shipments/${shipmentId}/cancel`, { reason }),
    customerReturn: (
      shipmentId: string,
      lineId: string,
      payload: { quantity: string; reason: string; replacement_owed: boolean; serials?: string[] },
    ) =>
      request<Shipment>(
        'POST',
        `/dispatch/shipments/${shipmentId}/lines/${lineId}/return`,
        payload,
      ),
    issueDocument: (
      shipmentId: string,
      kind: 'pick_list' | 'packing_list' | 'waybill' | 'delivery_note',
    ) => request<IssuedDocument>('POST', `/dispatch/shipments/${shipmentId}/documents/${kind}`),
  },

  designer: {
    sections: () => request<SectionKindInfo[]>('GET', '/designer/sections'),
    operators: () => request<OperatorInfo[]>('GET', '/designer/operators'),
    palette: (typeId: string) =>
      request<PaletteField[]>('GET', `/designer/types/${typeId}/palette`),

    createType: (payload: Record<string, unknown>) =>
      request<Draft>('POST', '/designer/types', payload),
    typeFields: (typeId: string) =>
      request<DocumentTypeField[]>('GET', `/designer/types/${typeId}/fields`),
    addField: (typeId: string, payload: Record<string, unknown>) =>
      request<DocumentTypeField[]>('POST', `/designer/types/${typeId}/fields`, payload),
    setClassification: (typeId: string, classification: string) =>
      request<Draft>('PUT', `/designer/types/${typeId}/classification`, { classification }),

    openDraft: (typeId: string) => request<Draft>('GET', `/designer/types/${typeId}/draft`),
    readVersion: (templateId: string) => request<Draft>('GET', `/designer/templates/${templateId}`),
    save: (templateId: string, definition: ComposedDefinition) =>
      request<Draft>('PUT', `/designer/templates/${templateId}`, { definition }),
    publish: (templateId: string) =>
      request<Draft>('POST', `/designer/templates/${templateId}/publish`),
    revert: (templateId: string) => request<void>('DELETE', `/designer/templates/${templateId}`),
    duplicate: (templateId: string, name: string) =>
      request<Draft>('POST', `/designer/templates/${templateId}/duplicate`, { name }),

    addSection: (templateId: string, kind: string, title = '') =>
      request<Draft>('POST', `/designer/templates/${templateId}/sections`, { kind, title }),
    moveSection: (templateId: string, sectionId: string, offset: number) =>
      request<Draft>('POST', `/designer/templates/${templateId}/sections/${sectionId}/move`, {
        offset,
      }),
    removeSection: (templateId: string, sectionId: string) =>
      request<Draft>('DELETE', `/designer/templates/${templateId}/sections/${sectionId}`),

    addColumn: (templateId: string, sectionId: string, binding: string) =>
      request<Draft>('POST', `/designer/templates/${templateId}/sections/${sectionId}/columns`, {
        binding,
      }),
    moveColumn: (templateId: string, sectionId: string, columnId: string, offset: number) =>
      request<Draft>(
        'POST',
        `/designer/templates/${templateId}/sections/${sectionId}/columns/${columnId}/move`,
        { offset },
      ),
    amendColumn: (
      templateId: string,
      sectionId: string,
      columnId: string,
      changes: Record<string, unknown>,
    ) =>
      request<Draft>(
        'PUT',
        `/designer/templates/${templateId}/sections/${sectionId}/columns/${columnId}`,
        { changes },
      ),
    removeColumn: (templateId: string, sectionId: string, columnId: string) =>
      request<Draft>(
        'DELETE',
        `/designer/templates/${templateId}/sections/${sectionId}/columns/${columnId}`,
      ),

    addElement: (templateId: string, sectionId: string, element: Record<string, unknown>) =>
      request<Draft>('POST', `/designer/templates/${templateId}/sections/${sectionId}/elements`, {
        element,
      }),
    amendElement: (
      templateId: string,
      sectionId: string,
      elementId: string,
      changes: Record<string, unknown>,
    ) =>
      request<Draft>(
        'PUT',
        `/designer/templates/${templateId}/sections/${sectionId}/elements/${elementId}`,
        { changes },
      ),
    removeElement: (templateId: string, sectionId: string, elementId: string) =>
      request<Draft>(
        'DELETE',
        `/designer/templates/${templateId}/sections/${sectionId}/elements/${elementId}`,
      ),

    setPage: (templateId: string, page: Record<string, unknown>) =>
      request<Draft>('PUT', `/designer/templates/${templateId}/page`, { page }),

    checkExpression: (expression: string, classification: string) =>
      request<ExpressionCheck>('POST', '/designer/expression', {
        expression,
        classification,
      }),

    exportUrl: (templateId: string) => `${BASE}/designer/templates/${templateId}/export`,
    inspectImport: (typeId: string, payload: Record<string, unknown>) =>
      request<ImportReport>('POST', `/designer/types/${typeId}/import/inspect`, { payload }),
    acceptImport: (typeId: string, payload: Record<string, unknown>) =>
      request<Draft>('POST', `/designer/types/${typeId}/import`, { payload }),

    previewHtml: (
      templateId: string,
      page?: PageFormatCode,
      source?: { type: string; id: string },
    ) =>
      requestText(`/documents/templates/${templateId}/preview`, {
        page,
        source_type: source?.type,
        source_id: source?.id,
      }),
  },

  taxation: {
    summary: (as_at?: string) =>
      request<TaxSummary>('GET', '/taxation/summary', undefined, { as_at }),
    authorities: () => request<TaxAuthority[]>('GET', '/taxation/authorities'),
    rules: (includeWithdrawn = false) =>
      request<TaxRule[]>('GET', '/taxation/rules', undefined, {
        include_withdrawn: includeWithdrawn,
      }),
    rule: (ruleId: string) => request<TaxRule>('GET', `/taxation/rules/${ruleId}`),
    createRule: (payload: Record<string, unknown>) =>
      request<TaxRule>('POST', '/taxation/rules', payload),
    setRate: (
      ruleId: string,
      payload: { rate: string; effective_from: string; note?: string | null },
    ) => request<TaxRule>('POST', `/taxation/rules/${ruleId}/rate`, payload),
    setConditions: (ruleId: string, conditions: TaxCondition[]) =>
      request<TaxRule>('PUT', `/taxation/rules/${ruleId}/conditions`, { conditions }),
    withdrawRule: (ruleId: string, reason: string) =>
      request<TaxRule>('POST', `/taxation/rules/${ruleId}/withdraw`, { reason }),
    deleteRule: (ruleId: string) => request<void>('DELETE', `/taxation/rules/${ruleId}`),

    evaluate: (payload: {
      transaction_kind: string;
      on: string;
      direction?: 'sales' | 'purchases';
      lines: TaxableLineInput[];
      party_tax_treatment?: string | null;
      party_is_withholding_agent?: boolean;
      exemption_evidence?: string | null;
      is_import?: boolean;
    }) => request<TaxEvaluation>('POST', '/taxation/evaluate', payload),

    assessments: (
      params: {
        rule_id?: string;
        source_type?: string;
        source_id?: string;
        start?: string;
        end?: string;
        limit?: number;
      } = {},
    ) => request<RecordedAssessment[]>('GET', '/taxation/assessments', undefined, params),
    override: (
      assessmentId: string,
      payload: { amount: string; reason: string; evidence_reference?: string | null },
    ) =>
      request<RecordedAssessment>(
        'POST',
        `/taxation/assessments/${assessmentId}/override`,
        payload,
      ),

    obligations: (state?: string, rule_id?: string) =>
      request<TaxObligation[]>('GET', '/taxation/obligations', undefined, { state, rule_id }),
    payments: (limit = 100) =>
      request<TaxPayment[]>('GET', '/taxation/payments', undefined, { limit }),
    recordPayment: (payload: Record<string, unknown>) =>
      request<TaxPayment>('POST', '/taxation/payments', payload),
    filings: (state?: string) =>
      request<TaxFiling[]>('GET', '/taxation/filings', undefined, { state }),
    openFiling: (obligationId: string) =>
      request<TaxFiling>('POST', `/taxation/obligations/${obligationId}/filing`),
    submitFiling: (
      filingId: string,
      payload: { reference: string; acknowledgement?: string | null },
    ) => request<TaxFiling>('POST', `/taxation/filings/${filingId}/submit`, payload),
  },

  sourcing: {
    requests: (params: { state?: string; limit?: number; offset?: number } = {}) =>
      request<RequestPage>('GET', '/sourcing/requests', undefined, params),
    request: (requestId: string) =>
      request<QuotationRequest>('GET', `/sourcing/requests/${requestId}`),
    createRequest: (payload: Record<string, unknown>) =>
      request<QuotationRequest>('POST', '/sourcing/requests', payload),
    setRequestState: (requestId: string, state: string, reason?: string) =>
      request<QuotationRequest>('POST', `/sourcing/requests/${requestId}/state`, {
        state,
        reason,
      }),
    comparison: (requestId: string) =>
      request<Comparison[]>('GET', `/sourcing/requests/${requestId}/comparison`),
    supplierRequests: (requestId?: string) =>
      request<SupplierRequest[]>('GET', '/sourcing/supplier-requests', undefined, {
        request_id: requestId,
      }),
    createSupplierRequest: (payload: Record<string, unknown>) =>
      request<SupplierRequest>('POST', '/sourcing/supplier-requests', payload),
    issueSupplierRequest: (supplierRequestId: string) =>
      request<SupplierRequest>('POST', `/sourcing/supplier-requests/${supplierRequestId}/issue`),
    supplierQuotations: (requestId?: string) =>
      request<SupplierQuotation[]>('GET', '/sourcing/supplier-quotations', undefined, {
        request_id: requestId,
      }),
    recordSupplierQuotation: (payload: Record<string, unknown>) =>
      request<SupplierQuotation>('POST', '/sourcing/supplier-quotations', payload),
  },

  quotations: {
    list: (
      params: {
        state?: string;
        customer_id?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => request<QuotationPage>('GET', '/quotations', undefined, params),
    summary: () => request<SalesSummary>('GET', '/quotations/summary'),
    read: (quotationId: string) => request<Quotation>('GET', `/quotations/${quotationId}`),
    create: (payload: Record<string, unknown>) =>
      request<Quotation>('POST', '/quotations', payload),
    fromRequest: (requestId: string, payload?: Record<string, unknown>) =>
      request<Quotation>('POST', `/quotations/from-request/${requestId}`, payload),
    costing: (quotationId: string) => request<Costing>('GET', `/quotations/${quotationId}/costing`),
    snapshots: (quotationId: string) =>
      request<QuotationSnapshot[]>('GET', `/quotations/${quotationId}/snapshots`),
    setSources: (lineId: string, sources: Record<string, unknown>[]) =>
      request<Quotation>('PUT', `/quotations/lines/${lineId}/sources`, {
        sources,
      }),
    setExpenses: (quotationId: string, expenses: Record<string, unknown>[]) =>
      request<Quotation>('PUT', `/quotations/${quotationId}/expenses`, {
        expenses,
      }),
    setPaymentPlan: (quotationId: string, payload: Record<string, unknown>) =>
      request<Quotation>('PUT', `/quotations/${quotationId}/payment-plan`, payload),
    recordActualCost: (lineId: string, payload: Record<string, unknown>) =>
      request<Quotation>('POST', `/quotations/lines/${lineId}/actual-cost`, payload),
    submit: (quotationId: string) =>
      request<Quotation>('POST', `/quotations/${quotationId}/submit`),
    approve: (quotationId: string) =>
      request<Quotation>('POST', `/quotations/${quotationId}/approve`),
    issue: (quotationId: string, valid_until?: string | null) =>
      request<Quotation>('POST', `/quotations/${quotationId}/issue`, {
        valid_until,
      }),
    decide: (quotationId: string, payload: Record<string, unknown>) =>
      request<Quotation>('POST', `/quotations/${quotationId}/decide`, payload),
    revise: (quotationId: string, reason: string) =>
      request<Quotation>('POST', `/quotations/${quotationId}/revise`, {
        reason,
      }),
    schedule: (quotationId: string) =>
      request<Schedule>('GET', `/quotations/${quotationId}/schedule`),
    reschedule: (quotationId: string, payload: Record<string, unknown>) =>
      request<Schedule>('POST', `/quotations/${quotationId}/reschedule`, payload),

    marginPolicies: () => request<MarginPolicy[]>('GET', '/quotations/policy/margins'),
    setMarginPolicy: (payload: Record<string, unknown>) =>
      request<MarginPolicy>('PUT', '/quotations/policy/margins', payload),
    withdrawMarginPolicy: (policyId: string) =>
      request<void>('DELETE', `/quotations/policy/margins/${policyId}`),
    discountLimits: () => request<DiscountLimit[]>('GET', '/quotations/policy/discount-limits'),
    setDiscountLimit: (payload: Record<string, unknown>) =>
      request<DiscountLimit>('PUT', '/quotations/policy/discount-limits', payload),
    plans: () => request<InstalmentPlan[]>('GET', '/quotations/policy/plans'),
    createPlan: (payload: Record<string, unknown>) =>
      request<InstalmentPlan>('POST', '/quotations/policy/plans', payload),
    setBands: (planId: string, bands: Record<string, unknown>[]) =>
      request<InstalmentPlan>('PUT', `/quotations/policy/plans/${planId}/bands`, { bands }),
  },

  commissions: {
    register: (params: { state?: string; affiliate_id?: string; limit?: number } = {}) =>
      request<CommissionAccrual[]>('GET', '/commissions', undefined, params),
    totals: () => request<CommissionTotals>('GET', '/commissions/totals'),
    accrue: (payload: Record<string, unknown>) =>
      request<CommissionAccrual[]>('POST', '/commissions/accrue', payload),
    approve: (accrualId: string) =>
      request<CommissionAccrual>('POST', `/commissions/${accrualId}/approve`),
    recordCollection: (accrualId: string, paid_fraction: string) =>
      request<CommissionAccrual>('POST', `/commissions/${accrualId}/collection`, {
        paid_fraction,
      }),
    pay: (accrualId: string, payload: { amount: string; reference?: string | null }) =>
      request<CommissionAccrual>('POST', `/commissions/${accrualId}/pay`, payload),
    cancel: (accrualId: string, reason: string) =>
      request<CommissionAccrual>('POST', `/commissions/${accrualId}/cancel`, {
        reason,
      }),
    override: (accrualId: string, payload: { rate: string; reason: string }) =>
      request<CommissionAccrual>('POST', `/commissions/${accrualId}/override`, payload),
  },

  pos: {
    shift: () => request<PosShiftSummary | null>('GET', '/pos/shift'),
    openShift: (opening_float: string, register?: string) =>
      request<PosShiftSummary>('POST', '/pos/shifts', { opening_float, register_name: register }),
    shifts: () => request<PosShift[]>('GET', '/pos/shifts'),
    shiftSummary: (shiftId: string) => request<PosShiftSummary>('GET', `/pos/shifts/${shiftId}`),
    previewCount: (shiftId: string, denominations: Record<string, number>) =>
      request<PosCountPreview>('POST', `/pos/shifts/${shiftId}/count/preview`, { denominations }),
    submitCount: (
      shiftId: string,
      denominations: Record<string, number>,
      variance_reason?: string | null,
    ) =>
      request<PosShiftSummary>('POST', `/pos/shifts/${shiftId}/count`, {
        denominations,
        variance_reason,
      }),
    approveShift: (shiftId: string) =>
      request<PosShiftSummary>('POST', `/pos/shifts/${shiftId}/approve`),
    cashMovement: (payload: {
      kind: 'cash_in' | 'cash_out';
      amount: string;
      reason: string;
      counter: 'bank' | 'petty_cash';
    }) =>
      request<{ id: string; kind: string; amount: string; reason: string }>(
        'POST',
        '/pos/cash-movements',
        payload,
      ),

    sale: () => request<PosSale>('GET', '/pos/sale'),
    sales: (shift_id?: string) =>
      request<PosSaleSummary[]>('GET', '/pos/sales', undefined, { shift_id }),
    parked: () => request<PosSaleSummary[]>('GET', '/pos/parked'),
    saleById: (saleId: string) => request<PosSale>('GET', `/pos/sales/${saleId}`),
    findSale: (number: string) =>
      request<PosSale>('GET', `/pos/sales/by-number/${encodeURIComponent(number)}`),
    scan: (saleId: string, code: string, quantity = '1') =>
      request<PosSale>('POST', `/pos/sales/${saleId}/scan`, { code, quantity }),
    addLine: (saleId: string, product_id: string, quantity = '1') =>
      request<PosSale>('POST', `/pos/sales/${saleId}/lines`, { product_id, quantity }),
    setQuantity: (saleId: string, lineId: string, quantity: string) =>
      request<PosSale>('PUT', `/pos/sales/${saleId}/lines/${lineId}`, { quantity }),
    overridePrice: (saleId: string, lineId: string, unit_price: string, reason: string) =>
      request<PosSale>('POST', `/pos/sales/${saleId}/lines/${lineId}/override`, {
        unit_price,
        reason,
      }),
    discount: (saleId: string, lineId: string, rate: string) =>
      request<PosSale>('POST', `/pos/sales/${saleId}/lines/${lineId}/discount`, { rate }),
    setCustomer: (saleId: string, customer_id: string | null) =>
      request<PosSale>('PUT', `/pos/sales/${saleId}/customer`, { customer_id }),
    park: (saleId: string, label?: string | null) =>
      request<PosSale>('POST', `/pos/sales/${saleId}/park`, { label }),
    resume: (saleId: string) => request<PosSale>('POST', `/pos/sales/${saleId}/resume`),
    abandon: (saleId: string, reason: string) =>
      request<PosSale>('POST', `/pos/sales/${saleId}/abandon`, { reason }),

    tender: (
      saleId: string,
      payload: {
        method: TenderMethodName;
        amount?: string | null;
        cash_received?: string | null;
        reference?: string | null;
      },
    ) => request<PosSale>('POST', `/pos/sales/${saleId}/tenders`, payload),
    confirmTender: (tenderId: string, provider_reference: string) =>
      request<PosSale>('POST', `/pos/tenders/${tenderId}/confirm`, { provider_reference }),
    failTender: (tenderId: string, reason: string) =>
      request<PosSale>('POST', `/pos/tenders/${tenderId}/fail`, { reason }),
    complete: (saleId: string) => request<PosSale>('POST', `/pos/sales/${saleId}/complete`),
    receiptText: (saleId: string, width = 42) =>
      requestText(`/pos/sales/${saleId}/receipt.txt`, { width }),

    refunds: (saleId: string) => request<PosRefund[]>('GET', `/pos/sales/${saleId}/refunds`),
    refund: (saleId: string, payload: PosRefundInput) =>
      request<PosRefund>('POST', `/pos/sales/${saleId}/refunds`, payload),
    void: (saleId: string, reason: string, supervisor?: PosSupervisor | null) =>
      request<PosRefund>('POST', `/pos/sales/${saleId}/void`, { reason, supervisor }),
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
