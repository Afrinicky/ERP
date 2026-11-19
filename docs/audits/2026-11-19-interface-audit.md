# Interface audit — 19 November 2026

Scope: the React interface in `ui/src`, as shipped through Phase 9. Audited against three
skills: **Impeccable** (technical audit, product register), **UI/UX Pro Max** (priority rule
categories 1–10) and **Apple Design** (Human Interface Guidelines, desktop guidance, five
lenses). The product context is in [PRODUCT.md](../../PRODUCT.md); the visual system is in
the [design system](../03-design-system.md).

Method: Impeccable's bundled detector over `ui/src`; contrast computed from the shipped token
values (not estimated from screenshots); a code read of the shell, primitives and every
module; and screenshots of the inventory and dispatch workflows against seeded data.

## Health score (Impeccable)

| # | Dimension | Before | After fixes | Key finding |
| --- | --- | --- | --- | --- |
| 1 | Accessibility | 2 | 4 | Tertiary text below 4.5:1 in 30 places; nested dialogs mis-handle Escape |
| 2 | Performance | 3 | 3 | One 400 kB bundle; no route-level splitting (acceptable offline, noted) |
| 3 | Responsive design | 3 | 3 | Desktop-first and sound; wide tables scroll inside their frame |
| 4 | Theming | 3 | 4 | Full token system; the contrast audit held tertiary text to the large-text threshold |
| 5 | Anti-patterns | 3 | 4 | Toast side-stripe; a figure row on Dispatch that repeats its own tabs |
| | **Total** | **14 / 20 — Good** | **18 / 20 — Excellent** | |

## Anti-patterns verdict

**Pass, with two tells.** The interface does not read as generated: a restrained navy and teal
system, a dense rail, tables over cards, status carried by label and shape, formal labels that
say what they do. The two tells were the 3px coloured stripe down the left edge of every toast
(`components.css:852`, the "side-tab" pattern) and the four-figure row above the Dispatch tabs,
which restated the tabs' own counts as big numbers: the hero-metric reflex, with no information
the tabs did not already carry.

## Findings

Severity: **P0** blocks a task · **P1** WCAG AA failure or major friction · **P2** minor ·
**P3** polish. Each finding cites the guideline it rests on.

### P1 — Tertiary text fails WCAG AA contrast (fixed)

- **Where**: `--colour-text-tertiary`, used in 30 rules: every `.cell-note` (SKUs, batch and
  serial lines, reasons), input placeholders, captions, measure footers.
- **Measured** (default Nickland theme, 12–13px text): `#788894` is 3.65:1 on white, 3.39:1 on
  the canvas and 3.24:1 on the sunken surface. The other three themes measured 3.2–3.6:1.
- **Standard**: WCAG 1.4.3; `accessibility.md › Vision`: "Up to 17 pts … 4.5:1"; UI/UX Pro Max
  `color-contrast`; Impeccable: "Placeholder text needs the same 4.5:1."
- **Root cause**: `tokens/pairings.json` audited tertiary text at 3.0, the threshold for large
  text, while the interface uses it at 12px. The audit was passing a failure.
- **Fix**: tertiary text darkened along its own hue in every theme to clear 4.5:1 on the darkest
  surface it sits on — Nickland `#606E79` (4.64:1 sunken, 5.24:1 white), Navy `#5C6F89`,
  Harbour `#626C83`, Charcoal `#706A64` — and the audit now requires 4.5:1 on the card, the
  canvas and the sunken surface, so the regression cannot return.

### P1 — A dialog opened from a dialog closes the wrong one on Escape (fixed)

- **Where**: `ShipmentDialog` opening the dispatch, delivery and return steps; `Assembly`
  opening a shipment. Both dialogs register a capture-phase `keydown` listener on `document`;
  the outer one was registered first, so Escape closed the shipment record and left the step
  open over nothing.
- **Standard**: `modality.md › Best practices`: "Let people dismiss a modal view before
  presenting another one … presenting multiple views adds to people's cognitive load";
  UI/UX Pro Max `escape-routes`.
- **Fix**: a step now replaces the shipment record instead of stacking on it, and returns to it
  when done or cancelled. One modal at a time, everywhere.

### P1 — A stray click outside a dialog discards what was entered (fixed)

- **Where**: `Dialog` closed on any mouse-down on the backdrop: a count sheet, a delivery with
  a drawn signature, an opening-stock form of several lines.
- **Standard**: `modality.md › Best practices`: "help people avoid data loss by getting
  confirmation before closing a modal view"; UI/UX Pro Max `sheet-dismiss-confirm`.
- **Fix**: dialogs close by their Close or Cancel button or by Escape, never by a click beside
  them.

### P2 — Placeholder control in the shell (fixed)

- **Where**: `shell.tsx` — a permanently disabled "Search records" field in the header.
- **Standard**: design system *Prohibited*: "Placeholder … content in shipped screens";
  `feedback.md`: "Show people when a command can't be carried out and help them understand why."
- **Fix**: removed until search is built (it belongs to the reporting phase), rather than shown
  disabled with no explanation.

### P2 — Heading levels skip from h1 to h3 (fixed)

- **Where**: `PageHeading` renders `h1`; `Card` titles render `h3`; section labels inside
  dialogs render `h4` under the dialog's `h2`.
- **Standard**: WCAG 1.3.1; UI/UX Pro Max `heading-hierarchy`.
- **Fix**: card titles are `h2`, section labels within a dialog `h3`. No visual change.

### P2 — No way to bypass the navigation rail (fixed)

- **Where**: the shell. A keyboard user tabs through every rail item on every page.
- **Standard**: WCAG 2.4.1; UI/UX Pro Max `skip-links`; `accessibility.md › Mobility`
  (Full Keyboard Access).
- **Fix**: a "Skip to content" link, visible on focus, before the rail.

### P2 — The toast's coloured side-stripe (fixed)

- **Where**: `components.css:852`, `.toast` with `border-left: 3px` in the status colour.
- **Standard**: Impeccable absolute ban (side-stripe borders); `color.md › Inclusive color`
  (status must not rest on colour alone).
- **Fix**: a full hairline border and a leading status mark of the same shape vocabulary as the
  status chips; the message carries its meaning in words.

### P2 — Dispatch repeats its tab counts as a figure row (fixed)

- **Where**: `Dispatch.tsx` — four large figures (Awaiting dispatch, In transit, Delivered,
  With exceptions) directly above tabs of the same names.
- **Standard**: design system *Prohibited*: "Oversized cards carrying a single figure where a
  table serves better"; Impeccable hero-metric ban; `feedback.md`: "Consider integrating status
  feedback into your interface … near the items it describes."
- **Fix**: the counts move into the tabs themselves ("In transit · 1"), next to what they count.

### P2 — Empty states with no next step (fixed on the working screens)

- **Where**: Dispatch ("Nothing is being prepared."), Stock on hand ("Nothing is held yet."),
  Transfers, Counts.
- **Standard**: `writing.md › Best practices`: "Provide clear next steps on any blank screens …
  give them a button or link to do so"; UI/UX Pro Max `empty-states`.
- **Fix**: each carries its one action — Prepare a shipment, Record opening stock, Prepare a
  transfer, Begin a count — for those permitted to take it.

### P3 — The progress bar animates `width` (fixed)

- **Where**: `components.css:1716`.
- **Standard**: Impeccable *Motion*: "Don't animate CSS layout properties"; UI/UX Pro Max
  `transform-performance`.
- **Fix**: `transform: scaleX()` from the leading edge.

### P3 — Not fixed, recorded

- **Uppercase, tracked column headings and figure labels.** `lists-and-tables.md › Content`
  asks for title-style column headings; the interface uses small uppercase headings
  consistently. Kept: consistency across eighty tables matters more than the convention, and
  the headings are sentence case in the source, so a later switch is one stylesheet rule.
- **One bundle.** 400 kB, served locally from the desktop install, never over a network.
  Route-level splitting would shave first paint by a few hundred milliseconds; worth doing when
  the Point of Sale screen arrives, since it must open instantly.
- **A received-share figure computed in the browser** (`modules/sales/Orders.tsx`, the order
  progress bar sums ordered and received quantities). It escapes the architecture test because
  the guard looks for an operator straight after the name. Presentation only — no figure posts
  from it — but it belongs with the order position the service already computes; move it there
  when the order screen is next touched.
- **Disclosure triangle drawn with borders** (`components.css:952`): a false positive from the
  detector — it is a CSS triangle, not a side-stripe.

## Systemic notes

- **Contrast is audited, but only as declared.** The pairing list is the single place the
  thresholds live; a token used at a smaller size than its pairing assumes passes falsely. The
  pairing for every text token is now 4.5:1, and the audit covers each surface the text sits on.
- **Modality was the habit.** Most workflows open in a dialog. That is right for short, single
  tasks (`modality.md`), and the dispatch record is the case that outgrew it; it now replaces
  rather than stacks.

## What works — keep it

- **Status never rests on colour.** Every chip carries a label and a mark (`accessibility.md ›
  Vision`: "Offer visual indicators, like distinct shapes or icons, in addition to color").
- **Touch tiers by context.** 44px floor, 56px transactional and 64px counter controls with an
  8px inert gap where hands are busy (`accessibility.md › Mobility`; UI/UX Pro Max
  `touch-target-size`, `touch-spacing`); compact 32px controls only on the desk.
- **One primary action per transactional view, labelled for what it does** — "Post the
  receipt", "Dispatch and issue waybill", "Confirm delivery" (`buttons.md › Style`: "Keep the
  number of prominent buttons to one or two per view"; `writing.md`: "Be action oriented").
- **Figures with their units, computed by the service**, tabular and monospaced in columns
  (UI/UX Pro Max `number-tabular`).
- **Focus is visible and trapped only inside a dialog**; reduced motion is honoured globally;
  every colour comes from a token, enforced by a build-time test.
- **Errors say what happened and what to do**, in the interface's voice, next to the action
  (`writing.md › Write clear error messages`).

## Platform notes

The application is a web interface delivered as a Windows desktop application, so Apple's
platform conventions (menu bar, sidebar materials, Liquid Glass) do not apply; the principles
and the foundations — accessibility, colour, typography, layout, writing, modality — do, and are
what this audit cites. Desktop sizes apply: 13px default text, 10px minimum (the smallest text
here is 12px), 28px default controls (compact controls here are 32px).
