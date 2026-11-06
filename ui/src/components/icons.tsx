/**
 * Navigation icons.
 *
 * Drawn inline as a consistent 18px stroke set, so nothing is fetched at runtime and
 * every icon inherits the current colour. Each is decorative: the navigation item
 * carries a permanent text label.
 */

import type { SVGProps } from 'react';

function Glyph({ children, ...rest }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const Icons = {
  dashboard: () => (
    <Glyph>
      <rect x="3" y="3" width="7" height="8" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="3" y="15" width="7" height="6" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
    </Glyph>
  ),
  sales: () => (
    <Glyph>
      <path d="M8 3h8l2 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7Z" />
      <path d="M6 7h12M9 12h6M9 16h4" />
    </Glyph>
  ),
  register: () => (
    <Glyph>
      <rect x="3" y="9" width="18" height="12" rx="1.5" />
      <path d="M7 9V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4M8 14h8M8 17.5h5" />
    </Glyph>
  ),
  purchases: () => (
    <Glyph>
      <path d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.8a2 2 0 0 0 2-1.5L21 8H6" />
      <circle cx="10" cy="20" r="1.2" />
      <circle cx="18" cy="20" r="1.2" />
    </Glyph>
  ),
  products: () => (
    <Glyph>
      <path d="M12 3 3 7.5v9L12 21l9-4.5v-9Z" />
      <path d="M3 7.5 12 12l9-4.5M12 12v9" />
    </Glyph>
  ),
  inventory: () => (
    <Glyph>
      <rect x="3" y="4" width="18" height="6" rx="1" />
      <rect x="3" y="14" width="8" height="6" rx="1" />
      <rect x="13" y="14" width="8" height="6" rx="1" />
    </Glyph>
  ),
  dispatch: () => (
    <Glyph>
      <path d="M3 7h11v9H3zM14 10h4l3 3v3h-7z" />
      <circle cx="7" cy="18.5" r="1.4" />
      <circle cx="17.5" cy="18.5" r="1.4" />
    </Glyph>
  ),
  parties: () => (
    <Glyph>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 5.2a3.2 3.2 0 0 1 0 5.6M18 20a5.5 5.5 0 0 0-2.8-4.8" />
    </Glyph>
  ),
  affiliates: () => (
    <Glyph>
      <circle cx="6" cy="7" r="2.4" />
      <circle cx="18" cy="7" r="2.4" />
      <circle cx="12" cy="18" r="2.4" />
      <path d="M8 8.5 11 15.6M16 8.5 13 15.6M8.4 7h7.2" />
    </Glyph>
  ),
  taxation: () => (
    <Glyph>
      <rect x="4" y="3" width="16" height="18" rx="1.5" />
      <path d="M8 8h8M8 12h3M14 16l2.5-4.5M8.5 15.5h.01M15.5 15.5h.01" />
    </Glyph>
  ),
  finance: () => (
    <Glyph>
      <path d="M4 19V9M9.5 19V5M15 19v-7M20.5 19v-4" />
      <path d="M3 21h18" />
    </Glyph>
  ),
  documents: () => (
    <Glyph>
      <path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z" />
      <path d="M14 3v4h4M9 12h6M9 16h4" />
    </Glyph>
  ),
  reports: () => (
    <Glyph>
      <path d="M21 12a9 9 0 1 1-9-9" />
      <path d="M12 3a9 9 0 0 1 9 9h-9Z" />
    </Glyph>
  ),
  settings: () => (
    <Glyph>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M4.2 7.3l2.6 1.5M17.2 15.2l2.6 1.5M4.2 16.7l2.6-1.5M17.2 8.8l2.6-1.5" />
    </Glyph>
  ),
  collapse: () => (
    <Glyph>
      <path d="M15 6l-6 6 6 6" />
    </Glyph>
  ),
  expand: () => (
    <Glyph>
      <path d="M9 6l6 6-6 6" />
    </Glyph>
  ),
  search: () => (
    <Glyph width="14" height="14">
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </Glyph>
  ),
};

export type IconName = keyof typeof Icons;
