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
