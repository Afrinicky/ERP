/**
 * First-run provisioning.
 *
 * Administrator provisioning is completed before any business data may be entered, per
 * PRD 9.6. The house palette is offered first and chosen unless another is picked, per
 * ADR-0002.
 */

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { Preset, SetupPayload } from '@/api/types';
import { Mark } from '@/components/brand';
import {
  Alert,
  Button,
  Card,
  Field,
  Select,
  Skeleton,
  TextInput,
} from '@/components/primitives';
import { describe, fieldError } from '@/lib/errors';
import { installTheme } from '@/lib/session';
import { PRESETS } from '@/tokens';

type Step = 'business' | 'appearance' | 'administrator';

const STEPS: { id: Step; label: string }[] = [
  { id: 'business', label: 'Business' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'administrator', label: 'Administrator' },
];

const TIMEZONES = [
  'Africa/Accra',
  'Africa/Lagos',
  'Africa/Nairobi',
  'Africa/Johannesburg',
  'Europe/London',
  'UTC',
];

const CURRENCIES: { code: string; symbol: string; name: string }[] = [
  { code: 'GHS', symbol: 'GH₵', name: 'Ghana cedi' },
  { code: 'NGN', symbol: '₦', name: 'Nigerian naira' },
  { code: 'KES', symbol: 'KSh', name: 'Kenyan shilling' },
  { code: 'ZAR', symbol: 'R', name: 'South African rand' },
  { code: 'USD', symbol: '$', name: 'United States dollar' },
  { code: 'EUR', symbol: '€', name: 'Euro' },
  { code: 'GBP', symbol: '£', name: 'Pound sterling' },
];

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const status = useQuery({ queryKey: ['setup'], queryFn: api.setup.status });

  const [step, setStep] = useState<Step>('business');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmPassword, setConfirmPassword] = useState('');
  const [mismatch, setMismatch] = useState<string | null>(null);

  const [form, setForm] = useState<SetupPayload>({
    business_name: '',
    administrator_username: '',
    administrator_full_name: '',
    administrator_password: '',
    theme_preset: '',
    base_currency: 'GHS',
    currency_symbol: 'GH₵',
    currency_precision: 2,
    timezone: 'Africa/Accra',
    financial_year_start_month: 1,
    branch_code: 'main',
    branch_name: 'Head office',
    warehouse_code: 'main',
    warehouse_name: 'Main store',
  });

  function set<K extends keyof SetupPayload>(key: K, value: SetupPayload[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  // The recommended preset is chosen as soon as the list arrives, and previewed, so
  // the appearance step opens on the house palette rather than on nothing. Choosing
  // another replaces it; the choice is never made on the administrator's behalf
  // without being shown.
  const recommended = (status.data?.presets ?? []).find((preset) => preset.recommended);
  useEffect(() => {
    if (recommended && form.theme_preset === '') choosePreset(recommended);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recommended?.code]);

  function choosePreset(preset: Preset) {
    set('theme_preset', preset.code);
    // Preview the choice immediately, so the decision is made on what it looks like.
    const definition = PRESETS[preset.code as keyof typeof PRESETS];
    if (definition) {
      installTheme({
        preset_code: definition.code,
        preset_name: definition.name,
        preset_source: definition.source,
        colour: definition.colour,
        overrides: {},
        customisable: [],
        dark_mode_enabled: false,
      });
    }
  }

  async function submit() {
    setError(null);
    setMismatch(null);
    if (form.administrator_password !== confirmPassword) {
      setMismatch('The two passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await api.setup.provision(form);
      onComplete();
    } catch (caught) {
      setError(caught);
      const problem = describe(caught);
      if (['business_name', 'base_currency', 'timezone'].includes(problem.field ?? '')) {
        setStep('business');
      } else if (problem.field === 'preset') {
        setStep('appearance');
      }
    } finally {
      setBusy(false);
    }
  }

  const businessReady = form.business_name.trim().length > 0;
  const appearanceReady = form.theme_preset.length > 0;
  const administratorReady =
    form.administrator_username.trim().length >= 3 &&
    form.administrator_full_name.trim().length > 0 &&
    form.administrator_password.length > 0 &&
    confirmPassword.length > 0;

  return (
    <div className="centred">
      <div className="centred__panel centred__panel--wide">
        <div className="brand-lockup">
          <span className="brand-lockup__mark">
            <Mark size={40} title="Nickland" />
          </span>
          <span className="brand-lockup__text">
            <span className="brand-lockup__product">Nickland ERP</span>
            <span className="brand-lockup__edition">Initial configuration</span>
          </span>
        </div>

        <nav className="wizard-steps" aria-label="Setup progress">
          {STEPS.map((item, index) => {
            const position = STEPS.findIndex((candidate) => candidate.id === step);
            const state = index === position ? 'current' : index < position ? 'done' : 'pending';
            return (
              <span key={item.id} className="row gap-2">
                {index > 0 ? <span className="wizard-step__rule" aria-hidden="true" /> : null}
                <span className="wizard-step" data-state={state}>
                  <span className="wizard-step__index">{index + 1}</span>
                  {item.label}
                </span>
              </span>
            );
          })}
        </nav>

        {error ? (
          <div style={{ paddingBottom: 'var(--space-4)' }}>
            <Alert tone="error" title="This configuration was not accepted">
              {describe(error).message}
            </Alert>
          </div>
        ) : null}

        {step === 'business' ? (
          <Card
            title="Business particulars"
            subtitle="These appear on every document the business issues."
            footer={
              <Button tone="primary" disabled={!businessReady} onClick={() => setStep('appearance')}>
                Continue
              </Button>
            }
          >
            <div className="form-grid">
              <Field
                label="Business name"
                required
                full
                error={fieldError(error, 'business_name')}
                hint="As it should appear on documents."
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    value={form.business_name}
                    onChange={(event) => set('business_name', event.target.value)}
                    autoFocus
                  />
                )}
              </Field>

              <Field label="Registered legal name" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={form.legal_name ?? ''}
                    onChange={(event) => set('legal_name', event.target.value)}
                  />
                )}
              </Field>

              <Field label="Tax identification number" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={form.tax_identifier ?? ''}
                    onChange={(event) => set('tax_identifier', event.target.value)}
                  />
                )}
              </Field>

              <Field label="Electronic mail address" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    type="email"
                    value={form.email ?? ''}
                    onChange={(event) => set('email', event.target.value)}
                  />
                )}
              </Field>

              <Field label="Telephone" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    value={form.telephone ?? ''}
                    onChange={(event) => set('telephone', event.target.value)}
                  />
                )}
              </Field>

              <Field
                label="Base currency"
                required
                hint="All accounting is conducted in this currency. It cannot be changed later."
                error={fieldError(error, 'base_currency')}
              >
                {(id, describedBy) => (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    value={form.base_currency}
                    onChange={(event) => {
                      const chosen = CURRENCIES.find((item) => item.code === event.target.value);
                      set('base_currency', event.target.value);
                      if (chosen) set('currency_symbol', chosen.symbol);
                    }}
                  >
                    {CURRENCIES.map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.name} ({item.code})
                      </option>
                    ))}
                  </Select>
                )}
              </Field>

              <Field label="Time zone" required>
                {(id) => (
                  <Select
                    id={id}
                    value={form.timezone}
                    onChange={(event) => set('timezone', event.target.value)}
                  >
                    {TIMEZONES.map((zone) => (
                      <option key={zone} value={zone}>
                        {zone}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>

              <Field label="Financial year commences" required>
                {(id) => (
                  <Select
                    id={id}
                    value={String(form.financial_year_start_month)}
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
            </div>
          </Card>
        ) : null}

        {step === 'appearance' ? (
          <Card
            title="Appearance"
            subtitle="The house palette is chosen. Any may be amended afterwards under Settings."
            footer={
              <>
                <Button tone="plain" onClick={() => setStep('business')}>
                  Back
                </Button>
                <Button
                  tone="primary"
                  disabled={!appearanceReady}
                  onClick={() => setStep('administrator')}
                >
                  Continue
                </Button>
              </>
            }
          >
            {status.isLoading ? (
              <Skeleton rows={2} height={120} />
            ) : (
              <div className="preset-choice">
                {(status.data?.presets ?? []).map((preset) => {
                  const definition = PRESETS[preset.code as keyof typeof PRESETS];
                  return (
                    <button
                      key={preset.code}
                      type="button"
                      className="preset-card"
                      aria-pressed={form.theme_preset === preset.code}
                      onClick={() => choosePreset(preset)}
                    >
                      <span className="preset-card__swatches" aria-hidden="true">
                        <span
                          className="preset-card__swatch preset-card__swatch--wide"
                          style={{ background: definition?.colour['surface-navigation'] }}
                        />
                        <span
                          className="preset-card__swatch"
                          style={{ background: definition?.colour['accent-primary'] }}
                        />
                        <span
                          className="preset-card__swatch"
                          style={{ background: definition?.colour['accent-secondary'] }}
                        />
                        <span
                          className="preset-card__swatch preset-card__swatch--wide"
                          style={{ background: definition?.colour['surface-canvas'] }}
                        />
                      </span>
                      <span className="preset-card__name">
                        {preset.name}
                        {preset.recommended ? (
                          <span className="preset-card__badge">Recommended</span>
                        ) : null}
                      </span>
                      <span className="preset-card__detail">{preset.description}</span>
                      <span className="preset-card__source">{preset.source}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </Card>
        ) : null}

        {step === 'administrator' ? (
          <Card
            title="Administrator account"
            subtitle="This account holds every permission. Further users are created afterwards."
            footer={
              <>
                <Button tone="plain" onClick={() => setStep('appearance')} disabled={busy}>
                  Back
                </Button>
                <Button
                  tone="primary"
                  busy={busy}
                  disabled={!administratorReady}
                  onClick={() => void submit()}
                >
                  Complete configuration
                </Button>
              </>
            }
          >
            <div className="form-grid">
              <Field
                label="Full name"
                required
                error={fieldError(error, 'full_name')}
              >
                {(id) => (
                  <TextInput
                    id={id}
                    value={form.administrator_full_name}
                    onChange={(event) => set('administrator_full_name', event.target.value)}
                    autoFocus
                  />
                )}
              </Field>

              <Field
                label="Username"
                required
                hint="Three to thirty-two characters."
                error={fieldError(error, 'username')}
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    value={form.administrator_username}
                    onChange={(event) =>
                      set('administrator_username', event.target.value.toLowerCase())
                    }
                    autoComplete="username"
                  />
                )}
              </Field>

              <Field label="Electronic mail address" optional>
                {(id) => (
                  <TextInput
                    id={id}
                    type="email"
                    value={form.administrator_email ?? ''}
                    onChange={(event) => set('administrator_email', event.target.value)}
                  />
                )}
              </Field>

              <Field
                label="Password"
                required
                hint={status.data?.password_requirements.join(' · ')}
                error={fieldError(error, 'password')}
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    type="password"
                    aria-describedby={describedBy}
                    value={form.administrator_password}
                    onChange={(event) => set('administrator_password', event.target.value)}
                    autoComplete="new-password"
                  />
                )}
              </Field>

              <Field label="Confirm password" required error={mismatch}>
                {(id) => (
                  <TextInput
                    id={id}
                    type="password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    autoComplete="new-password"
                  />
                )}
              </Field>

              <div className="form-grid__full">
                <Alert tone="info" title="What this creates">
                  One business with a primary branch and a default store, the eight roles of the
                  requirements, a numbering sequence for each document type, and this
                  administrator account.
                </Alert>
              </div>
            </div>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
