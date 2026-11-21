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
  holders: number;
}

export interface AccessException {
  permission_code: string;
  effect: 'grant' | 'withhold';
  reason: string;
  set_at: string;
  set_by: string | null;
}

export interface UserAccess {
  user_id: string;
  full_name: string;
  username: string;
  is_administrator: boolean;
  roles: string[];
  role_names: string[];
  from_roles: string[];
  exceptions: AccessException[];
  effective: string[];
}

export interface UserSession {
  id: string;
  issued_at: string;
  last_seen_at: string;
  expires_at: string;
  device: string | null;
  address: string | null;
}

export interface AccessPolicyTerms {
  minimum_length: number;
  require_uppercase: boolean;
  require_lowercase: boolean;
  require_digit: boolean;
  require_symbol: boolean;
  reuse_window: number;
  lockout_threshold: number;
  lockout_minutes: number;
  idle_minutes: number;
  absolute_hours: number;
}

export interface AccessPolicy extends AccessPolicyTerms {
  is_default: boolean;
  requirements: string[];
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

export interface Acknowledgement {
  id: string;
  document_fk: string;
  recipient_name: string;
  recipient_role: string | null;
  acknowledged_at: string;
  location: string | null;
  comments: string | null;
  evidence_kind: string;
  media_type: string | null;
  evidence_hash: string | null;
  recorded_by_id: string | null;
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

// -- Sourcing, quotations and commission (Phase 5) ------------------------------------
//
// Every monetary figure is a string. The service computes it; this layer transports
// and displays it, and never does arithmetic on it.

export interface MarginPolicy {
  id: string;
  scope: "business" | "category" | "product";
  category_fk: string | null;
  product_fk: string | null;
  method: "markup_on_cost" | "target_gross_margin";
  rate: string;
  is_active: boolean;
  notes: string | null;
  target_name: string | null;
}

export interface DiscountLimit {
  id: string;
  role_code: string;
  maximum_rate: string;
  maximum_amount: string | null;
}

export interface InstalmentBand {
  id: string;
  label: string;
  minimum_months: number;
  maximum_months: number;
  method: "percentage" | "fixed_per_unit";
  rate: string;
  fixed_per_unit: string | null;
}

export interface InstalmentPlan {
  id: string;
  code: string;
  name: string;
  description: string | null;
  applies_to: string;
  frequency: "weekly" | "fortnightly" | "monthly" | "quarterly";
  deposit_rate: string;
  is_active: boolean;
  bands: InstalmentBand[];
}

export type RequestState =
  | "draft"
  | "open"
  | "sourcing"
  | "quoted"
  | "closed"
  | "cancelled";

export interface RequestLine {
  id: string;
  position: number;
  product_fk: string | null;
  description: string;
  specification: string | null;
  quantity: string;
  unit_fk: string | null;
  required_by: string | null;
  notes: string | null;
}

export interface QuotationRequest {
  id: string;
  number: string;
  customer_fk: string;
  contact_fk: string | null;
  customer_reference: string | null;
  received_on: string;
  required_by: string | null;
  delivery_address: string | null;
  currency_code: string;
  state: RequestState;
  cancellation_reason: string | null;
  notes: string | null;
  lines: RequestLine[];
  customer_name: string | null;
}

export interface RequestPage {
  items: QuotationRequest[];
  total: number;
  offset: number;
  limit: number;
}

export interface SupplierRequest {
  id: string;
  number: string;
  supplier_fk: string;
  request_fk: string | null;
  issued_on: string | null;
  respond_by: string | null;
  currency_code: string;
  state: "draft" | "sent" | "answered" | "closed" | "cancelled";
  notes: string | null;
  lines: {
    id: string;
    position: number;
    description: string;
    quantity: string;
  }[];
  supplier_name: string | null;
}

export interface SupplierQuotationLine {
  id: string;
  position: number;
  request_line_fk: string | null;
  product_fk: string | null;
  description: string;
  quantity_offered: string;
  unit_cost: string;
  discount_rate: string;
  lead_time_days: number | null;
  is_available: boolean;
  availability_note: string | null;
}

export interface SupplierQuotation {
  id: string;
  reference: string;
  supplier_fk: string;
  request_fk: string | null;
  received_on: string;
  valid_until: string | null;
  currency_code: string;
  exchange_rate: string;
  lead_time_days: number | null;
  freight_amount: string;
  other_charges: string;
  payment_terms: string | null;
  state: string;
  lines: SupplierQuotationLine[];
  supplier_name: string | null;
}

export interface Offer {
  supplier_quotation_id: string;
  supplier_quotation_line_id: string;
  supplier_id: string;
  supplier_name: string;
  reference: string;
  currency_code: string;
  exchange_rate: string;
  unit_cost: string;
  discount_rate: string;
  net_unit_cost: string;
  base_unit_cost: string;
  charges_per_unit: string;
  comparable_unit_cost: string;
  quantity_offered: string;
  lead_time_days: number | null;
  is_available: boolean;
  availability_note: string | null;
  valid_until: string | null;
  payment_terms: string | null;
  meets_quantity: boolean;
  minimum_order_quantity: string | null;
}

export interface Comparison {
  request_line_id: string;
  description: string;
  quantity: string;
  offers: Offer[];
  best_offer_id: string | null;
}

export type QuotationState =
  | "draft"
  | "pending_approval"
  | "issued"
  | "accepted"
  | "rejected"
  | "expired"
  | "superseded";

export interface QuotationLine {
  id: string;
  position: number;
  product_fk: string | null;
  description: string;
  specification: string | null;
  quantity: string;
  unit_net_price: string;
  list_price: string;
  discount_amount: string;
  net_price: string;
  finance_charge: string;
  payable_before_tax: string;
  tax_amount: string;
  total_payable: string;
}

export interface Quotation {
  id: string;
  number: string;
  revision: number;
  supersedes_fk: string | null;
  request_fk: string | null;
  customer_fk: string;
  customer_reference: string | null;
  state: QuotationState;
  quoted_on: string;
  valid_until: string | null;
  currency_code: string;
  precision: number;
  payment_plan: "outright" | "instalment";
  instalment_months: number | null;
  instalment_frequency: string | null;
  deposit_rate: string;
  requires_approval: boolean;
  approval_reason: string | null;
  approved_at: string | null;
  issued_at: string | null;
  accepted_on: string | null;
  decided_on: string | null;
  decision_reason: string | null;
  discount_total: string;
  net_total: string;
  finance_charge_total: string;
  payable_before_tax: string;
  tax_total: string;
  total_payable: string;
  terms: string | null;
  notes: string | null;
  lines: QuotationLine[];
  customer_name: string | null;
}

export interface QuotationPage {
  items: Quotation[];
  total: number;
  offset: number;
  limit: number;
}

export interface BuildUpStep {
  step: string;
  label: string;
  amount: string;
}

export interface LineSource {
  id: string;
  position: number;
  kind: "stock" | "supplier";
  supplier_fk: string | null;
  warehouse_fk: string | null;
  quantity: string;
  unit_cost: string;
  currency_code: string;
  exchange_rate: string;
  lead_time_days: number | null;
  selection_reason: string | null;
  supplier_name: string | null;
}

export interface LineCosting {
  position: number;
  description: string;
  quantity: string;
  supplier_cost: string;
  supplier_discount: string;
  allocated_cost: string;
  landed_cost: string;
  margin_method: string;
  margin_rate: string;
  margin_source: string;
  margin_amount: string;
  commission: string;
  commission_rate: string;
  commission_in_scope: boolean;
  commission_reason: string;
  list_price: string;
  discount: string;
  net_price: string;
  finance_charge: string;
  payable_before_tax: string;
  contribution_before_commission: string;
  contribution_after_commission: string;
  realised_margin_rate: string;
  estimated_cost: string;
  actual_cost: string;
  variance: string;
  variance_rate: string;
  steps: BuildUpStep[];
  sources: LineSource[];
}

export interface Allocation {
  line_position: number;
  expense_reference: string;
  category: string;
  method: string;
  amount: string;
}

export interface Costing {
  quotation_id: string;
  number: string;
  state: QuotationState;
  currency_code: string;
  landed_cost_total: string;
  margin_total: string;
  commission_total: string;
  list_total: string;
  discount_total: string;
  net_total: string;
  finance_charge_total: string;
  payable_before_tax: string;
  tax_total: string;
  total_payable: string;
  contribution_before_commission: string;
  contribution_after_commission: string;
  requires_approval: boolean;
  approval_reason: string | null;
  tax_by_rule: Record<string, string>;
  tax_review: string[];
  lines: LineCosting[];
  allocations: Allocation[];
}

export interface Instalment {
  id: string;
  sequence: number;
  due_on: string;
  amount: string;
  state: string;
  paid_on: string | null;
  paid_amount: string;
  is_deposit: boolean;
}

export interface Schedule {
  deposit: string;
  instalment_amount: string;
  frequency: string | null;
  duration_months: number | null;
  first_due_on: string | null;
  final_due_on: string | null;
  finance_charge: string;
  total_payable: string;
  instalments: Instalment[];
}

export interface QuotationSnapshot {
  id: string;
  revision: number;
  taken_at: string;
  content_hash: string;
  payload: Record<string, unknown>;
}

export interface SalesSummary {
  counts: Record<string, number>;
  issued_value: string;
  accepted_value: string;
  open_requests: number;
  awaiting_approval: number;
  acceptance_rate: string;
}

export type CommissionState =
  | "accrued"
  | "approved"
  | "payable"
  | "paid"
  | "cancelled";

export interface CommissionAccrual {
  id: string;
  affiliate_fk: string;
  quotation_fk: string | null;
  quotation_line_fk: string | null;
  source_reference: string | null;
  state: CommissionState;
  accrued_on: string;
  basis_amount: string;
  method: string;
  rate: string;
  accrued_amount: string;
  fulfilled_fraction: string;
  paid_fraction: string;
  entitlement: string;
  settled_amount: string;
  prior_rate: string | null;
  override_reason: string | null;
  cancellation_reason: string | null;
  payment_reference: string | null;
  affiliate_name: string | null;
  quotation_number: string | null;
}

export interface CommissionTotals {
  accrued: string;
  approved: string;
  payable: string;
  cancelled: string;
  settled: string;
  outstanding: string;
}

// -- The document designer (Phase 6) --------------------------------------------------

export interface PaletteField {
  key: string;
  label: string;
  group: string;
  group_label: string;
  kind: string;
  per_line: boolean;
  summable: boolean;
  is_internal: boolean;
}

export interface Finding {
  where: string;
  message: string;
  is_blocking: boolean;
}

export interface TemplateCondition {
  field: string;
  operator: string;
  value: string | null;
  values: string[];
}

export interface TemplateVisibility {
  mode: 'always' | 'never' | 'conditional';
  join: 'all' | 'any';
  conditions: TemplateCondition[];
}

export interface TemplateFormat {
  precision: number | null;
  currency_symbol: boolean;
  thousands_separator: boolean;
  date_format: string;
  text_case: string;
  prefix: string;
  suffix: string;
}

export interface TemplateColumn {
  id: string;
  label: string;
  binding: string | null;
  expression: string;
  width_percent: number;
  alignment: 'left' | 'centre' | 'right';
  format: TemplateFormat;
  visibility: TemplateVisibility;
  subtotal: boolean;
}

export interface TemplateElement {
  id: string;
  kind: string;
  label: string;
  binding: string | null;
  text: string;
  expression: string;
  format: TemplateFormat;
  alignment: 'left' | 'centre' | 'right';
  width_percent: number;
  emphasis: boolean;
  visibility: TemplateVisibility;
}

export interface TemplateSection {
  id: string;
  kind: string;
  title: string;
  visibility: TemplateVisibility;
  elements: TemplateElement[];
  columns: TemplateColumn[];
  bordered: boolean;
  keep_together: boolean;
  page_break_before: boolean;
}

export interface TemplatePage {
  page: string;
  landscape: boolean;
  margin_top_mm: number;
  margin_right_mm: number;
  margin_bottom_mm: number;
  margin_left_mm: number;
  base_font_size_pt: number;
  heading_font_size_pt: number;
  accent_colour: string | null;
  show_page_numbers: boolean;
  repeat_table_header: boolean;
  carry_forward_totals: boolean;
}

export interface ComposedDefinition {
  format: number;
  page: TemplatePage;
  sections: TemplateSection[];
  footer_note: string;
  options: Record<string, unknown>;
}

export interface Draft {
  template_id: string;
  type_id: string;
  version: number;
  name: string;
  is_published: boolean;
  definition: ComposedDefinition;
  findings: Finding[];
  is_publishable: boolean;
}

export interface SectionKindInfo {
  code: string;
  label: string;
}

export interface OperatorInfo {
  code: string;
  label: string;
  takes_value: boolean;
  takes_list: boolean;
}

export interface DocumentTypeField {
  id: string;
  key: string;
  label: string;
  value_kind: string;
  per_line: boolean;
  is_required: boolean;
  help_text: string | null;
  display_order: number;
}

export interface ExpressionCheck {
  is_valid: boolean;
  message: string | null;
  references: string[];
  sample: string | null;
  nodes: number;
  depth: number;
}

export interface ImportReport {
  name: string;
  kind: string;
  classification: string;
  unresolved: string[];
  findings: Finding[];
  is_acceptable: boolean;
}

// ---------------------------------------------------------------- Phase 7: orders

export interface Attachment {
  id: string;
  owner_type: string | null;
  owner_id: string | null;
  original_filename: string;
  media_type: string;
  size_bytes: number;
  content_hash: string;
  purpose: string | null;
  is_immutable: boolean;
  created_at: string;
}

export interface Acceptance {
  id: string;
  quotation_fk: string;
  method: string;
  reference: string | null;
  accepted_on: string;
  evidence_attachment_fk: string | null;
  notes: string | null;
  recorded_by_id: string | null;
}

export type SalesOrderState = 'open' | 'partially_fulfilled' | 'fulfilled' | 'cancelled';

export interface SalesOrderLine {
  id: string;
  position: number;
  product_fk: string | null;
  description: string;
  specification: string | null;
  source_kind: 'stock' | 'supplier';
  quantity_ordered: string;
  quantity_cancelled: string;
  quantity_procured: string;
  quantity_received: string;
  state: 'open' | 'backordered' | 'fulfilled' | 'cancelled';
  unit_price: string;
  line_total: string;
  tax_amount: string;
  substituted_from_product_fk: string | null;
  substitution_reason: string | null;
}

export interface SalesOrder {
  id: string;
  number: string;
  quotation_fk: string;
  acceptance_fk: string | null;
  customer_fk: string;
  customer_name: string | null;
  customer_reference: string | null;
  state: SalesOrderState;
  ordered_on: string;
  required_by: string | null;
  currency_code: string;
  precision: number;
  payment_plan: string;
  net_total: string;
  tax_total: string;
  total_payable: string;
  notes: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  lines: SalesOrderLine[];
  purchase_order_numbers: string[];
}

export interface LineVariance {
  line_id: string;
  position: number;
  description: string;
  quantity: string;
  expected_unit_cost: string;
  actual_unit_cost: string | null;
  per_unit: string | null;
  total: string | null;
}

export interface OrderVariance {
  order_id: string;
  number: string;
  lines: LineVariance[];
  expected_total: string;
  actual_total: string | null;
  variance_total: string | null;
  lines_known: number;
}

export type PurchaseOrderState =
  | 'draft'
  | 'approved'
  | 'issued'
  | 'partially_received'
  | 'received'
  | 'closed'
  | 'cancelled';

export interface PurchaseLineInput {
  description: string;
  quantity: string;
  unit_cost: string;
  product_id?: string | null;
  sales_order_line_id?: string | null;
  supplier_quotation_line_id?: string | null;
  discount_rate?: string;
  unit_id?: string | null;
  expected_on?: string | null;
  notes?: string | null;
}

export interface PurchaseOrderInput {
  supplier_id: string;
  lines: PurchaseLineInput[];
  sales_order_id?: string | null;
  supplier_quotation_id?: string | null;
  warehouse_id?: string | null;
  purpose?: 'order' | 'replenishment';
  currency_code?: string | null;
  exchange_rate?: string;
  expected_on?: string | null;
  supplier_reference?: string | null;
  notes?: string | null;
}

export interface SuggestedOrder {
  supplier_id: string;
  supplier_name: string;
  currency_code: string | null;
  exchange_rate: string;
  expected_on: string | null;
  supplier_quotation_id: string | null;
  lines: PurchaseLineInput[];
}

export interface PurchaseOrderLine {
  id: string;
  position: number;
  sales_order_line_fk: string | null;
  product_fk: string | null;
  description: string;
  quantity_ordered: string;
  quantity_received: string;
  quantity_accepted: string;
  quantity_rejected: string;
  quantity_invoiced: string;
  unit_cost: string;
  discount_rate: string;
  net_unit_cost: string;
  line_total: string;
  expected_on: string | null;
  notes: string | null;
}

export interface PurchaseOrder {
  id: string;
  number: string | null;
  supplier_fk: string;
  supplier_name: string | null;
  sales_order_fk: string | null;
  sales_order_number: string | null;
  supplier_quotation_fk: string | null;
  warehouse_fk: string | null;
  purpose: 'order' | 'replenishment';
  state: PurchaseOrderState;
  ordered_on: string;
  expected_on: string | null;
  supplier_reference: string | null;
  currency_code: string;
  exchange_rate: string;
  net_total: string;
  tax_total: string;
  total: string;
  notes: string | null;
  approved_at: string | null;
  issued_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  lines: PurchaseOrderLine[];
}

export interface ReceiptLineInput {
  purchase_order_line_id: string;
  quantity_received: string;
  quantity_rejected?: string;
  rejection_reason?: string | null;
  batch?: string | null;
  expiry?: string | null;
  serials?: string | null;
  manufacturer?: string | null;
}

export interface ReceiptInput {
  purchase_order_id: string;
  lines: ReceiptLineInput[];
  received_on?: string | null;
  warehouse_id?: string | null;
  delivery_reference?: string | null;
  inspection_pending?: boolean;
  notes?: string | null;
}

export interface ReceiptLine {
  id: string;
  position: number;
  purchase_order_line_fk: string;
  quantity_received: string;
  quantity_accepted: string;
  quantity_rejected: string;
  rejection_reason: string | null;
  unit_cost: string;
  batch: string | null;
  expiry: string | null;
  serials: string | null;
  manufacturer: string | null;
}

export interface GoodsReceipt {
  id: string;
  number: string | null;
  purchase_order_fk: string;
  purchase_order_number: string | null;
  supplier_fk: string;
  supplier_name: string | null;
  warehouse_fk: string | null;
  state: 'draft' | 'posted';
  received_on: string;
  delivery_reference: string | null;
  inspection_pending: boolean;
  accepted_value: string;
  journal_entry_fk: string | null;
  notes: string | null;
  lines: ReceiptLine[];
}

export type SupplierInvoiceState =
  | 'draft'
  | 'variance'
  | 'matched'
  | 'posted'
  | 'partially_paid'
  | 'paid'
  | 'cancelled';

export interface InvoiceLineInput {
  purchase_order_line_id: string;
  quantity: string;
  unit_cost: string;
  recoverable_tax?: string;
  non_recoverable_tax?: string;
  description?: string | null;
}

export interface InvoiceInput {
  purchase_order_id: string;
  supplier_reference: string;
  invoiced_on: string;
  lines: InvoiceLineInput[];
  due_on?: string | null;
  evidence_attachment_id?: string | null;
  notes?: string | null;
}

export interface InvoiceLine {
  id: string;
  position: number;
  purchase_order_line_fk: string;
  description: string;
  quantity: string;
  unit_cost: string;
  line_total: string;
  recoverable_tax: string;
  non_recoverable_tax: string;
}

export interface MatchVariance {
  id: string;
  invoice_fk: string;
  invoice_line_fk: string;
  kind: 'quantity' | 'price';
  expected: string;
  actual: string;
  amount_at_stake: string;
  state: 'pending' | 'approved' | 'rejected';
  decided_at: string | null;
  reason: string | null;
}

export interface SupplierInvoice {
  id: string;
  number: string | null;
  supplier_reference: string;
  supplier_fk: string;
  supplier_name: string | null;
  purchase_order_fk: string;
  purchase_order_number: string | null;
  state: SupplierInvoiceState;
  invoiced_on: string;
  due_on: string;
  currency_code: string;
  exchange_rate: string;
  net_total: string;
  recoverable_tax_total: string;
  non_recoverable_tax_total: string;
  total: string;
  base_total: string;
  amount_paid: string;
  amount_credited: string;
  evidence_attachment_fk: string | null;
  journal_entry_fk: string | null;
  notes: string | null;
  posted_at: string | null;
  lines: InvoiceLine[];
  variances: MatchVariance[];
}

export interface Tolerance {
  absolute: string;
  rate: string;
}

export interface CreditNote {
  id: string;
  supplier_reference: string;
  supplier_fk: string;
  invoice_fk: string;
  credited_on: string;
  reason: string;
  amount: string;
  base_amount: string;
  journal_entry_fk: string | null;
}

export interface PaymentInput {
  supplier_id: string;
  amount: string;
  method?: 'cash' | 'bank' | 'mobile_money';
  withheld?: string;
  paid_on?: string | null;
  reference?: string | null;
  evidence_attachment_id?: string | null;
  invoice_ids?: string[];
  notes?: string | null;
}

export interface SupplierPayment {
  id: string;
  number: string | null;
  supplier_fk: string;
  supplier_name: string | null;
  paid_on: string;
  method: string;
  reference: string | null;
  amount: string;
  withheld: string;
  evidence_attachment_fk: string | null;
  journal_entry_fk: string | null;
  notes: string | null;
  allocations: { invoice_fk: string; amount: string }[];
}

export interface PayableRow {
  invoice_id: string;
  number: string | null;
  supplier_id: string;
  supplier_name: string;
  supplier_reference: string;
  invoiced_on: string;
  due_on: string;
  total: string;
  paid: string;
  credited: string;
  outstanding: string;
  bucket: 'current' | '1_30' | '31_60' | '61_90' | 'over_90';
  days_overdue: number;
}

export interface PayablesRegister {
  as_at: string;
  rows: PayableRow[];
  total_outstanding: string;
  by_bucket: Record<string, string>;
  by_supplier: Record<string, string>;
  suggested_amounts: { amount: string; exact: boolean }[];
}

// -- Inventory --------------------------------------------------------------------------

export type StockStateName = 'usable' | 'quarantine' | 'rejected' | 'in_transit' | 'reserved';
export type ExpiryStatusName = 'none' | 'good' | 'near' | 'expired';

export interface InventorySettings {
  valuation_policy: 'weighted_average' | 'fifo';
  expiry_warning_days: number;
}

export interface StockBin {
  id: string;
  code: string;
  name: string | null;
  is_active: boolean;
}

export interface StockLocation {
  id: string;
  code: string;
  name: string;
  kind: 'storage' | 'quarantine' | 'assembly';
  is_active: boolean;
  bins: StockBin[];
}

export interface StockWarehouse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
  locations: StockLocation[];
}

export interface StockPosition {
  product_id: string;
  sku: string;
  name: string;
  warehouse_id: string;
  warehouse: string;
  usable: string;
  quarantine: string;
  rejected: string;
  in_transit: string;
  reserved: string;
  available: string;
  incoming: string;
  /** Null for anyone without the costing permission. */
  value: string | null;
  unit_cost: string | null;
  reorder_level: string | null;
  tracking: 'none' | 'batch' | 'serial';
}

export interface StockBatch {
  batch_id: string;
  batch: string;
  product_id: string;
  sku: string;
  name: string;
  warehouse: string;
  expires_on: string | null;
  quantity: string;
  status: ExpiryStatusName;
  days: number | null;
}

export interface StockMovementRow {
  id: string;
  operation_id: string;
  movement_type: string;
  state: StockStateName;
  product_id: string;
  sku: string;
  warehouse: string;
  location: string | null;
  batch: string | null;
  serial: string | null;
  quantity: string;
  value: string | null;
  reference: string | null;
  reason: string | null;
  override_reason: string | null;
  occurred_on: string;
  created_at: string;
  reversible: boolean;
}

export interface StockValuation {
  rows: {
    product_id: string;
    sku: string;
    name: string;
    warehouse: string;
    state: StockStateName;
    account: string;
    quantity: string;
    value: string;
    unit_cost: string;
  }[];
  accounts: {
    account: string;
    name: string;
    layers: string;
    ledger: string;
    difference: string;
    agrees: boolean;
  }[];
  total: string;
  agrees: boolean;
}

export interface StockReconciliation {
  movements: number;
  agrees: boolean;
  discrepancies: { record: string; key: string; expected: string; found: string }[];
}

export interface StockLineInput {
  product_id: string;
  quantity: string;
  unit_cost?: string | null;
  batch?: string | null;
  batch_id?: string | null;
  expiry?: string | null;
  manufacturer?: string | null;
  serials?: string[];
  location_id?: string | null;
  bin_id?: string | null;
}

export interface StockOperation {
  operation_id: string;
  movements: number;
  journal_entry_id: string | null;
}

export interface StockTransfer {
  id: string;
  number: string | null;
  from_warehouse: string;
  to_warehouse: string;
  state: 'draft' | 'dispatched' | 'received' | 'cancelled';
  requested_on: string;
  dispatched_on: string | null;
  received_on: string | null;
  notes: string | null;
  lines: {
    id: string;
    position: number;
    product_id: string;
    sku: string;
    name: string;
    batch: string | null;
    quantity: string;
  }[];
}

export interface StockInspection {
  id: string;
  product_id: string;
  sku: string;
  name: string;
  warehouse: string;
  batch: string | null;
  quantity_passed: string;
  quantity_failed: string;
  criteria: string | null;
  findings: string | null;
  inspected_on: string;
  inspector: string | null;
}

export interface InspectionInput {
  product_id: string;
  warehouse_id: string;
  passed: string;
  failed?: string;
  batch_id?: string | null;
  receipt_line_id?: string | null;
  serials_passed?: string[];
  serials_failed?: string[];
  criteria?: string | null;
  findings?: string | null;
  evidence_attachment_id?: string | null;
}

export interface StockReservation {
  id: string;
  product_id: string;
  sku: string;
  name: string;
  warehouse: string;
  batch: string | null;
  sales_order_line_id: string | null;
  order_number: string | null;
  quantity: string;
  quantity_consumed: string;
  quantity_released: string;
  state: 'active' | 'consumed' | 'released';
  created_at: string;
}

export interface StockCountSheet {
  id: string;
  number: string | null;
  warehouse: string;
  location: string | null;
  state: 'open' | 'submitted' | 'posted' | 'cancelled';
  counted_on: string;
  notes: string | null;
  lines: {
    id: string;
    position: number;
    product_id: string;
    sku: string;
    name: string;
    location: string | null;
    batch: string | null;
    system_quantity: string;
    counted_quantity: string | null;
    difference: string | null;
    reason: string | null;
  }[];
}

export interface LandedCharge {
  id: string;
  category: string;
  method: 'by_value' | 'by_quantity';
  reference: string | null;
  amount: string;
  to_inventory: string;
  to_cost_of_sales: string;
  created_at: string;
}

export interface AssemblyLine {
  line_id: string;
  position: number;
  description: string;
  product_id: string | null;
  sku: string | null;
  tracking: 'none' | 'batch' | 'serial';
  holds_stock: boolean;
  required: string;
  available: string;
  reserved: string;
  incoming: string;
  assembled: string;
  staged: string;
  dispatched: string;
  delivered: string;
  outstanding: string;
  to_reserve: string;
  /** Reserved and still on the shelf: what may be staged or released. */
  stageable: string;
}

export interface AssemblyWorkspace {
  order_id: string;
  order_number: string | null;
  customer: string | null;
  warehouse: string;
  ready: boolean;
  lines: AssemblyLine[];
}

export interface AssemblyStepInput {
  quantity: string;
  serials?: string[];
  override_reason?: string | null;
}

// -- Dispatch ---------------------------------------------------------------------------

export type ShipmentStateName =
  | 'prepared'
  | 'packed'
  | 'dispatched'
  | 'delivered'
  | 'partially_delivered'
  | 'failed'
  | 'cancelled';

export type DispatchModeName = 'carrier' | 'own_vehicle' | 'courier' | 'collection';

export interface ShippableLine {
  line_id: string;
  position: number;
  description: string;
  sku: string | null;
  tracking: 'none' | 'batch' | 'serial';
  holds_stock: boolean;
  ordered: string;
  delivered: string;
  outstanding: string;
  staged: string;
  shippable: string;
}

export interface DocumentLink {
  id: string;
  number: string;
  kind: string;
}

export interface ShipmentLineRow {
  id: string;
  position: number;
  sales_order_line_id: string;
  sku: string;
  description: string;
  tracking: 'none' | 'batch' | 'serial';
  quantity: string;
  delivered: string;
  returned: string;
  damaged: string;
  lost: string;
  customer_returned: string;
  serials: string[];
}

export interface Shipment {
  id: string;
  number: string;
  order_id: string;
  order_number: string | null;
  customer: string | null;
  warehouse: string;
  state: ShipmentStateName;
  state_label: string;
  mode: DispatchModeName;
  mode_label: string;
  carrier: string | null;
  driver: string | null;
  vehicle: string | null;
  destination: string | null;
  package_count: number;
  tracking_reference: string | null;
  prepared_on: string;
  packed_on: string | null;
  dispatched_on: string | null;
  delivered_on: string | null;
  notes: string | null;
  pick_list: DocumentLink | null;
  packing_list: DocumentLink | null;
  waybill: DocumentLink | null;
  delivery_note: DocumentLink | null;
  proof: {
    recipient_name: string;
    recipient_role: string | null;
    acknowledged_at: string;
    location: string | null;
    evidence_kind: string;
    has_evidence: boolean;
    evidence_hash: string | null;
  } | null;
  lines: ShipmentLineRow[];
  exceptions: {
    id: string;
    line_position: number;
    kind: 'returned' | 'damaged' | 'lost' | 'customer_return';
    kind_label: string;
    quantity: string;
    reason: string;
    replacement_owed: boolean;
    occurred_on: string;
  }[];
}

export interface DispatchInput {
  mode: DispatchModeName;
  package_count: number;
  carrier?: string | null;
  driver?: string | null;
  vehicle?: string | null;
  destination?: string | null;
  tracking_reference?: string | null;
  override_reason?: string | null;
}

export interface DeliveryOutcomeInput {
  shipment_line_id: string;
  delivered: string;
  returned?: string;
  damaged?: string;
  lost?: string;
  reason?: string | null;
  serials_delivered?: string[];
  /** Everything not delivered, under one outcome; the service works out how many. */
  remainder_as?: 'returned' | 'damaged' | 'lost' | null;
}

export interface DeliveryInput {
  outcomes: DeliveryOutcomeInput[];
  recipient_name?: string | null;
  recipient_role?: string | null;
  location?: string | null;
  comments?: string | null;
  evidence_kind?: string;
  evidence_base64?: string | null;
  media_type?: string | null;
}

// -- Point of sale ---------------------------------------------------------------------

export type TenderMethodName = 'cash' | 'mobile_money' | 'card' | 'bank_transfer' | 'on_account';
export type TenderStateName = 'pending' | 'confirmed' | 'failed' | 'abandoned';
export type SaleStateName =
  | 'open'
  | 'parked'
  | 'completed'
  | 'voided'
  | 'refunded'
  | 'partially_refunded';
export type ShiftStateName = 'open' | 'submitted' | 'closed';

export interface PosSaleLine {
  id: string;
  position: number;
  product_id: string;
  sku: string;
  description: string;
  quantity: string;
  list_price: string;
  price_source: string | null;
  unit_price: string;
  override_reason: string | null;
  discount_rate: string;
  discount: string;
  net: string;
  tax: string;
  total: string;
  returnable: string;
}

export interface PosTender {
  id: string;
  method: TenderMethodName;
  method_label: string;
  state: TenderStateName;
  state_label: string;
  amount: string;
  cash_received: string | null;
  change_given: string | null;
  provider_reference: string | null;
  failure_reason: string | null;
}

export interface PosSale {
  id: string;
  number: string | null;
  state: SaleStateName;
  customer_id: string | null;
  customer: string | null;
  parked_label: string | null;
  lines: PosSaleLine[];
  tenders: PosTender[];
  gross_total: string;
  discount_total: string;
  net_total: string;
  tax_total: string;
  total: string;
  due: string;
  change: string;
  suggested_cash: { amount: string; exact: boolean }[];
  refunded_total: string;
  awaiting_confirmation: boolean;
  completed_at: string | null;
  cashier: string | null;
  receipt_id: string | null;
  void_reason: string | null;
}

export interface PosSaleSummary {
  id: string;
  number: string | null;
  state: SaleStateName;
  customer: string | null;
  parked_label: string | null;
  total: string;
  refunded_total: string;
  items: number;
  completed_at: string | null;
  methods: string[];
}

export interface PosShift {
  id: string;
  number: string;
  register_name: string;
  cashier: string | null;
  state: ShiftStateName;
  business_day: string;
  opened_at: string;
  opening_float: string;
  counted_cash: string | null;
  expected_cash: string | null;
  variance: string | null;
  variance_reason: string | null;
  submitted_at: string | null;
  closed_at: string | null;
  approved_by: string | null;
  counted_denominations: Record<string, number> | null;
}

export interface PosShiftSummary {
  shift: PosShift;
  drawer: {
    opening_float: string;
    cash_sales: string;
    cash_in: string;
    cash_out: string;
    cash_refunds: string;
    expected: string;
  };
  sales: number;
  pending_tenders: number;
  by_method: { method: TenderMethodName; label: string; amount: string }[];
  denominations: string[];
}

export interface PosCountPreview {
  counted: string;
  expected: string;
  variance: string;
}

export interface PosRefund {
  id: string;
  number: string;
  kind: 'refund' | 'exchange' | 'void';
  sale_id: string;
  reason: string;
  method: TenderMethodName;
  restock: boolean;
  net_total: string;
  tax_total: string;
  total: string;
  requested_by: string | null;
  authorised_by: string | null;
  refunded_on: string;
  lines: { sale_line_id: string; quantity: string; net: string; tax: string }[];
}

export interface PosSupervisor {
  username: string;
  password: string;
}

export interface PosRefundInput {
  lines: { sale_line_id: string; quantity: string }[];
  reason: string;
  method?: TenderMethodName | null;
  restock: boolean;
  supervisor?: PosSupervisor | null;
}

// -- Financial management ----------------------------------------------------------------

export type FinancialAccountKindName = 'cash_drawer' | 'petty_cash' | 'bank' | 'mobile_money';

export interface FinancialAccount {
  id: string;
  kind: FinancialAccountKindName;
  kind_label: string;
  name: string;
  ledger_code: string;
  institution: string | null;
  number: string | null;
  branch_name: string | null;
  custodian: string | null;
  balance: string;
  is_active: boolean;
}

export type TreasuryMovementKindName =
  | 'transfer'
  | 'bank_charge'
  | 'interest'
  | 'adjustment_in'
  | 'adjustment_out';

export interface TreasuryMovement {
  id: string;
  number: string;
  kind: TreasuryMovementKindName;
  kind_label: string;
  occurred_on: string;
  from_account: string | null;
  to_account: string | null;
  amount: string;
  charge: string;
  reference: string | null;
  reason: string;
}

export interface Cashbook {
  account: FinancialAccount;
  starts_on: string | null;
  ends_on: string;
  opening_balance: string;
  closing_balance: string;
  total_in: string;
  total_out: string;
  lines: {
    entry_id: string;
    number: string | null;
    posting_date: string;
    narration: string;
    source: string;
    debit: string;
    credit: string;
    running_balance: string;
  }[];
}

export interface InvoiceableLine {
  line_id: string;
  description: string;
  unit_price: string;
  ordered: string;
  delivered: string;
  invoiced: string;
  invoiceable: string;
}

export interface SalesInvoiceLine {
  id: string;
  position: number;
  description: string;
  quantity: string;
  unit_price: string;
  net: string;
  tax: string;
  total: string;
  quantity_credited: string;
}

export interface SalesInvoice {
  id: string;
  number: string;
  origin: 'sales_order' | 'pos_sale';
  customer_id: string;
  customer: string | null;
  order_number: string | null;
  issued_on: string;
  due_on: string;
  net_total: string;
  tax_total: string;
  total: string;
  amount_received: string;
  amount_credited: string;
  amount_written_off: string;
  amount_provided: string;
  outstanding: string;
  state: 'open' | 'part_settled' | 'settled' | 'written_off';
  state_label: string;
  disputed: boolean;
  days_overdue: number;
  credit_override_reason: string | null;
  lines: SalesInvoiceLine[];
}

export interface CustomerReceipt {
  id: string;
  number: string;
  customer_id: string;
  customer: string | null;
  account: string | null;
  received_on: string;
  amount: string;
  allocated: string;
  refunded: string;
  unapplied: string;
  reference: string | null;
}

export interface CustomerDeposit {
  id: string;
  number: string;
  customer_id: string;
  customer: string | null;
  order_number: string | null;
  received_on: string;
  amount: string;
  applied: string;
  refunded: string;
  held: string;
  condition: 'on_invoice' | 'on_delivery';
  condition_label: string;
  state: 'held' | 'part_applied' | 'applied' | 'refunded';
}

export interface CreditNoteRecord {
  id: string;
  number: string;
  invoice_id: string;
  issued_on: string;
  reason: string;
  net: string;
  tax: string;
  total: string;
}

export interface AgeingRow {
  customer_id: string;
  customer: string;
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  over_90: string;
  total: string;
  unapplied: string;
  credit_limit: string | null;
  on_hold: boolean;
}

export interface CollectionNote {
  id: string;
  kind: 'contact' | 'promise' | 'dispute' | 'resolved';
  kind_label: string;
  note: string;
  invoice_number: string | null;
  promised_on: string | null;
  promised_amount: string | null;
  recorded_at: string;
  recorded_by: string | null;
}

export interface WorklistItem {
  invoice: SalesInvoice;
  days_overdue: number;
  outstanding: string;
  latest_note: string | null;
  latest_kind: string | null;
  promised_on: string | null;
  reminder_due: boolean;
}

export interface PartyStatement {
  party_id: string;
  party: string;
  starts_on: string;
  ends_on: string;
  opening: string;
  closing: string;
  entries: {
    posted_on: string;
    reference: string | null;
    narration: string;
    debit: string;
    credit: string;
    balance: string;
  }[];
  ageing: Record<string, string>;
}

export interface ExpenseRecord {
  id: string;
  number: string;
  payee_name: string;
  category_code: string;
  category: string | null;
  description: string;
  classification: 'operating' | 'capital';
  net: string;
  input_tax: string;
  total: string;
  incurred_on: string;
  payee_reference: string | null;
  settlement: 'paid' | 'on_credit' | 'staff_claim';
  settlement_label: string;
  account: string | null;
  employee: string | null;
  state: 'awaiting_approval' | 'recognised' | 'settled' | 'rejected';
  state_label: string;
  submitted_by: string | null;
  approved_by: string | null;
  rejection_reason: string | null;
  duplicate_of: string | null;
  duplicate_reason: string | null;
  settled_on: string | null;
}

export interface ExpenseInput {
  payee_name: string;
  category_code: string;
  description: string;
  net: string;
  settlement: 'paid' | 'on_credit' | 'staff_claim';
  input_tax?: string;
  classification?: 'operating' | 'capital';
  incurred_on?: string | null;
  payee_reference?: string | null;
  employee_id?: string | null;
  financial_account_id?: string | null;
  duplicate_reason?: string | null;
}

export interface RecurringExpenseRecord {
  id: string;
  name: string;
  payee_name: string;
  category_code: string;
  description: string;
  net: string;
  input_tax: string;
  settlement: string;
  frequency: 'weekly' | 'monthly' | 'quarterly';
  next_due: string;
  ends_on: string | null;
  is_active: boolean;
}

export interface StatementLineRecord {
  id: string;
  position: number;
  line_date: string;
  description: string;
  reference: string | null;
  amount: string;
  state: 'unmatched' | 'matched' | 'duplicate' | 'excluded';
  note: string | null;
}

export interface BankStatementRecord {
  id: string;
  account_id: string;
  account: string | null;
  reference: string;
  starts_on: string;
  ends_on: string;
  closing_balance: string;
  ledger_balance: string;
  difference: string;
  unmatched: number;
  duplicates: number;
  state: 'open' | 'reconciled';
  reconciled: boolean;
  lines: StatementLineRecord[];
}

export interface LedgerItem {
  journal_line_id: string;
  entry_number: string | null;
  posting_date: string;
  narration: string;
  reference: string | null;
  amount: string;
}

export interface SourceRow {
  source: string;
  label: string;
  records: number;
  recorded: string;
  posted: string;
  difference: string;
  unposted: number;
  agrees: boolean;
  notes: string[];
}

export interface MeasureRecord {
  code: string;
  label: string;
  basis: 'flow' | 'position';
  description: string;
  value: string;
  starts_on: string | null;
  ends_on: string;
}

export interface MeasureDrill {
  measure: MeasureRecord;
  lines: {
    entry_id: string;
    number: string | null;
    posting_date: string;
    narration: string;
    account_code: string;
    account_name: string;
    debit: string;
    credit: string;
    contribution: string;
  }[];
  total: string;
  truncated: boolean;
}

export interface Widget {
  measure: string;
  size: 'small' | 'medium' | 'large';
}

export interface CommandLayout {
  preset: string;
  widgets: Widget[];
  source: 'personal' | 'role' | 'preset';
}

export interface PaymentBatchRecord {
  id: string;
  number: string;
  state: 'awaiting_approval' | 'paid' | 'rejected';
  account: string | null;
  pay_on: string;
  items: { supplier_id: string; supplier: string; amount: string; invoice_ids: string[] }[];
  total: string;
  prepared_by: string | null;
  approved_by: string | null;
  rejection_reason: string | null;
}

// -- debt, loans and financing (PRD §25) --------------------------------------------------

export type LoanDirection = 'borrowing' | 'lending';
export type LoanTypeName =
  | 'term_loan'
  | 'overdraft'
  | 'revolving_credit'
  | 'asset_finance'
  | 'staff_advance'
  | 'related_party'
  | 'other';
export type InterestModelName =
  | 'none'
  | 'fixed'
  | 'flat'
  | 'reducing_balance'
  | 'interest_only'
  | 'balloon'
  | 'custom';
export type RepaymentFrequencyName =
  | 'daily'
  | 'weekly'
  | 'fortnightly'
  | 'monthly'
  | 'quarterly'
  | 'semi_annual'
  | 'annual';
export type RevisionKindName =
  | 'reschedule'
  | 'payment_holiday'
  | 'deferment'
  | 'interest_change'
  | 'restructure';

export interface LoanInput {
  direction: LoanDirection;
  loan_type: LoanTypeName;
  counterparty_name: string;
  principal: string;
  agreement_date: string;
  model: InterestModelName;
  frequency: RepaymentFrequencyName;
  instalments: number;
  first_due_on: string;
  annual_rate?: string;
  compounding?: RepaymentFrequencyName | null;
  grace_periods?: number;
  interest_only_periods?: number;
  balloon?: string;
  periodic_fee?: string;
  fixed_interest?: string;
  upfront_fee?: string;
  penalty_rate?: string;
  early_settlement_percent?: string;
  prepayment_allowed?: boolean;
  allocation_order?: string[];
  counterparty_party_id?: string | null;
  counterparty_employee_id?: string | null;
  agreement_reference?: string | null;
  purpose?: string | null;
  financial_account_id?: string | null;
  responsible_id?: string | null;
  notes?: string | null;
}

export interface ScheduleRow {
  id: string | null;
  sequence: number;
  due_on: string;
  opening: string;
  interest: string;
  fees: string;
  principal: string;
  capitalised: string;
  penalty: string;
  total: string;
  closing: string;
  paid: string;
  arrears: boolean;
  state: string;
  state_label: string;
}

export interface ScheduleSummary {
  principal: string;
  interest: string;
  fees: string;
  capitalised: string;
  total: string;
  instalments: number;
  final_due_on: string | null;
}

export interface SchedulePreview {
  rows: ScheduleRow[];
  summary: ScheduleSummary;
}

export interface LoanPosition {
  principal_agreed: string;
  principal_drawn: string;
  undrawn: string;
  capitalised: string;
  principal_repaid: string;
  principal_outstanding: string;
  interest_due: string;
  fees_due: string;
  penalties_due: string;
  total_outstanding: string;
  arrears: string;
  next_due_on: string | null;
  next_due_amount: string;
  days_overdue: number;
  settlement_today: string;
  ledger_principal: string;
  ledger_accrued: string;
  agrees_with_ledger: boolean;
}

export interface LoanSummary {
  id: string;
  number: string;
  direction: LoanDirection;
  direction_label: string;
  loan_type: LoanTypeName;
  loan_type_label: string;
  counterparty_name: string;
  agreement_reference: string | null;
  principal: string;
  annual_rate: string;
  model: InterestModelName;
  model_label: string;
  frequency: RepaymentFrequencyName;
  frequency_label: string;
  agreement_date: string;
  maturity_date: string | null;
  state: string;
  state_label: string;
  disputed: boolean;
  outstanding: string;
  charges: string;
  next_due_on: string | null;
  next_due_amount: string;
  days_overdue: number;
  revision_pending: boolean;
}

export interface ScheduleVersion {
  id: string;
  version: number;
  kind: string;
  kind_label: string;
  state: string;
  reason: string | null;
  effective_on: string;
  principal_basis: string;
  annual_rate: string;
  instalments: number;
  proposed_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  rows: ScheduleRow[];
  summary: ScheduleSummary;
}

export interface LoanTransactionRecord {
  id: string;
  kind: string;
  kind_label: string;
  occurred_on: string;
  amount: string;
  fee: string;
  account: string | null;
  reference: string | null;
  note: string | null;
  recorded_by: string | null;
  principal: string;
  interest: string;
  fees: string;
  penalty: string;
  journal_entry_id: string | null;
}

export interface LoanSecurityRecord {
  id: string;
  kind: string;
  kind_label: string;
  description: string;
  party_name: string | null;
  value: string | null;
  review_on: string | null;
  released_on: string | null;
}

export interface LoanDetail extends LoanSummary {
  counterparty_party_id: string | null;
  counterparty_employee_id: string | null;
  purpose: string | null;
  currency_code: string;
  disbursement_date: string | null;
  compounding: string | null;
  instalments: number;
  first_due_on: string;
  grace_periods: number;
  interest_only_periods: number;
  balloon: string;
  periodic_fee: string;
  fixed_interest: string;
  upfront_fee: string;
  penalty_rate: string;
  early_settlement_percent: string;
  prepayment_allowed: boolean;
  allocation_order: string[];
  financial_account_id: string | null;
  account: string | null;
  responsible_id: string | null;
  responsible: string | null;
  created_by: string | null;
  submitted_by: string | null;
  approved_by: string | null;
  approval_note: string | null;
  rejection_reason: string | null;
  refinances: string | null;
  refinanced_by: string | null;
  closed_on: string | null;
  closing_note: string | null;
  notes: string | null;
  position: LoanPosition;
  current: ScheduleVersion;
  proposed: ScheduleVersion | null;
  versions: ScheduleVersion[];
  transactions: LoanTransactionRecord[];
  securities: LoanSecurityRecord[];
}

export interface SettlementQuote {
  on: string;
  principal: string;
  interest_due: string;
  interest_accrued: string;
  fees_due: string;
  penalties_due: string;
  early_settlement_fee: string;
  total: string;
}

export interface LoanMovementInput {
  amount: string;
  on?: string | null;
  financial_account_id?: string | null;
  fee?: string;
  reference?: string | null;
}

export interface RevisionInput {
  kind: RevisionKindName;
  reason: string;
  effective_on: string;
  first_due_on: string;
  instalments: number;
  annual_rate?: string | null;
  model?: InterestModelName | null;
  frequency?: RepaymentFrequencyName | null;
  grace_periods?: number;
  interest_only_periods?: number;
  balloon?: string;
  periodic_fee?: string | null;
}

export interface SecurityInput {
  kind: 'collateral' | 'guarantor' | 'covenant';
  description: string;
  party_name?: string | null;
  value?: string | null;
  review_on?: string | null;
}

export interface DebtLineRecord {
  kind: string;
  kind_label: string;
  source_type: string;
  source_id: string;
  reference: string;
  counterparty: string;
  counterparty_id: string | null;
  description: string;
  original: string;
  settled: string;
  outstanding: string;
  charges: string;
  issued_on: string;
  due_on: string | null;
  standing: string;
  standing_label: string;
  bucket: string;
  responsible: string | null;
}

export interface DebtSplit {
  code: string;
  label: string;
  amount: string;
  count: number;
}

export interface DebtCounterparty {
  name: string;
  amount: string;
  count: number;
}

export interface LoanBook {
  principal: string;
  charges: string;
  total: string;
  count: number;
}

export interface DebtTrendPoint {
  month: string;
  collected: string;
  settled: string;
  borrowed: string;
  lent: string;
}

export interface DebtSummary {
  on: string;
  receivables: string;
  receivables_by_kind: DebtSplit[];
  payables: string;
  payables_by_kind: DebtSplit[];
  loans_receivable: LoanBook;
  loans_payable: LoanBook;
  receivable_due_soon: string;
  receivable_overdue: string;
  payable_due_soon: string;
  payable_overdue: string;
  collected: string;
  repaid: string;
  receivable_ageing: DebtSplit[];
  payable_ageing: DebtSplit[];
  largest_debtors: DebtCounterparty[];
  largest_creditors: DebtCounterparty[];
  debt_service: string;
  cash_available: string;
  coverage: string | null;
  window_days: number;
  period_start: string;
  period_end: string;
  trend: DebtTrendPoint[];
}

export interface CommitmentRecord {
  due_on: string;
  inward: boolean;
  kind: string;
  kind_label: string;
  reference: string;
  counterparty: string;
  amount: string;
  source_type: string;
  source_id: string;
}

export interface MaturityRecord {
  code: string;
  label: string;
  borrowed: string;
  lent: string;
}

export interface CashClassRecord {
  code: string;
  label: string;
  category: string;
  inflow: string;
  outflow: string;
  net: string;
}

export interface OtherDebtInput {
  owed_to_us: boolean;
  counterparty_name: string;
  description: string;
  amount: string;
  financial_account_id: string;
  issued_on?: string | null;
  due_on?: string | null;
  reference?: string | null;
}

export interface OtherDebtRecord {
  id: string;
  number: string;
  owed_to_us: boolean;
  counterparty_name: string;
  description: string;
  original: string;
  settled: string;
  outstanding: string;
  issued_on: string;
  due_on: string | null;
}

// -- budgets, forecasts, profitability and charts (PRD §20.7, §29.6, §29.8) ----------------

export type FigureBasis = 'actual' | 'budget' | 'forecast' | 'estimate';

export interface AnalysisCode {
  id: string;
  kind: 'department' | 'project' | 'cost_centre';
  kind_label: string;
  code: string;
  name: string;
  is_active: boolean;
}

export interface BudgetPeriod {
  key: string;
  label: string;
  starts_on: string;
  ends_on: string;
}

export interface BudgetLineRecord {
  id: string;
  position: number;
  account_code: string | null;
  group_code: string | null;
  label: string;
  branch_id: string | null;
  analysis_code_id: string | null;
  analysis: string | null;
  amounts: Record<string, string>;
  total: string;
  note: string | null;
}

export interface BudgetSummary {
  id: string;
  name: string;
  period_kind: 'annual' | 'quarterly' | 'monthly' | 'custom';
  starts_on: string;
  ends_on: string;
  state: 'draft' | 'in_review' | 'approved' | 'superseded' | 'rejected';
  state_label: string;
  version: number;
  total: string;
  lines: number;
  owner: string | null;
  approved_by: string | null;
}

export interface BudgetRequestRecord {
  id: string;
  budget_id: string;
  kind: 'transfer' | 'overspend';
  line_id: string;
  from_line_id: string | null;
  period_key: string;
  amount: string;
  reason: string;
  state: 'pending' | 'approved' | 'rejected';
  expense_id: string | null;
  requested_by: string | null;
  decided_by: string | null;
  decision_note: string | null;
}

export interface BudgetDetail extends BudgetSummary {
  branch_id: string | null;
  owner_id: string | null;
  assumptions: string | null;
  periods: BudgetPeriod[];
  budget_lines: BudgetLineRecord[];
  decisions: { action: string; actor: string | null; note: string | null; occurred_at: string; version: number }[];
  versions: BudgetSummary[];
  requests: BudgetRequestRecord[];
}

export interface BudgetInput {
  name: string;
  period_kind: BudgetSummary['period_kind'];
  starts_on: string;
  ends_on: string;
  branch_id?: string | null;
  assumptions?: string | null;
}

export interface BudgetLineInput {
  account_code?: string | null;
  group_code?: string | null;
  analysis_code_id?: string | null;
  amounts?: Record<string, string>;
  total?: string | null;
  note?: string | null;
}

export interface ComparisonCell {
  period_key: string;
  original: string;
  budget: string;
  actual: string;
  prior: string;
  variance: {
    absolute: string;
    percent: string | null;
    favourable: boolean | null;
    utilisation: string | null;
  };
}

export interface BudgetComparison {
  budget_id: string;
  as_of: string;
  periods: BudgetPeriod[];
  lines: {
    line_id: string;
    label: string;
    nature: 'income' | 'cost';
    analysis: string | null;
    cells: ComparisonCell[];
    total: ComparisonCell;
    to_date: ComparisonCell;
    forecast: string;
  }[];
  income: ComparisonCell;
  costs: ComparisonCell;
  income_to_date: ComparisonCell;
  costs_to_date: ComparisonCell;
  bases: Record<string, string>;
}

export interface PostingRecord {
  posting_date: string;
  entry_id: string;
  number: string | null;
  account_code: string;
  account_name: string;
  narration: string | null;
  amount: string;
}

export interface ForecastAssumptions {
  collection_delay_days: number;
  collection_rate: string;
  payment_delay_days: number;
  include_recurring: boolean;
  include_overdue_payables: boolean;
  extra_flows: { on: string; amount: string; inward: boolean; label: string }[];
  settle_loan_id: string | null;
  settle_on: string | null;
}

export interface ForecastScenario {
  id: string;
  name: string;
  kind: 'best' | 'base' | 'downside' | 'custom';
  kind_label: string;
  is_default: boolean;
  assumptions: ForecastAssumptions;
  notes: string | null;
}

export interface CashForecast {
  basis: FigureBasis;
  basis_label: string;
  scenario: string;
  scenario_kind: string;
  assumptions: ForecastAssumptions;
  starts_on: string;
  ends_on: string;
  opening_cash: string;
  closing_cash: string;
  lowest: string;
  lowest_on: string;
  threshold: string | null;
  bucket_days: number;
  rows: {
    starts_on: string;
    ends_on: string;
    opening: string;
    inflow: string;
    outflow: string;
    closing: string;
    by_class: Record<string, string>;
    below_threshold: boolean;
  }[];
  totals: Record<string, string>;
  class_labels: Record<string, string>;
  items: {
    on: string;
    cls: string;
    cls_label: string;
    inward: boolean;
    amount: string;
    reference: string;
    counterparty: string | null;
    source_type: string;
    source_id: string | null;
  }[];
  warnings: string[];
}

export interface PlannedCashItem {
  id: string;
  expected_on: string;
  amount: string;
  inward: boolean;
  label: string;
  scenario_id: string | null;
}

export interface AlertRecord {
  severity: 'critical' | 'warning' | 'notice';
  kind: string;
  title: string;
  detail: string;
  amount: string | null;
  count: number;
  view: string;
}

export interface ChartSeries {
  key: string;
  label: string;
  basis: FigureBasis;
  basis_label: string;
  role: 'primary' | 'comparison' | 'supporting' | 'level';
}

export interface ChartPoint {
  key: string;
  label: string;
  values: Record<string, string>;
  drill: Record<string, string>;
}

export interface ChartData {
  id: string;
  title: string;
  kind: 'line' | 'columns' | 'bars' | 'grouped_bars';
  unit: string;
  starts_on: string;
  ends_on: string;
  interval: string | null;
  series: ChartSeries[];
  points: ChartPoint[];
  note: string | null;
}

export interface ProfitRow {
  key: string;
  label: string;
  lines: number;
  revenue: string;
  cost: string;
  gross: string;
  commission: string;
  after_commission: string;
  direct: string;
  overheads: string;
  after_allocation: string;
  margin: string | null;
  pending_lines: number;
  pending_revenue: string;
}

export interface ProfitabilityReport {
  basis: FigureBasis;
  basis_label: string;
  dimension: string;
  dimension_label: string;
  starts_on: string;
  ends_on: string;
  rows: ProfitRow[];
  total: ProfitRow;
  allocated: boolean;
  direct_pool: string;
  overhead_pool: string;
}

export interface SaleFact {
  source_type: string;
  source_id: string;
  reference: string;
  on: string;
  description: string;
  channel: string;
  quantity: string;
  revenue: string;
  cost: string | null;
  commission: string;
}

export interface ErosionReport {
  rows: {
    order_id: string;
    order_number: string;
    order_line_id: string;
    customer: string;
    description: string;
    supplier: string;
    quantity_quoted: string;
    quantity_invoiced: string;
    quoted_margin: string;
    realised_margin: string;
    difference: string;
    quoted_cost: string;
    actual_cost: string | null;
    causes: Record<string, string>;
    complete: boolean;
  }[];
  totals: Record<string, string>;
  cause_labels: Record<string, string>;
  quoted_margin: string;
  realised_margin: string;
  pending: number;
  bases: Record<string, string>;
}
