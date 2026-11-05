# Document Designer

Satisfies PRD §6.6, §6.7, AC-05, AC-06. PRD §6.6 designates this a core product capability:
an administrator must redesign existing documents and create new document types without
developer intervention.

## Position

Documents are data, not code. A template is a stored structure the administrator composes;
rendering is a pure function of a template version and a transaction. No document layout
exists in the source tree except as seeded template data.

## Template model

```
document_type                     pro forma, invoice, waybill, or administrator-defined
├─ numbering_sequence             per business, branch and type
├─ data_source                    the transaction type the template binds against
├─ classification                 internal or customer-facing
└─ template (versioned)
   ├─ page_configuration          size, orientation, margins, typography, colour, borders
   ├─ header                      repeating, per-page
   ├─ sections [ordered]
   │  ├─ section                  titled, bordered, conditionally visible
   │  │  └─ elements [positioned]
   │  │     ├─ field              bound to a palette entry, formatted
   │  │     ├─ text               static or interpolated
   │  │     ├─ image              logo, stamp, uploaded asset
   │  │     ├─ signature          captured or uploaded
   │  │     ├─ qr                 encoding a bound value
   │  │     └─ spacer
   │  └─ table
   │     ├─ columns [ordered]     label, width, alignment, format, visibility
   │     ├─ row binding           the line collection
   │     ├─ group and subtotal    optional
   │     └─ repeating header      and carried-forward totals
   └─ footer                      repeating, with page numbering
```

A version is immutable once published. Editing a published version creates a new draft
version.

## Field palette

Grouped exactly as PRD §6.6 requires.

| Group | Representative fields |
| --- | --- |
| Business | Name, logo, addresses, contacts, tax identifiers, bank details, Mobile Money details, branch |
| Customer | Name, code, bill-to, ship-to, contact, phone, email, tax identifier, terms, credit status |
| Supplier | Name, code, address, contact, tax identifier, terms |
| Transaction | Document number, type, issue date, validity or expiry date, customer purchase order reference, RFQ reference, our reference, state, prepared by, approved by, notes |
| Product | SKU, name, description, category, manufacturer, model, batch or lot, serial, expiry date, unit of measure, quantity, unit price, discount, line total, weight, volume |
| Payment | Plan type, deposit, instalment amount, frequency, duration, first and final due dates, finance charge, total payable, terms, bank details, Mobile Money details |
| Delivery | Mode, address, carrier, driver, vehicle, waybill number, dispatch date, package count, tracking reference, recipient, acknowledgement date |
| Tax | Rule name, rate, basis, amount, treatment, authority, identifier, summary by component |
| Totals | Subtotal, total discount, transport, other charges, tax total, grand total, total units, amount in words |
| Custom | Every administrator-defined custom field on the bound entity |
| Internal | Supplier cost, allocated costs, margin, affiliate commission, contribution — available only to internal templates |

Internal fields are not offered in the palette when the template is classified
customer-facing. The classification cannot be changed on a published template that has
issued documents.

## Table column editing

Per PRD §6.6, the administrator may:

| Operation | Behaviour |
| --- | --- |
| Add | Select a palette field; a column is appended with a derived default label and format |
| Remove | The column is withdrawn; the remaining columns redistribute the freed width |
| Reorder | By drag, or by keyboard with the column selected |
| Rename | The label is independent of the bound field name |
| Resize | By drag or by entered width; the editor warns when total width exceeds the printable area |
| Align | Left, centre or right; numeric columns default to right with decimal alignment |
| Format | Number precision, currency symbol and position, date format, text case, thousands separator |
| Visibility | Always, never, or conditional |

## Conditional visibility

Per PRD §6.6, a section, element or column may be conditional. Examples the requirements
name explicitly: manufacturer and expiry appear only where populated; instalment details
appear only for an instalment sale.

Conditions are composed in a constrained builder, not written as free text:

```
<field> <operator> <value>          operators: is empty, is not empty, equals, not equals,
                                               greater than, less than, in, not in
<condition> and|or <condition>      at most three terms, no nesting
```

## Calculated fields

Per PRD §6.6, calculated and custom fields are supported "through safe constrained
expressions, not arbitrary code execution".

| Control | Specification |
| --- | --- |
| Grammar | A closed grammar: numeric literals, palette field references, `+ − × ÷`, parentheses, and a fixed function set |
| Functions | `sum`, `round`, `min`, `max`, `if`, `abs`, `words` — nothing further |
| Evaluation | A parsed abstract syntax tree walked by an interpreter with no host access |
| Prohibited | Identifier resolution outside the palette, attribute access, indexing, loops, assignment, imports, and any host call |
| Resources | Expression depth, term count and evaluation time are bounded |
| Failure | A failed expression renders a stated placeholder and records a diagnostic; it never aborts the document |

An automated test asserts that no expression can execute arbitrary code. This is a Phase 6
exit criterion.

## Editor

Per PRD §6.6, drag-and-drop editing or an equivalent accessible visual layout editor.
Both are provided; they are the same editor in two input modes.

| Region | Contents |
| --- | --- |
| Palette | Searchable, grouped fields; drag to the canvas or select and press Enter to insert at the cursor |
| Canvas | Page-accurate rendering at true proportions, with margin guides, a snapping grid and page-break indicators |
| Inspector | Properties of the selected element: binding, label, format, width, alignment, visibility, style |
| Outline | The document tree; sections and elements reorderable by keyboard |
| Toolbar | Version state, preview, publish, duplicate, export, import, revert |

### Keyboard equivalence

Every drag operation has a keyboard equivalent. Insert by selecting a palette entry and
pressing Enter. Move by selecting an element and using arrow keys, or by reordering within
the outline. Resize with modifier and arrow keys. The editor is fully operable without a
pointer, per PRD §11 accessibility.

## Preview

Two modes, both required by PRD §6.6.

| Mode | Data |
| --- | --- |
| Sample | A seeded representative transaction with enough lines to exercise pagination, grouping and carried-forward totals |
| Draft | A real draft transaction the administrator selects |

Preview renders through the same engine as final output. A preview is watermarked and is
never issuable.

## Page composition

| Concern | Options |
| --- | --- |
| Page size | A4, A5, Letter, Legal, 80mm and 58mm receipt rolls |
| Orientation | Portrait, landscape |
| Margins | Per edge, in millimetres |
| Typography | Family, size, weight and colour per text role |
| Colour | Per element, from the theme tokens or an explicit value |
| Borders | Per section and per table, with width, style and colour |
| Table styles | Header fill, row banding, grid lines, cell padding |
| Pagination | Explicit breaks, keep-together on sections, repeating table headers, carried-forward totals, page `n of m` |

## Rendering

| Concern | Specification |
| --- | --- |
| Engine | HTML and CSS composed from the template model, rendered to PDF by WeasyPrint |
| Determinism | A template version and a transaction always produce an identical document |
| Fonts | Bundled and embedded; nothing is fetched at render time |
| Output | PDF for file and print; direct thermal printing for receipt formats |
| Performance | A twenty-line pro forma renders in under two seconds on office hardware |

## Numbering

Per PRD §6.7, numbers are unique by business, branch and document type. A sequence defines a
prefix, a pattern with date and counter components, and its next value. Allocation occurs
within the issuing transaction and is safe under concurrent issue in LAN mode, per PRD §9.5.
A number is never reused, and a gap is never silently closed.

## Version preservation

Per PRD §6.6 and §14, an issued document must remain reproducible.

1. On issue, the rendered PDF is stored as an immutable snapshot with a content hash.
2. The document records the template version used.
3. A later template amendment creates a new version and leaves issued documents untouched.
4. Reprinting serves the stored snapshot, not a re-render.
5. Where a re-render is required for audit, it uses the recorded template version and the
   transaction state at issue.

## Customer acknowledgement

Per PRD §6.7, acknowledgement is captured as a signed delivery document, or as a scanned
signed PDF or image. The record holds the recipient, the signature or stamp, the date and
time, the location and any comments. The original evidence is preserved without alteration
and carries a content hash.

## Portability

Authorised export, import and duplication of templates, per PRD §6.6. An exported template
carries its structure, formatting and expressions, and carries no business data. Import
validates every field binding against the target installation's palette and reports any
unresolved binding before the template is accepted.

## Default templates

Twelve professional defaults ship, per PRD §6.6 and §6.7: pro forma invoice, quotation, sales
invoice, receipt, waybill, delivery note, packing list, purchase order, goods received note,
statement, payment voucher and credit note.

The defaults are informed by the structure of the source documents reviewed in PRD §17 —
supplier identity, tax identifier, order number, issue date, bill-to and ship-to, SKU,
description, quantity, price, discount, line total, subtotal, total units, validity, terms
and banking details. No branding, wording or identity from any reviewed document is
reproduced, per PRD §17 and §18.

### Default pro forma composition

Per PRD §6.6, the default pro forma includes: business identity, bill-to and ship-to,
document number, issue date, validity or expiry date, customer purchase order or RFQ
reference, item table, subtotal, discount, transport and other charges, tax summary, grand
total, payment plan and terms, delivery or collection terms, payment details, approval and
signature, and footer.

Manufacturer, model, batch, expiry and the remaining product fields are available as optional
columns, disabled by default.

## Confidentiality control

Per PRD §6.6, §10 and §14, internal and customer-facing templates are separated to prevent
disclosure of supplier cost and commission.

| Control | Mechanism |
| --- | --- |
| Palette restriction | Internal fields are not offered to a customer-facing template |
| Binding validation | A customer-facing template cannot be published with an internal binding |
| Expression validation | An expression in a customer-facing template cannot reference an internal field |
| Render assertion | An automated test renders every default customer-facing template against a transaction carrying known cost, margin and commission values, and asserts their absence from the extracted PDF text |
| Classification lock | Classification cannot change once documents have been issued |

## Permissions

| Capability | Role |
| --- | --- |
| View generated documents | Any role with access to the source transaction |
| Issue a document | The role owning the source transaction |
| Reprint | The role owning the source transaction |
| Edit a template | Administrator |
| Publish a template version | Administrator |
| Create a document type | Administrator |
| Export or import a template | Administrator |
| View an internal template's output | Roles holding the internal costing permission |
