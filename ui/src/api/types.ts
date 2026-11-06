/**
 * Contract types mirroring nickland.interface.schemas.
 *
 * A contract test asserts that every path this client calls exists in the service's
 * published OpenAPI schema, so a contract change cannot drift from its consumer
 * unnoticed.
 */

export interface ApiError {
  code: string;
  message: string;
  field?: string | null;
  detail?: Record<string, string>;
}

export interface Preset {
  code: string;
  name: string;
  description: string;
  source: string;
  recommended: boolean;
}

export interface SetupStatus {
  setup_required: boolean;
  presets: Preset[];
  password_requirements: string[];
}

export interface SetupPayload {
  business_name: string;
  administrator_username: string;
  administrator_full_name: string;
  administrator_password: string;
  theme_preset: string;
  legal_name?: string | null;
  tax_identifier?: string | null;
  registration_number?: string | null;
  email?: string | null;
  telephone?: string | null;
  website?: string | null;
  administrator_email?: string | null;
  base_currency?: string;
  currency_symbol?: string;
  currency_precision?: number;
  timezone?: string;
  financial_year_start_month?: number;
  branch_code?: string;
  branch_name?: string;
  warehouse_code?: string;
  warehouse_name?: string;
}

export interface SetupResult {
  business_id: string;
  branch_id: string;
  administrator_id: string;
  roles_created: number;
  sequences_created: number;
}

export interface Actor {
  user_id: string;
  username: string;
  full_name: string;
  business_id: string;
  branch_id: string | null;
  role_codes: string[];
  permissions: string[];
  is_administrator: boolean;
}

export interface SignInResult {
  token: string;
  expires_at: string;
  must_change_password: boolean;
  actor: Actor;
}

export interface Business {
  id: string;
  name: string;
  legal_name: string | null;
  trading_name: string | null;
  tax_identifier: string | null;
  registration_number: string | null;
  email: string | null;
  telephone: string | null;
  website: string | null;
  base_currency: string;
  currency_symbol: string;
  currency_precision: number;
  timezone: string;
  locale: string;
  financial_year_start_month: number;
  setup_completed_at: string | null;
}

export interface Branch {
  id: string;
  code: string;
  name: string;
  is_primary: boolean;
  is_active: boolean;
  email: string | null;
  telephone: string | null;
}

export interface Warehouse {
  id: string;
  branch_fk: string;
  code: string;
  name: string;
  is_default: boolean;
  is_active: boolean;
  allows_negative_stock: boolean;
}

export interface Sequence {
  id: string;
  document_kind: string;
  document_label: string;
  prefix: string;
  pattern: string;
  reset_cadence: 'never' | 'annually' | 'monthly';
  period_key: string;
  next_value: number;
  is_active: boolean;
  preview: string;
}

export interface User {
  id: string;
  username: string;
  full_name: string;
  email: string | null;
  telephone: string | null;
  branch_id: string | null;
  is_active: boolean;
  must_change_password: boolean;
  locked_until: string | null;
  last_signed_in_at: string | null;
  role_codes: string[];
}

export interface Role {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_administrator: boolean;
  is_read_only: boolean;
  is_system: boolean;
  permissions: string[];
}

export interface Permission {
  code: string;
  name: string;
  group: string;
  description: string;
  sensitive: boolean;
}

export interface Theme {
  preset_code: string;
  preset_name: string;
  preset_source: string;
  colour: Record<string, string>;
  overrides: Record<string, string>;
  customisable: string[];
  dark_mode_enabled: boolean;
}

export interface AuditLine {
  label: string;
  foreground: string;
  background: string;
  ratio: number;
  required: number;
  passes: boolean;
}

export interface ThemePreview {
  theme: Theme;
  audit: AuditLine[];
  passes: boolean;
}

export interface CustomField {
  id: string;
  target: string;
  code: string;
  label: string;
  field_type: string;
  help_text: string | null;
  is_required: boolean;
  is_active: boolean;
  display_order: number;
  choices: string[] | null;
  default_value: unknown;
  show_on_documents: boolean;
}

export type HealthState = 'ok' | 'degraded' | 'failed' | 'unavailable';

export interface HealthCheck {
  name: string;
  state: HealthState;
  detail: string;
  measurements: Record<string, unknown>;
}

export interface Health {
  state: HealthState;
  deployment_mode: string;
  checks: HealthCheck[];
}

export interface Backup {
  id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  trigger: string;
  storage_key: string | null;
  size_bytes: number | null;
  verified_at: string | null;
  verification_note: string | null;
  failure_reason: string | null;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_username: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  entity_label: string | null;
  reason: string | null;
  before_values: Record<string, unknown> | null;
  after_values: Record<string, unknown> | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

// --- Phase 2: catalogue, parties and import ------------------------------------------

export interface Unit {
  id: string;
  code: string;
  name: string;
  plural_name: string | null;
  kind: string;
  base_fk: string | null;
  factor: string;
  decimal_places: number;
  is_active: boolean;
  is_system: boolean;
}

export interface Category {
  id: string;
  code: string;
  name: string;
  description: string | null;
  parent_fk: string | null;
  path: string;
  depth: number;
  display_order: number;
  is_active: boolean;
  default_tax_code: string | null;
}

export interface Attribute {
  id: string;
  code: string;
  name: string;
  kind: string;
  choices: string[] | null;
  unit_label: string | null;
  is_variant_defining: boolean;
  display_order: number;
  is_active: boolean;
}

export interface Barcode {
  id: string;
  value: string;
  kind: string;
  unit_fk: string | null;
  is_primary: boolean;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string | null;
  item_class: 'stocked' | 'non_stock' | 'service' | 'bundle';
  category_fk: string | null;
  parent_fk: string | null;
  is_variant_parent: boolean;
  variant_label: string | null;
  variant_values: Record<string, string> | null;
  stock_unit_fk: string;
  sales_unit_fk: string | null;
  purchase_unit_fk: string | null;
  tracking_policy: 'none' | 'batch' | 'serial';
  requires_expiry: boolean;
  shelf_life_days: number | null;
  valuation_policy: string;
  manufacturer: string | null;
  model_code: string | null;
  country_of_origin: string | null;
  list_price: string | null;
  currency_code: string | null;
  tax_code: string | null;
  weight_kg: string | null;
  volume_m3: string | null;
  reorder_level: string | null;
  reorder_quantity: string | null;
  lead_time_days: number | null;
  is_active: boolean;
  is_sellable: boolean;
  is_purchasable: boolean;
  barcodes: Barcode[];
  attribute_values: { attribute_fk: string; value: string }[];
  holds_stock: boolean;
}

export interface Contact {
  id: string;
  name: string;
  position: string | null;
  email: string | null;
  telephone: string | null;
  is_primary: boolean;
  notes: string | null;
}

export interface Party {
  id: string;
  code: string;
  name: string;
  legal_name: string | null;
  party_type: 'organisation' | 'individual';
  is_customer: boolean;
  is_supplier: boolean;
  is_affiliate: boolean;
  email: string | null;
  telephone: string | null;
  website: string | null;
  tax_identifier: string | null;
  registration_number: string | null;
  tax_treatment: string;
  tax_exemption_reference: string | null;
  tax_exemption_expires: string | null;
  payment_term_kind: string;
  payment_term_days: number;
  payment_terms_label: string;
  credit_limit: string | null;
  credit_on_hold: boolean;
  credit_hold_reason: string | null;
  price_list_fk: string | null;
  discount_percent: string | null;
  default_lead_time_days: number | null;
  bank_name: string | null;
  bank_account_name: string | null;
  bank_account_number: string | null;
  mobile_money_provider: string | null;
  mobile_money_number: string | null;
  notes: string | null;
  is_active: boolean;
  contacts: Contact[];
}

export interface Credit {
  standing: string;
  limit: string;
  outstanding: string;
  overdue: string;
  available: string;
  detail: string;
  permits_credit_sale: boolean;
}

export interface PriceList {
  id: string;
  code: string;
  name: string;
  currency_code: string;
  is_default: boolean;
  is_active: boolean;
  tax_inclusive: boolean;
  effective_from: string | null;
  effective_to: string | null;
}

export interface PriceListLine {
  id: string;
  price_list_fk: string;
  product_fk: string;
  unit_fk: string | null;
  minimum_quantity: string;
  price: string;
}

export interface ResolvedPrice {
  price: string;
  source: string;
  source_label: string;
  currency_code: string | null;
  tax_inclusive: boolean;
  minimum_quantity: string;
}

export interface SupplierProduct {
  id: string;
  supplier_fk: string;
  product_fk: string;
  supplier_sku: string | null;
  supplier_name: string | null;
  unit_fk: string | null;
  units_per_purchase_unit: string;
  last_price: string | null;
  currency_code: string | null;
  price_recorded_on: string | null;
  lead_time_days: number | null;
  minimum_order_quantity: string | null;
  is_preferred: boolean;
  is_active: boolean;
  notes: string | null;
}

export interface AffiliateProfile {
  id: string;
  party_fk: string;
  scope: string;
  method: string;
  rate: string;
  fixed_amount: string | null;
  currency_code: string | null;
  effective_from: string | null;
  effective_to: string | null;
  payout_trigger: string;
  agreement_reference: string | null;
  notes: string | null;
  is_active: boolean;
  eligible_product_ids: string[];
  eligible_category_ids: string[];
}

export interface RowReport {
  line: number;
  outcome: 'create' | 'amend' | 'reject';
  reference: string;
  message: string;
  field: string | null;
}

export interface ImportReport {
  kind: string;
  filename: string;
  total: number;
  to_create: number;
  to_amend: number;
  rejected: number;
  acceptable: boolean;
  committed: boolean;
  summary: string;
  run_id: string | null;
  rows: RowReport[];
}

export interface ImportRun {
  id: string;
  kind: string;
  filename: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  total_rows: number;
  accepted_rows: number;
  rejected_rows: number;
  created_rows: number;
  amended_rows: number;
  failure_reason: string | null;
}

// -- the ledger ------------------------------------------------------------------------

export type AccountingBasis = 'accrual' | 'cash';
export type JournalState =
  | 'draft'
  | 'pending_approval'
  | 'posted'
  | 'reversed'
  | 'rejected';

export interface AccountGroup {
  id: string;
  code: string;
  name: string;
  kind: string;
  statement: string;
  display_order: number;
  is_active: boolean;
}

export interface Account {
  id: string;
  code: string;
  name: string;
  description: string | null;
  kind: string;
  statement: string;
  group_fk: string | null;
  cash_flow_category: string;
  control_subledger: string | null;
  currency_code: string | null;
  is_active: boolean;
  is_system: boolean;
  permits_manual_posting: boolean;
  display_order: number;
  balance: string;
  presented_side: 'debit' | 'credit';
}

export interface AccountingPeriod {
  id: string;
  financial_year: number;
  sequence: number;
  label: string;
  starts_on: string;
  ends_on: string;
  state: 'open' | 'closed' | 'reopened';
  closed_at: string | null;
  reopened_at: string | null;
  reopen_reason: string | null;
  is_year_end: boolean;
  year_end_posted_at: string | null;
}

export interface CloseCheck {
  name: string;
  passed: boolean;
  detail: string;
  measurements: Record<string, unknown>;
}

export interface CloseReadiness {
  period: AccountingPeriod;
  checks: CloseCheck[];
  ready: boolean;
}

export interface YearEndOutcome {
  period: AccountingPeriod;
  entry_number: string | null;
  accounts_closed: number;
  net_result: string;
}

export interface JournalLine {
  id: string;
  account_fk: string;
  display_order: number;
  debit: string;
  credit: string;
  currency_code: string;
  narration: string | null;
  party_fk: string | null;
  subledger: string | null;
  state: JournalState;
  account_code: string;
  account_name: string;
}

export interface JournalEntry {
  id: string;
  number: string | null;
  posting_date: string;
  period_fk: string | null;
  state: JournalState;
  source: string;
  source_reference: string | null;
  narration: string;
  currency_code: string;
  total_debit: string;
  total_credit: string;
  created_at: string;
  created_by_id: string | null;
  posted_at: string | null;
  approved_at: string | null;
  approved_by_id: string | null;
  rejection_reason: string | null;
  reverses_fk: string | null;
  reversed_by_fk: string | null;
  reversal_reason: string | null;
  lines: JournalLine[];
}

export interface PostingOutcome {
  entry: JournalEntry;
  created: boolean;
  awaiting_approval: boolean;
}

export interface JournalPage {
  entries: JournalEntry[];
  total: number;
  offset: number;
  limit: number;
}

export interface JournalApproval {
  id: string;
  decided_at: string;
  decided_by_id: string;
  decision: string;
  reason: string | null;
  threshold_amount: string | null;
}

export interface ApprovalThreshold {
  id: string;
  scope: string;
  amount: string;
  requires_distinct_checker: boolean;
  is_active: boolean;
}

export interface DraftLine {
  account_code: string;
  side: 'debit' | 'credit';
  amount: string;
  narration?: string | null;
  party_id?: string | null;
  subledger?: string | null;
}

export interface TrialBalanceRow {
  code: string;
  name: string;
  kind: string;
  debit: string;
  credit: string;
}

export interface TrialBalance {
  as_at: string;
  starts_on: string | null;
  basis: AccountingBasis;
  rows: TrialBalanceRow[];
  total_debit: string;
  total_credit: string;
  difference: string;
  balanced: boolean;
}

export interface LedgerMovement {
  entry_id: string;
  number: string | null;
  posting_date: string;
  narration: string;
  line_narration: string | null;
  source: string;
  debit: string;
  credit: string;
  running_balance: string;
  party_id: string | null;
  state: JournalState;
}

export interface AccountLedger {
  account: Account;
  starts_on: string | null;
  ends_on: string;
  basis: AccountingBasis;
  opening_balance: string;
  movements: LedgerMovement[];
  total_debit: string;
  total_credit: string;
  closing_balance: string;
  presented_side: 'debit' | 'credit';
}

export interface StatementLine {
  code: string;
  name: string;
  amount: string;
}

export interface StatementSection {
  code: string;
  name: string;
  lines: StatementLine[];
  total: string;
}

export interface ProfitAndLoss {
  starts_on: string;
  ends_on: string;
  basis: AccountingBasis;
  revenue: StatementSection[];
  expenses: StatementSection[];
  total_revenue: string;
  total_cost_of_sales: string;
  gross_profit: string;
  total_expenses: string;
  net_result: string;
}

export interface BalanceSheet {
  as_at: string;
  basis: AccountingBasis;
  assets: StatementSection[];
  liabilities: StatementSection[];
  equity: StatementSection[];
  total_assets: string;
  total_liabilities: string;
  total_equity: string;
  total_liabilities_and_equity: string;
  retained_result: string;
  difference: string;
  balanced: boolean;
}

export interface CashFlow {
  starts_on: string;
  ends_on: string;
  opening_cash: string;
  operating: string;
  investing: string;
  financing: string;
  unclassified: string;
  net_movement: string;
  closing_cash: string;
  reconciles: boolean;
}

export interface SubledgerVariance {
  account_code: string;
  account_name: string;
  subledger: string;
  control_balance: string;
  subledger_balance: string;
  difference: string;
  agrees: boolean;
}

export interface LedgerSummary {
  as_at: string;
  basis: AccountingBasis;
  total_assets: string;
  total_liabilities: string;
  total_equity: string;
  cash_on_hand: string;
  receivables: string;
  payables: string;
  revenue_year_to_date: string;
  expenses_year_to_date: string;
  net_result_year_to_date: string;
  trial_balance_difference: string;
  accounting_equation_difference: string;
  pending_approvals: number;
  open_period_label: string | null;
  financial_year: number | null;
}

// -- documents -------------------------------------------------------------------------

export type PageFormatCode = 'a4' | 'a5' | 'letter' | 'legal' | 'roll_80' | 'roll_58';

export interface PageFormatInfo {
  code: PageFormatCode;
  label: string;
  width_mm: string;
  height_mm: string | null;
  is_roll: boolean;
}

export interface DocumentType {
  id: string;
  code: string;
  name: string;
  kind: string;
  classification: 'customer_facing' | 'internal';
  sequence_kind: string | null;
  is_system: boolean;
  is_active: boolean;
  display_order: number;
  page_format: PageFormatCode;
  published_version: number | null;
}

export interface TemplateDefinition {
  page?: PageFormatCode;
  classification?: string;
  /** null follows the kind's own default. */
  show_prices?: boolean | null;
  show_bill_to?: boolean;
  show_ship_to?: boolean;
  show_delivery?: boolean;
  show_totals?: boolean;
  show_terms?: boolean;
  show_banking?: boolean;
  show_notes?: boolean;
  show_signatures?: boolean;
  show_index_column?: boolean;
  show_unit_column?: boolean;
  show_discount_column?: boolean;
  show_batch_and_expiry?: boolean;
  show_manufacturer?: boolean;
  terms?: [string, string][];
  notes?: string[];
  footer_note?: string | null;
}

export interface DocumentTemplate {
  id: string;
  version: number;
  name: string;
  definition: TemplateDefinition;
  is_published: boolean;
  published_at: string | null;
}

export interface IssuedDocument {
  id: string;
  number: string;
  kind: string;
  issued_on: string;
  issued_at: string;
  issued_by_id: string | null;
  template_version: number | null;
  page_format: PageFormatCode;
  media_type: string;
  content_hash: string;
  byte_count: number;
  cancelled_at: string | null;
}

export interface ThermalPreview {
  width: number;
  text: string;
}

// -- taxation --------------------------------------------------------------------------

export type TaxOutcome =
  | 'applied'
  | 'not_applicable'
  | 'exempt'
  | 'needs_review'
  | 'overridden';

export interface TaxAuthority {
  id: string;
  code: string;
  name: string;
  jurisdiction: string | null;
  registration_identifier: string | null;
  is_active: boolean;
  display_order: number;
}

export interface TaxRuleVersion {
  id: string;
  effective_from: string;
  effective_to: string | null;
  method: string;
  rate: string;
  fixed_amount: string;
  threshold: string;
  threshold_applies_to_excess: boolean;
  bands: Record<string, unknown>[] | null;
  note: string | null;
}

export interface TaxCondition {
  field: string;
  operator: string;
  value: string | null;
  values: string[] | null;
}

export interface TaxRule {
  id: string;
  code: string;
  name: string;
  description: string | null;
  category: string;
  treatment: string;
  authority_fk: string | null;
  jurisdiction: string | null;
  is_mandatory: boolean;
  applies_to_sales: boolean;
  applies_to_purchases: boolean;
  cascade_order: number;
  compounds_on_preceding: boolean;
  liability_account_code: string | null;
  recoverable_account_code: string | null;
  expense_account_code: string | null;
  filing_frequency: string;
  due_date_rule: string;
  due_days: number;
  due_day_of_month: number;
  is_active: boolean;
  is_system: boolean;
  withdrawal_reason: string | null;
  versions: TaxRuleVersion[];
  conditions: TaxCondition[];
  current_rate: string | null;
}

export interface TaxAssessmentResult {
  rule_code: string;
  rule_name: string;
  category: string;
  outcome: TaxOutcome;
  treatment: string;
  method: string;
  base: string;
  rate: string;
  amount: string;
  detail: string;
  version_effective_from: string | null;
}

export interface TaxLineResult {
  line_reference: string;
  net: string;
  charged: string;
  withheld: string;
  assessments: TaxAssessmentResult[];
}

export interface TaxEvaluation {
  lines: TaxLineResult[];
  charged: string;
  withheld: string;
  needs_review: number;
  by_rule: Record<string, string>;
}

export interface RecordedAssessment {
  id: string;
  rule_code: string;
  rule_name: string;
  category: string;
  outcome: TaxOutcome;
  treatment: string;
  source_type: string;
  source_id: string;
  source_line_reference: string;
  assessed_on: string;
  base: string;
  rate: string;
  amount: string;
  currency_code: string;
  is_recoverable: boolean;
  is_withheld: boolean;
  detail: string;
}

export interface TaxObligation {
  id: string;
  rule_fk: string;
  authority_fk: string | null;
  period_starts_on: string;
  period_ends_on: string;
  due_on: string;
  label: string;
  assessed: string;
  recoverable: string;
  withheld: string;
  adjustments: string;
  paid: string;
  carried_forward: string;
  state: 'outstanding' | 'part_paid' | 'paid' | 'written_off';
  currency_code: string;
  net_payable: string;
}

export interface TaxPaymentAllocation {
  obligation_fk: string;
  amount: string;
  allocated_at: string;
}

export interface TaxPayment {
  id: string;
  reference: string;
  paid_on: string;
  amount: string;
  allocated: string;
  authority_fk: string | null;
  method: string | null;
  bank_reference: string | null;
  evidence_reference: string | null;
  currency_code: string;
  allocations: TaxPaymentAllocation[];
}

export interface TaxFiling {
  id: string;
  obligation_fk: string;
  authority_fk: string | null;
  reference: string | null;
  state: 'due' | 'prepared' | 'submitted' | 'accepted' | 'rejected';
  due_on: string;
  submitted_at: string | null;
  acknowledgement_reference: string | null;
  declared_amount: string;
}

export interface TaxSummary {
  as_at: string;
  outstanding: string;
  overdue: string;
  recoverable: string;
  withheld: string;
  paid_this_year: string;
  obligations_outstanding: number;
  obligations_overdue: number;
  filings_due: number;
  rules_active: number;
}

export interface TaxableLineInput {
  reference: string;
  net: string;
  quantity?: string;
  product_tax_code?: string | null;
  product_category?: string | null;
  item_class?: string | null;
}
