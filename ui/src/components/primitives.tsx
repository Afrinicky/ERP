/**
 * Component library.
 *
 * Every component reads the semantic token layer through its class names. No colour,
 * font, spacing or radius literal appears here.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';

/* ------------------------------------------------------------------ Button */

type ButtonTone = 'primary' | 'secondary' | 'plain' | 'destructive';
type ControlSize = 'compact' | 'standard' | 'touch';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone;
  size?: ControlSize;
  block?: boolean;
  busy?: boolean;
  icon?: ReactNode;
}

export function Button({
  tone = 'secondary',
  size = 'standard',
  block,
  busy,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  const classes = [
    'button',
    `button--${tone}`,
    size === 'compact' ? 'button--compact' : '',
    size === 'touch' ? 'button--touch' : '',
    block ? 'button--block' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button className={classes} disabled={disabled || busy} aria-busy={busy} {...rest}>
      {busy ? <span className="button__spinner" aria-hidden="true" /> : icon}
      {children}
    </button>
  );
}

/**
 * A navigation link presented as a button.
 *
 * Used where the action is navigation: a button wrapping a link is neither, and reads
 * incorrectly to assistive technology.
 */
export function LinkButton({
  tone = 'secondary',
  size = 'standard',
  children,
  ...rest
}: {
  tone?: ButtonTone;
  size?: ControlSize;
  children: ReactNode;
} & AnchorHTMLAttributes<HTMLAnchorElement>) {
  const classes = [
    'button',
    `button--${tone}`,
    size === 'compact' ? 'button--compact' : '',
    size === 'touch' ? 'button--touch' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <a className={classes} {...rest}>
      {children}
    </a>
  );
}

/* ------------------------------------------------------------------ Field */

interface FieldProps {
  label: string;
  children: (id: string, describedBy: string | undefined) => ReactNode;
  hint?: string;
  error?: string | null;
  required?: boolean;
  optional?: boolean;
  full?: boolean;
}

/**
 * A labelled field. The label is persistent; placeholder text is never the only label.
 * Validation sits next to the field and is announced to assistive technology.
 */
export function Field({
  label,
  children,
  hint,
  error,
  required,
  optional,
  full,
}: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={`field${full ? ' form-grid__full' : ''}`}>
      <label className="field__label" htmlFor={id}>
        {label}
        {required ? (
          <span className="field__required" aria-hidden="true">
            required
          </span>
        ) : null}
        {optional ? <span className="field__optional">optional</span> : null}
      </label>
      {children(id, describedBy || undefined)}
      {hint && !error ? (
        <span className="field__hint" id={hintId}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="field__error" id={errorId} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function TextInput({
  invalid,
  numeric,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean; numeric?: boolean }) {
  return (
    <input
      className={[
        'input',
        invalid ? 'input--invalid' : '',
        numeric ? 'input--numeric' : '',
        className ?? '',
      ]
        .filter(Boolean)
        .join(' ')}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function TextArea({
  invalid,
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }) {
  return (
    <textarea
      className={['textarea', invalid ? 'textarea--invalid' : '', className ?? '']
        .filter(Boolean)
        .join(' ')}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function Select({
  invalid,
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { invalid?: boolean }) {
  return (
    <select
      className={['select', invalid ? 'select--invalid' : '', className ?? '']
        .filter(Boolean)
        .join(' ')}
      aria-invalid={invalid || undefined}
      {...rest}
    >
      {children}
    </select>
  );
}

interface CheckboxProps {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

export function Checkbox({ label, hint, checked, onChange, disabled }: CheckboxProps) {
  return (
    <label className="choice">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="choice__body">
        <span className="choice__label">{label}</span>
        {hint ? <span className="choice__hint">{hint}</span> : null}
      </span>
    </label>
  );
}

export function Switch({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="row gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className="switch"
        disabled={disabled}
        onClick={() => onChange(!checked)}
      />
      <span className="choice__label">{label}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ Card */

export function Card({
  title,
  subtitle,
  actions,
  children,
  footer,
  flush,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="card">
      {title ? (
        <header className="card__header">
          <div className="card__heading">
            <h3 className="card__title">{title}</h3>
            {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="row gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={`card__body${flush ? ' card__body--flush' : ''}`}>{children}</div>
      {footer ? <footer className="card__footer">{footer}</footer> : null}
    </section>
  );
}

/* ------------------------------------------------------------------ Status */

export type Tone = 'ok' | 'warning' | 'error' | 'info' | 'neutral' | 'accent';

/** Shape and label in addition to colour, so colour is never the sole carrier. */
export function StatusChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`chip chip--${tone}`}>
      <span className="chip__mark" aria-hidden="true" />
      {children}
    </span>
  );
}

export function Alert({
  tone,
  title,
  children,
}: {
  tone: 'error' | 'warning' | 'info' | 'success';
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`alert alert--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <div className="alert__body">
        {title ? <span className="alert__title">{title}</span> : null}
        {children ? <span className="alert__detail">{children}</span> : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Measure tile */

/** Value, period and comparison. Never a bare number. */
export function Measure({
  label,
  value,
  period,
  footer,
  text,
}: {
  label: string;
  value: string;
  period?: string;
  footer?: ReactNode;
  text?: boolean;
}) {
  return (
    <div className="measure">
      <span className="measure__label">{label}</span>
      <span className={`measure__value${text ? ' measure__value--text' : ''}`}>{value}</span>
      <div className="measure__footer">
        {period ? <span className="measure__period">{period}</span> : null}
        {footer}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Empty and loading */

/** Explains the absence and offers the next action. */
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {detail ? <p className="empty__detail">{detail}</p> : null}
      {action}
    </div>
  );
}

/** Matches the eventual layout, rather than a spinner over a blank region. */
export function Skeleton({ rows = 3, height = 20 }: { rows?: number; height?: number }) {
  return (
    <div className="stack gap-2" aria-busy="true" aria-live="polite">
      <span className="visually-hidden">Loading</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="skeleton"
          style={{ height, width: index === rows - 1 ? '60%' : '100%' }}
        />
      ))}
    </div>
  );
}

export function TableSkeleton({ columns, rows = 5 }: { columns: number; rows?: number }) {
  return (
    <tbody aria-busy="true">
      {Array.from({ length: rows }, (_, row) => (
        <tr key={row}>
          {Array.from({ length: columns }, (_, column) => (
            <td key={column}>
              <div className="skeleton" style={{ height: 14, width: column === 0 ? '70%' : '45%' }} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/* ------------------------------------------------------------------ Dialog */

/**
 * A modal dialog. Focus is trapped within it and returns to the invoking control on
 * close; Escape closes it.
 */
export function Dialog({
  title,
  children,
  footer,
  onClose,
  size = 'md',
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  size?: 'sm' | 'md' | 'lg';
}) {
  const panel = useRef<HTMLDivElement>(null);
  const invoker = useRef<Element | null>(null);
  const titleId = useId();

  useEffect(() => {
    invoker.current = document.activeElement;
    const first = panel.current?.querySelector<HTMLElement>(
      'input, select, textarea, button, [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel.current) return;
      const focusable = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = previous;
      (invoker.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={`dialog dialog--${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panel}
      >
        <header className="dialog__header">
          <h2 className="dialog__title" id={titleId}>
            {title}
          </h2>
          <Button tone="plain" size="compact" onClick={onClose} aria-label="Close">
            Close
          </Button>
        </header>
        <div className="dialog__body">{children}</div>
        {footer ? <footer className="dialog__footer">{footer}</footer> : null}
      </div>
    </div>
  );
}

/**
 * Confirmation of a destructive action. Names the record and states what cannot be
 * undone.
 */
export function ConfirmDialog({
  title,
  record,
  consequence,
  confirmLabel,
  requireReason,
  busy,
  onConfirm,
  onClose,
}: {
  title: string;
  record: string;
  consequence: string;
  confirmLabel: string;
  requireReason?: boolean;
  busy?: boolean;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  const blocked = Boolean(requireReason) && reason.trim().length === 0;

  return (
    <Dialog
      title={title}
      size="sm"
      onClose={onClose}
      footer={
        <>
          <Button tone="plain" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            tone="destructive"
            busy={busy}
            disabled={blocked}
            onClick={() => onConfirm(reason.trim())}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="stack gap-4">
        <div className="stack gap-2">
          <p>
            <strong>{record}</strong>
          </p>
          <p className="muted">{consequence}</p>
        </div>
        {requireReason ? (
          <Field label="Reason" required hint="Recorded in the audit trail.">
            {(id, describedBy) => (
              <TextArea
                id={id}
                aria-describedby={describedBy}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
              />
            )}
          </Field>
        ) : null}
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ Toast */

interface ToastEntry {
  id: number;
  tone: 'success' | 'error' | 'info';
  message: string;
}

interface ToastApi {
  show: (tone: ToastEntry['tone'], message: string) => void;
}

const ToastContext = createContext<ToastApi>({ show: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);
  const next = useRef(0);

  const show = useCallback((tone: ToastEntry['tone'], message: string) => {
    const id = (next.current += 1);
    setEntries((current) => [...current, { id, tone, message }]);
    window.setTimeout(() => {
      setEntries((current) => current.filter((entry) => entry.id !== id));
    }, 6000);
  }, []);

  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {entries.map((entry) => (
          <div key={entry.id} className={`toast toast--${entry.tone}`}>
            {entry.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

/* ------------------------------------------------------------------ Tabs */

export function Tabs<T extends string>({
  items,
  active,
  onChange,
}: {
  items: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          className="tab"
          aria-selected={item.id === active}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Description list */

export function Details({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="details">
      {rows.map(([term, value]) => (
        <div key={term} style={{ display: 'contents' }}>
          <dt>{term}</dt>
          <dd>{value ?? <span className="muted">Not recorded</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Advanced settings behind a labelled disclosure. */
export function Disclosure({
  summary,
  children,
  open,
}: {
  summary: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="disclosure" open={open}>
      <summary className="disclosure__summary">{summary}</summary>
      <div className="disclosure__body">{children}</div>
    </details>
  );
}

export function SectionHeader({
  title,
  detail,
  actions,
}: {
  title: string;
  detail?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div className="stack gap-1">
        <h2>{title}</h2>
        {detail ? <p className="caption">{detail}</p> : null}
      </div>
      {actions ? <div className="row gap-2">{actions}</div> : null}
    </div>
  );
}
