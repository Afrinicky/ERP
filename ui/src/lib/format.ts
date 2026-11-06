/**
 * Presentation helpers.
 *
 * Every figure is accompanied by its period, currency or unit. Monetary values arrive
 * from the service as decimal strings and are never parsed to a floating-point number
 * for arithmetic — only for presentation.
 */

export interface CurrencyProfile {
  symbol: string;
  precision: number;
  code: string;
}

/**
 * Format a monetary amount.
 *
 * The amount arrives as a decimal string. Grouping and the decimal point are applied
 * textually, so no precision is lost on the way to the screen.
 */
export function money(amount: string | null | undefined, profile: CurrencyProfile): string {
  if (amount === null || amount === undefined || amount === '') return '—';
  const negative = amount.trimStart().startsWith('-');
  const digits = amount.replace('-', '').trim();
  const [whole = '0', fraction = ''] = digits.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const scaled =
    profile.precision > 0
      ? `.${fraction.padEnd(profile.precision, '0').slice(0, profile.precision)}`
      : '';
  const body = `${profile.symbol}${grouped}${scaled}`;
  // Negatives are parenthesised in financial presentation.
  return negative ? `(${body})` : body;
}

export function quantity(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const text = String(value);
  const trimmed = text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text;
  const [whole = '0', fraction] = trimmed.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return fraction ? `${grouped}.${fraction}` : grouped;
}

const DATE = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export function date(value: string | null | undefined): string {
  if (!value) return '—';
  return DATE.format(new Date(value));
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return '—';
  return DATE_TIME.format(new Date(value)).replace(',', '');
}

export function relative(value: string | null | undefined): string {
  if (!value) return 'never';
  const then = new Date(value).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`;
  return date(value);
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[unit]}`;
}

export function initials(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return `${parts[0]![0]}${parts[parts.length - 1]![0]}`.toUpperCase();
}

/** Convert a machine code to a readable label: `sales_person` to `Sales person`. */
export function humanise(code: string): string {
  const text = code.replace(/[_.-]/g, ' ').trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * A form of address drawn from a full name.
 *
 * "N. Nickland" yields "Nickland", not "N.": an initial is not a name to greet someone
 * by. Where every part is an initial, the whole name is used.
 */
export function addressAs(fullName: string | null | undefined): string {
  const parts = (fullName ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '';
  const named = parts.find((part) => part.replace(/[.]/g, '').length > 1);
  return named ?? parts.join(' ');
}

export function ratio(value: number): string {
  return `${value.toFixed(2)}:1`;
}

/**
 * A rate held as a fraction, shown as a percentage.
 *
 * The service holds 0.075 and a reader wants 7.5%. The conversion is presentation,
 * not arithmetic on money: no monetary figure passes through it, and a rate is never
 * summed, so the exact-decimal rule that governs amounts does not apply here.
 */
export function percent(
  rate: string | number | null | undefined,
  places = 2,
): string {
  if (rate === null || rate === undefined || rate === "") return "—";
  const scaled = (Number(rate) * 100).toFixed(places);
  return `${scaled.replace(/\.?0+$/, "")}%`;
}
