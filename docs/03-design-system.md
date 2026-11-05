# Design System

Satisfies PRD §7, §9.7, §28, §31. Governed by
[ADR-0002](decisions/adr-0002-dual-theme-token-engine.md).

## Design intent

The interface is an instrument of work, not a presentation about work. It is dense where
density aids comprehension, quiet everywhere else, and it states the next action without
being asked. A user who has operated a till, a ledger or a stockroom should recognise what
they are looking at.

### Prohibited

Per PRD §7.1 and §28.2, the following are excluded by standard, not by preference.

- Decorative gradients, and orange as a page background.
- Illustrative graphics, mascots and stock photography.
- Oversized cards carrying a single figure where a table serves better.
- Figures without a stated period, currency or unit.
- Colour as the sole carrier of meaning.
- Brand colour substituted for status meaning.
- Placeholder, sample or filler content in shipped screens.

## Token architecture

Colour, type, spacing, radius, elevation, motion and border values exist once, in
`tokens/*.json`. A build step generates CSS custom properties and TypeScript types into
`ui/src/tokens/`. No colour literal appears in a component. An automated check fails the
build on any hard-coded colour, per PRD §9.7.

```
tokens/
├─ primitives.json      raw scales — never referenced by a component
├─ semantic.json        role-based aliases — the only layer components reference
├─ presets/
│  ├─ charcoal.json     PRD §28 palette
│  └─ navy.json         PRD §7.1 palette
└─ components.json      component-level overrides where genuinely required
```

Components reference semantic tokens only. A theme change therefore alters no component.

### Semantic layer

| Token | Role |
| --- | --- |
| `surface.canvas` | Application background |
| `surface.raised` | Cards, panels, table containers |
| `surface.sunken` | Input wells, inset regions |
| `surface.navigation` | Primary navigation and application header |
| `surface.overlay` | Dialogs, popovers, menus |
| `text.primary` | Body and table content |
| `text.secondary` | Labels, captions, column headers |
| `text.tertiary` | Hints, disabled text, metadata |
| `text.inverse` | Text on dark and accent surfaces |
| `border.subtle` | Table rules, dividers |
| `border.default` | Input and card borders |
| `border.strong` | Focused and selected borders |
| `accent.primary` | Primary action, active navigation, key chart series |
| `accent.primary.hover`, `accent.primary.active` | Interaction states |
| `accent.secondary` | Supporting emphasis, secondary chart series |
| `status.success`, `status.warning`, `status.error`, `status.info` | Semantic status, independent of brand |
| `focus.ring` | Keyboard focus indicator |

## Shipped themes

Both presets ship as equal first-class options. No default is imposed; the administrator
selects one during first-run setup. See [ADR-0002](decisions/adr-0002-dual-theme-token-engine.md).

### Charcoal — PRD §28

| Role | Value | Application |
| --- | --- | --- |
| Primary charcoal | `#252525` | Navigation, application header, primary text, dark surfaces |
| Orange accent | `#F7931E` | Primary actions, active navigation, selected tiles, key chart series |
| Beige accent | `#C9A078` | Secondary emphasis, supporting chart series, quiet highlights |
| White | `#FFFFFF` | Cards, tables, content surfaces |
| Off-white | `#F7F5F3` | Application canvas |
| Light grey | `#E8E6E4` | Dividers, borders, disabled surfaces |

### Navy — PRD §7.1

| Role | Value | Application |
| --- | --- | --- |
| Primary navy | `#1B2A41` | Navigation, application header, primary text, dark surfaces |
| Accent | `#2F6FED` | Primary actions, active navigation, key chart series |
| Secondary accent | `#7D8FA8` | Secondary emphasis, supporting chart series |
| White | `#FFFFFF` | Cards, tables, content surfaces |
| Off-white | `#F6F8FA` | Application canvas |
| Light grey | `#E3E8EF` | Dividers, borders, disabled surfaces |

### Status palette

Identical across both presets, and never replaced by a brand colour.

| Status | Value | Surface |
| --- | --- | --- |
| Success | `#1F7A4C` | `#E8F5EE` |
| Warning | `#B26A00` | `#FDF3E3` |
| Error | `#C0392B` | `#FBEAE8` |
| Information | `#1F5F8B` | `#E7F1F8` |

### Custom themes

The administrator may override the primary, accent, secondary, background and text tokens,
and the logo and business name, per PRD §28.2. The theme editor:

1. validates every foreground-and-background pairing against WCAG AA before the value is
   accepted, and refuses a failing combination;
2. previews the proposed theme across a representative sample of real screens before saving;
3. retains a reset to the selected preset's published defaults.

A dark mode is produced from the same semantic layer. It is optional and is not required
before Phase 14.

## Typography

| Role | Family | Size | Weight | Line height | Tracking |
| --- | --- | --- | --- | --- | --- |
| Page title | Inter | 24px | 600 | 32px | −0.01em |
| Section title | Inter | 18px | 600 | 26px | −0.005em |
| Subsection title | Inter | 15px | 600 | 22px | 0 |
| Body | Inter | 14px | 400 | 21px | 0 |
| Body emphasis | Inter | 14px | 500 | 21px | 0 |
| Table content | Inter | 13px | 400 | 19px | 0 |
| Column header | Inter | 12px | 600 | 16px | 0.04em, uppercase |
| Label | Inter | 12px | 500 | 16px | 0.01em |
| Caption | Inter | 12px | 400 | 16px | 0 |
| Numeric | JetBrains Mono | 13px | 400 | 19px | 0 |
| Numeric emphasis | JetBrains Mono | 15px | 500 | 20px | 0 |
| Measure display | JetBrains Mono | 28px | 500 | 34px | −0.01em |

Both families are bundled; nothing is fetched at runtime. Every monetary and quantity figure
is set in the monospaced family and aligned on the decimal, so columns of figures compare by
eye without being read. Negative amounts are rendered in parentheses in financial statements
and with a leading minus elsewhere.

## Spacing and layout

A 4px base unit. Permitted steps: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.

| Region | Specification |
| --- | --- |
| Navigation rail | 240px expanded, 64px collapsed, persistent |
| Application header | 56px, fixed |
| Content gutter | 24px, reducing to 16px below 1280px |
| Card padding | 20px |
| Table row height | 40px standard, 32px compact, 48px touch |
| Form field height | 36px |
| Control height | 36px standard, 32px compact, 44px point of sale |
| Dialog width | 480px small, 640px standard, 880px large |
| Maximum content width | 1600px, centred beyond that |

### Breakpoints

| Breakpoint | Width | Behaviour |
| --- | --- | --- |
| Compact | below 1024px | Navigation collapses to icons; tables scroll horizontally with a frozen first column |
| Standard | 1024–1439px | Full navigation; two-column forms |
| Wide | 1440px and above | Three-column forms; side panel persistent rather than overlaid |

Desktop is the primary target. Key workflows remain usable at tablet width, per PRD §28.2.

## Shell structure

```
┌────────────────────────────────────────────────────────────────────┐
│ ▪ Business name          Branch ▾   Period ▾      ⌕    ◔   AO ▾   │  header 56px
├──────────┬─────────────────────────────────────────────────────────┤
│          │ Sales ▸ Quotations ▸ QTN-2026-0184          [Actions ▾] │  context bar
│ Dashboard│─────────────────────────────────────────────────────────│
│ Sales    │                                                        │
│ Point of │   Work surface                                         │
│  Sale    │                                                        │
│ Purchases│                                                        │
│ Products │                                                        │
│ Inventory│                                                        │
│ Dispatch │                                                        │
│ Parties  │                                                        │
│ Affiliates│                                                       │
│ Taxation │                                                        │
│ Finance  │                                                        │
│ Documents│                                                        │
│ Reports  │                                                        │
│ Settings │                                                        │
├──────────┴─────────────────────────────────────────────────────────┤
│ Standalone · Synchronised 14:32 · Backup current · Period open     │  status 28px
└────────────────────────────────────────────────────────────────────┘
```

The context bar carries the breadcrumb trail on the left and the record's permitted actions
on the right. The primary action is the single filled accent button; everything else is
outlined or plain. The status strip reports deployment mode, service health, backup position
and accounting period state, per PRD §9.4.

### Navigation

Fourteen top-level destinations, per PRD §7.3 and §23. Every item is icon-led with a
permanent text label, per PRD §28.2.

| Destination | Contents |
| --- | --- |
| Dashboard | Measures, pending tasks, financial summary, alerts |
| Sales | RFQs, quotations, pro formas, sales orders, invoices, returns |
| Point of Sale | Register, checkout, suspended sales, shifts, refunds |
| Purchases | Supplier RFQs, comparisons, purchase orders, invoices, payments |
| Products | Catalogue, categories, attributes, units, price lists, supplier links |
| Inventory | Stock, receiving, inspection, assembly, batches, expiry, transfers, counts |
| Dispatch | Picking, packing, waybills, delivery, proof of receipt |
| Parties | Customers, suppliers, contacts, history, statements, balances |
| Affiliates | Referrers, eligibility, rates, accruals, payouts |
| Taxation | Overview, outstanding, paid, withheld, recoverable, import charges, filings, rules, reconciliation |
| Finance | Command centre, income, expenses, cash and bank, receivables, payables, loans, budgets, profitability, ledger, accounts, reconciliation, reports |
| Documents | Generated documents, uploaded documents, templates, designer |
| Reports | Operational and management reporting |
| Settings | Users, permissions, theme, templates, taxes, payment plans, numbering, backup |

Point of Sale occupies the whole window when entered. It presents no navigation rail, so a
cashier cannot leave the register by accident.

## Components

### Foundations

Button, icon button, split button, input, numeric input, currency input, quantity input,
select, combobox, multi-select, date and date-range picker, checkbox, radio group, switch,
textarea, file upload, search field, segmented control, slider.

### Composition

Card, panel, section header, tab set, accordion, side panel, dialog, drawer, popover,
tooltip, divider, breadcrumb, pagination, empty state, loading skeleton, inline alert,
banner, toast.

### Business components

| Component | Purpose |
| --- | --- |
| Data table | Virtualised, column reordering and resizing, sorting, filtering, grouping, pinned columns, aggregate footer, row selection, saved views, CSV and Excel export |
| Document line editor | Keyboard-driven grid for quotation, order, invoice and receipt lines; tab advances, Enter commits a row, scanner input appends |
| Measure tile | Value, period, comparison, trend direction, drill-down target; never a bare number |
| Status chip | Shape and label in addition to colour |
| Party selector | Searches name, code, phone and tax identifier; shows balance and credit status inline |
| Product selector | Searches SKU, name and barcode; shows price, available quantity and item class |
| Cost build-up panel | Each component of the price as a separate, labelled, traceable line |
| Tax evaluation panel | Each rule with its result — Applied, Not applicable, Exempt, Needs review, Overridden |
| Allocation editor | Distributes an expense or payment across lines with a live residual |
| Schedule table | Instalment and amortisation rows with principal, interest, fees, total, paid and balance |
| Ageing bars | Current, 1–30, 31–60, 61–90, over 90 days |
| Approval banner | Who approved, when, on what basis, with the evidence link |
| Audit trail panel | Actor, timestamp, action, reason, before and after |
| Attachment list | Preview, download, content hash, upload actor and timestamp |
| Register tile | POS product card — image, name, SKU, price, availability |
| Tender panel | Method, amount, cash received, change due, confirmation state |
| Chart frame | Title, period, currency, unit, legend, export, table alternative, empty and loading states |

### Component standards

1. Every interactive element has a visible focus ring of at least 2px at 3:1 contrast.
2. Every form field has a persistent label. Placeholder text is never the only label.
3. Validation appears adjacent to the field, states the remedy, and is announced to assistive
   technology.
4. Every destructive action is confirmed, names the record, and states what cannot be undone.
5. Every table has an empty state that explains the absence and offers the next action.
6. Loading uses skeletons matching the eventual layout, not spinners over blank regions.
7. No action proceeds without feedback within 100ms.
8. Every figure is accompanied by its period, currency or unit.

## Interaction standards

| Concern | Standard |
| --- | --- |
| Primary action | One per view, filled accent, positioned bottom-right of a form or right of the context bar |
| Keyboard | Every workflow completable without a pointer |
| Tables | Arrow-key navigation, Space to select, Enter to open |
| Forms | Tab order follows reading order; Ctrl+S saves a draft; Esc closes with an unsaved-change prompt |
| Drafts | Autosaved every 30 seconds and on navigation, per PRD §7.2 |
| Scanner input | Captured by the active line grid without focus management by the user |
| Progressive disclosure | Essential fields first; advanced settings behind a labelled disclosure, per PRD §7.2 |
| Prefill | Business, customer, supplier and product details prefilled from existing records |
| Reuse | Data carried forward across RFQ, quotation, sales order, purchase order, GRN, dispatch and invoice without re-entry |
| Long operations | Off the interface thread, with progress, cancellation and a clear failure message |

### Point of sale

| Key | Action |
| --- | --- |
| F2 | Product search |
| F3 | Customer selection |
| F4 | Line discount, subject to permission |
| F6 | Suspend sale |
| F7 | Retrieve suspended sale |
| F8 | Tender |
| F9 | Cash tender, exact |
| F10 | Complete |
| F12 | Shift operations |
| Esc | Cancel current line |

Targets are a minimum of 44px. The cart is permanently visible. The total is the largest
figure on screen. Change due is displayed until dismissed. Tender confirmation state is
stated in words, never implied by colour alone.

## Charts

Satisfies PRD §20.7, §28.2, §29.8.

| Requirement | Standard |
| --- | --- |
| Series colour | Accent primary for the series under examination; accent secondary and neutrals for supporting series |
| Non-colour encoding | Line style, marker shape or direct labelling in addition to colour |
| Axes | Labelled with units; the value axis includes zero unless truncation is explicitly stated |
| Legend | Present whenever more than one series is shown |
| Period | Stated on every chart |
| Currency and unit | Stated on every axis and tooltip |
| Tooltip | Series name, period, exact value, comparison |
| Drill-down | Clicking a segment opens the filtered records, per PRD §20.7 |
| Table alternative | Every chart offers its underlying data as a table |
| Empty and loading | Explicit states; never a blank frame |
| Forecast distinction | Forecast, budget and estimate series are dashed and labelled, and never presented as actual |
| Export | PNG and the underlying data, offline |

### Chart selection

| Purpose | Form |
| --- | --- |
| Revenue and net profit over time | Line |
| Income against expenses | Grouped column |
| Cash flow | Column for movement with a line for running balance |
| Expense composition | Ranked horizontal bar |
| Profit by product or category | Ranked horizontal bar |
| Sales channel comparison | Grouped column |
| Tax position | Stacked column by state over time |
| Receivable and payable ageing | Stacked horizontal bar by bucket |
| Supplier cost variance | Paired bar, quoted against actual |
| Period performance | Column with a prior-period reference line |
| Budget utilisation | Horizontal bar with a target marker |
| Debt maturity profile | Column by maturity band |

Pie and donut charts are used only for a single part-to-whole relationship of at most five
categories. Three-dimensional effects, dual value axes and truncated axes without
annotation are prohibited.

## Accessibility

Satisfies PRD §11 and §28.2.

1. Text meets WCAG AA: 4.5:1 for body, 3:1 for text above 18px.
2. Interface components and graphical objects meet 3:1 against adjacent colours.
3. Colour is never the sole carrier of meaning; status carries a label and a shape.
4. Every workflow is completable by keyboard.
5. Focus is always visible and never trapped except within a modal dialog.
6. Every control, icon and chart has an accessible name.
7. Validation and status changes are announced to assistive technology.
8. The interface remains usable at 200% zoom and at 125% and 150% Windows display scaling.
9. Theme validation rejects any custom colour pairing that fails these thresholds.

## Content standards

Formal, specific, and free of exhortation.

| Use | Avoid |
| --- | --- |
| Issue quotation | Let's send it |
| Post journal entry | Save changes |
| Outstanding balance | Money owed |
| Proof of delivery | Delivery pic |
| Awaiting confirmation | Processing… |
| Payment not confirmed | Payment failed, where the state is merely unconfirmed |
| Record supplier payment | Pay supplier |
| Reverse posted entry | Delete entry |
| Instalment schedule | Payment plan details |

Rules:

1. Titles are nouns or noun phrases. Actions are imperative verbs.
2. Sentence case throughout, except column headers, which are uppercase by token.
3. No exclamation marks. No first-person plural. No encouragement.
4. Figures state their currency and period.
5. An error states what occurred, why, and the remedy.
6. An unconfirmed state is never described as a completed one.
7. The interface is written in British English, consistent with the requirements document.

## Verification

The following are automated and run in continuous integration.

| Check | Enforces |
| --- | --- |
| Token lint | No colour, font, spacing or radius literal outside the token layer |
| Contrast audit | Every shipped token pairing and every saved custom theme |
| Accessibility audit | Automated axe pass per screen; keyboard traversal per workflow |
| Visual regression | Every component in both presets, at three breakpoints |
| Content lint | Prohibited vocabulary and punctuation |
| Confidentiality test | Supplier cost, margin and commission absent from rendered customer-facing output |
