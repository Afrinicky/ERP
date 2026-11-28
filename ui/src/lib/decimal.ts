/**
 * Exact arithmetic on decimal strings.
 *
 * Amounts cross the boundary as strings so that no figure passes through a JSON
 * float (PRD 9.5). Parsing one back with `Number` to add it up would reintroduce
 * exactly the error the string was there to avoid: in binary floating point,
 * 0.1 + 0.2 is not 0.3, and a journal that balances would be reported as out by a
 * fraction of a pesewa.
 *
 * Every figure the user acts on is still computed in Python. These helpers exist for
 * the one thing the server cannot do — tell the operator, while they are still
 * typing, whether the entry in front of them balances — and for comparing a figure
 * against zero. They work on the digits, scaling to a common exponent and adding as
 * integers, so they are exact for any value the ledger can hold.
 */

interface Parsed {
  negative: boolean;
  digits: string;
  exponent: number;
}

function parse(figure: string): Parsed | null {
  const text = figure.trim();
  if (!/^[+-]?\d*(\.\d*)?$/.test(text) || text === '' || text === '.') return null;
  const negative = text.startsWith('-');
  const unsigned = text.replace(/^[+-]/, '');
  const [whole = '', fraction = ''] = unsigned.split('.');
  const digits = `${whole}${fraction}`.replace(/^0+(?=\d)/, '');
  return { negative, digits: digits === '' ? '0' : digits, exponent: fraction.length };
}

/** The figure as an integer count of its smallest units, at the given exponent. */
function units(parsed: Parsed, exponent: number): bigint {
  const scaled = BigInt(parsed.digits) * 10n ** BigInt(exponent - parsed.exponent);
  return parsed.negative ? -scaled : scaled;
}

function render(scaled: bigint, exponent: number): string {
  const negative = scaled < 0n;
  const digits = (negative ? -scaled : scaled).toString().padStart(exponent + 1, '0');
  const whole = digits.slice(0, digits.length - exponent);
  const fraction = exponent > 0 ? `.${digits.slice(digits.length - exponent)}` : '';
  return `${negative ? '-' : ''}${whole}${fraction}`;
}

/** Add decimal strings exactly. An unparseable figure contributes nothing. */
export function sum(figures: readonly string[]): string {
  const parsed = figures.map(parse).filter((item): item is Parsed => item !== null);
  if (parsed.length === 0) return '0';
  const exponent = Math.max(...parsed.map((item) => item.exponent));
  const scaled = parsed.reduce((carried, item) => carried + units(item, exponent), 0n);
  return render(scaled, exponent);
}

/** The left figure less the right, exactly. */
export function difference(left: string, right: string): string {
  const negated = right.trim().startsWith('-')
    ? right.trim().slice(1)
    : `-${right.trim() || '0'}`;
  return sum([left, negated]);
}

/** Whether a decimal string is exactly zero, however it is written. */
export function isZero(figure: string | null | undefined): boolean {
  if (figure === null || figure === undefined) return true;
  const parsed = parse(figure);
  return parsed === null ? true : BigInt(parsed.digits) === 0n;
}

/** Whether a decimal string is below zero. */
export function isNegative(figure: string | null | undefined): boolean {
  if (figure === null || figure === undefined) return false;
  const parsed = parse(figure);
  return parsed !== null && parsed.negative && BigInt(parsed.digits) !== 0n;
}

/** Drop the sign, for presenting a figure beside the word that carries it. */
export function magnitude(figure: string): string {
  return figure.trim().startsWith('-') ? figure.trim().slice(1) : figure.trim();
}
