/**
 * Appearance.
 *
 * Two presets ship as equals, per ADR-0002. An override is measured against WCAG AA as
 * the administrator works, and a failing combination is refused on save, per PRD 28.2.
 * Dependent tokens — hover, active, surface, focus ring, chart series and the text that
 * sits on the accent — are derived by the service.
 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Preset, Theme } from '@/api/types';
import {
  Alert,
  Button,
  Card,
  EmptyState,
  FormRow,
  PickerList,
  PickerOption,
  RadioGroup,
  SearchField,
  SectionHeader,
  Skeleton,
  StatusChip,
  TextInput,
  TotalDisplay,
  useToast,
} from '@/components/primitives';
import { describe } from '@/lib/errors';
import { humanise, ratio } from '@/lib/format';
import { installTheme, useCurrency, useSession } from '@/lib/session';
export function Appearance() {
  const { theme, applyTheme, can } = useSession();
  const currencySymbol = useCurrency().symbol;
  const client = useQueryClient();
  const toast = useToast();
  const presets = useQuery({ queryKey: ['presets'], queryFn: api.appearance.presets });
  const [presetCode, setPresetCode] = useState(theme?.preset_code ?? '');
  const [overrides, setOverrides] = useState<Record<string, string>>(theme?.overrides ?? {});
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!theme) return;
    setPresetCode(theme.preset_code);
    setOverrides(theme.overrides);
  }, [theme]);
  const preview = useQuery({
    queryKey: ['appearance', 'preview', presetCode, overrides],
    queryFn: () => api.appearance.preview(presetCode, overrides),
    enabled: Boolean(presetCode) && can('theme.manage'),
  });
  /** Show the proposed theme on screen, so the decision is made on what it looks like. */
  useEffect(() => {
    if (preview.data) installTheme(preview.data.theme);
  }, [preview.data]);
  /** Restore the saved theme when leaving, so an abandoned preview does not persist. */
  useEffect(
    () => () => {
      if (theme) installTheme(theme);
    },
    [theme],
  );
  const apply = useMutation({
    mutationFn: () => api.appearance.apply(presetCode, overrides),
    onSuccess: (saved: Theme) => {
      applyTheme(saved);
      void client.invalidateQueries({ queryKey: ['appearance'] });
      toast.show('success', 'Appearance saved.');
    },
    onError: (error) => toast.show('error', describe(error).message),
  });
  const reset = useMutation({
    mutationFn: api.appearance.reset,
    onSuccess: (saved: Theme) => {
      applyTheme(saved);
      setOverrides({});
      setDraft({});
      void client.invalidateQueries({ queryKey: ['appearance'] });
      toast.show('success', 'Preset defaults restored.');
    },
    onError: (error) => toast.show('error', describe(error).message),
  });
  const dirty = useMemo(
    () =>
      presetCode !== theme?.preset_code ||
      JSON.stringify(overrides) !== JSON.stringify(theme?.overrides ?? {}),
    [presetCode, overrides, theme],
  );
  const failures = preview.data?.audit.filter((line) => !line.passes) ?? [];
  const colours = preview.data?.theme.colour ?? theme?.colour ?? {};
  function setColour(token: string, value: string) {
    setDraft((current) => ({ ...current, [token]: value }));
    if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value)) {
      setOverrides((current) => ({ ...current, [token]: value.toUpperCase() }));
    }
  }
  function clearColour(token: string) {
    setOverrides((current) => {
      const next = { ...current };
      delete next[token];
      return next;
    });
    setDraft((current) => {
      const next = { ...current };
      delete next[token];
      return next;
    });
  }
  if (!can('theme.manage')) {
    return (
      <EmptyState
        title="Appearance is managed by an administrator"
        detail="You do not hold the permission to amend the appearance of this installation."
      />
    );
  }
  return (
    <div className="settings__panel">
      <SectionHeader
        title="Appearance"
        detail="The palette applies to every screen, every document and the point of sale."
        actions={
          <>
            <Button
              tone="plain"
              onClick={() => reset.mutate()}
              busy={reset.isPending}
              disabled={Object.keys(theme?.overrides ?? {}).length === 0}
            >
              Restore defaults
            </Button>
            <Button
              tone="primary"
              onClick={() => apply.mutate()}
              busy={apply.isPending}
              disabled={!dirty || failures.length > 0}
            >
              Save appearance
            </Button>
          </>
        }
      />
      {failures.length > 0 ? (
        <Alert tone="error" title="These combinations are not legible enough to save">
          {failures.map((line) => `${line.label}: ${ratio(line.ratio)}, requires ${ratio(line.required)}`).join('\n')}
        </Alert>
      ) : null}
      <Card
        title="Palette"
        subtitle="Four palettes are supplied. Any may be amended, and reset afterwards."
      >
        {presets.isLoading ? (
          <Skeleton rows={2} height={120} />
        ) : (
          <div className="preset-choice">
            {(presets.data ?? []).map((preset: Preset) => (
              <button
                key={preset.code}
                type="button"
                className="preset-card"
                aria-pressed={presetCode === preset.code}
                onClick={() => {
                  setPresetCode(preset.code);
                  setOverrides({});
                  setDraft({});
                }}
              >
                <span className="preset-card__swatches" aria-hidden="true">
                  <PresetSwatches code={preset.code} />
                </span>
                <span className="preset-card__name">{preset.name}</span>
                <span className="preset-card__detail">{preset.description}</span>
                <span className="preset-card__source">Specified in {preset.source}</span>
              </button>
            ))}
          </div>
        )}
      </Card>
      <Card
        title="Brand colours"
        subtitle="The remaining shades, and the text that sits upon them, are derived from these."
      >
        <div className="stack">
          {(theme?.customisable ?? []).map((token) => {
            const current = draft[token] ?? overrides[token] ?? colours[token] ?? '#000000';
            const overridden = token in overrides;
            return (
              <div className="colour-row" key={token}>
                <div className="stack gap-1">
                  <span className="colour-row__name">{humanise(token)}</span>
                  <span className="colour-row__token">--colour-{token}</span>
                </div>
                <div className="row gap-2">
                  <input
                    type="color"
                    className="colour-swatch"
                    value={/^#[0-9a-fA-F]{6}$/.test(current) ? current : '#000000'}
                    onChange={(event) => setColour(token, event.target.value.toUpperCase())}
                    aria-label={`${humanise(token)} colour`}
                  />
                  <TextInput
                    className="colour-input"
                    value={current}
                    onChange={(event) => setColour(token, event.target.value)}
                    aria-label={`${humanise(token)} hexadecimal value`}
                    spellCheck={false}
                  />
                </div>
                <Button
                  tone="plain"
                  size="compact"
                  disabled={!overridden}
                  onClick={() => clearColour(token)}
                >
                  Revert
                </Button>
              </div>
            );
          })}
        </div>
      </Card>
      <Card
        title="Legibility audit"
        subtitle="Every pairing is measured against WCAG AA before the palette can be saved."
        actions={
          preview.data ? (
            <StatusChip tone={preview.data.passes ? 'ok' : 'error'}>
              {preview.data.passes
                ? `${preview.data.audit.length} pairings pass`
                : `${failures.length} of ${preview.data.audit.length} fail`}
            </StatusChip>
          ) : null
        }
      >
        {preview.isLoading ? (
          <Skeleton rows={6} height={16} />
        ) : preview.data ? (
          <div className="audit-list">
            {preview.data.audit.map((line) => (
              <div className="audit-row" key={line.label}>
                <span>{line.label}</span>
                <span
                  className="audit-row__pair"
                  style={{ color: line.foreground, background: line.background }}
                  aria-hidden="true"
                >
                  Abc
                </span>
                <span className="audit-row__ratio">{ratio(line.ratio)}</span>
                <StatusChip tone={line.passes ? 'ok' : 'error'}>
                  {line.passes ? 'Passes' : `Needs ${ratio(line.required)}`}
                </StatusChip>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="Select a palette to measure it" />
        )}
      </Card>
      <Card
        title="Specimen"
        subtitle="How the palette reads across the interface and at the till."
      >
        <div className="stack gap-5">
          <div className="row gap-3 row--wrap">
            <Button tone="primary">Issue quotation</Button>
            <Button tone="secondary">Save draft</Button>
            <Button tone="plain">Cancel</Button>
            <Button tone="destructive">Reverse entry</Button>
          </div>
          <div className="row gap-2 row--wrap">
            <StatusChip tone="ok">Posted</StatusChip>
            <StatusChip tone="warning">Pending approval</StatusChip>
            <StatusChip tone="error">Overdue</StatusChip>
            <StatusChip tone="info">Awaiting confirmation</StatusChip>
            <StatusChip tone="neutral">Draft</StatusChip>
            <StatusChip tone="accent">Selected</StatusChip>
          </div>
          <div className="table-frame" style={{ border: '1px solid var(--colour-border-default)' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Description</th>
                  <th className="numeric">Quantity</th>
                  <th className="numeric">Unit price</th>
                  <th className="numeric">Line total</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Specimen line item</td>
                  <td className="numeric">10</td>
                  <td className="numeric">120.0000</td>
                  <td className="numeric">1 200.0000</td>
                </tr>
                <tr>
                  <td>Allocated transport</td>
                  <td className="numeric">—</td>
                  <td className="numeric">—</td>
                  <td className="numeric">160.0000</td>
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Total payable</td>
                  <td className="numeric">1 360.0000</td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="stack gap-4">
            <p className="label">At the till</p>
            <div
              className="stack gap-4"
              style={{
                padding: 'var(--space-5)',
                background: 'var(--colour-surface-canvas)',
                border: '1px solid var(--colour-border-default)',
                borderRadius: 'var(--radius-lg)',
              }}
            >
              <div className="row row--between gap-6 row--wrap" style={{ alignItems: 'flex-start' }}>
                <div className="grow" style={{ minWidth: 420, maxWidth: 620 }}>
                  <FormRow label="Product" required>
                    {() => (
                      <>
                        <SearchField
                          label="Specimen product search"
                          value=""
                          placeholder="Enter name or barcode of product"
                          onChange={() => undefined}
                        />
                        <PickerList>
                          <PickerOption
                            label="Nitrile examination glove, medium"
                            secondary="GLV-NITRILE-M"
                            onSelect={() => undefined}
                          />
                          <PickerOption
                            label="Disposable syringe, 10 ml"
                            unavailable="Out of stock"
                            secondary="SYR-10ML"
                            onSelect={() => undefined}
                          />
                          <PickerOption
                            label="Analyser calibration"
                            unavailable="Service — not stocked"
                            secondary="SRV-CALIB"
                            onSelect={() => undefined}
                          />
                        </PickerList>
                      </>
                    )}
                  </FormRow>
                </div>

                <div className="stack gap-4" style={{ flex: '0 0 auto', minWidth: 240 }}>
                  <TotalDisplay value={`${currencySymbol} 1 360.00`} caption="3 lines" />
                  <RadioGroup
                    legend="Tender"
                    value="cash"
                    onChange={() => undefined}
                    options={[
                      { value: 'cash', label: 'Cash' },
                      { value: 'account', label: 'On account' },
                    ]}
                  />
                </div>
              </div>

              <p className="caption">
                An unavailable item is listed with the reason stated inline, rather than
                hidden: an operator would otherwise search for something that is in fact
                there.
              </p>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
function PresetSwatches({ code }: { code: string }) {
  const preview = useQuery({
    queryKey: ['appearance', 'swatches', code],
    queryFn: () => api.appearance.preview(code, {}),
    staleTime: Infinity,
  });
  const colour = preview.data?.theme.colour;
  return (
    <>
      <span
        className="preset-card__swatch preset-card__swatch--wide"
        style={{ background: colour?.['surface-navigation'] }}
      />
      <span className="preset-card__swatch" style={{ background: colour?.['accent-primary'] }} />
      <span className="preset-card__swatch" style={{ background: colour?.['accent-secondary'] }} />
      <span
        className="preset-card__swatch preset-card__swatch--wide"
        style={{ background: colour?.['surface-canvas'] }}
      />
    </>
  );
}
