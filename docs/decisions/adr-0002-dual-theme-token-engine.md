# ADR-0002 — Token-driven theming with shipped presets and no imposed default

Status: accepted · Date: 5 November 2026

## Context

PRD §7.1 and the document-control table specify bluish-black or navy as the default primary
brand colour. PRD §28.1, written in revision v1.3, specifies charcoal `#252525`, orange
`#F7931E`, beige `#C9A078` and off-white. PRD §31 tests that the default theme visibly uses
charcoal, orange, beige and off-white. §7.1 was not withdrawn when §28 superseded it. See
[conflict register](../09-conflict-register.md) C-02.

PRD §28.2 separately requires that theme customisation use design tokens with validated
accessible colour combinations, a preview before applying, and a reset to defaults.

## Decision

Both palettes ship as complete, equal presets over a single token layer. No default is
imposed. The administrator selects a preset during first-run setup.

| Preset | Source | Primary | Accent | Secondary |
| --- | --- | --- | --- | --- |
| Charcoal | §28.1 | `#252525` | `#F7931E` | `#C9A078` |
| Navy | §7.1 | `#1B2A41` | `#2F6FED` | `#7D8FA8` |
| Harbour | The pilot's own terminal | `#14276B` | `#F5B921` | `#1A5FBF` |

Harbour was added after the pilot supplied photographs of the retail terminal its staff
use daily. The token layer made it one file and no component change, which is the
argument for the token layer made concrete.

Status colours are identical across both presets and are never replaced by a brand colour,
per §28.1.

## Rationale

The disagreement is not a design question but a document-maintenance artefact: v1.3 changed
the palette and v1.0's text remained. Either palette satisfies the stated design intent of
§7.1 and §28.2 — a professional light interface, restrained, with purposeful colour. The
token layer required by §28.2 makes a second preset nearly free: a preset is one JSON file,
and no component changes.

Imposing a default would either contradict §7.1 or contradict §31. Shipping both, and letting
the business choose, contradicts neither and defers a cosmetic decision to the person whose
business it is.

## Implementation

1. Colour, type, spacing, radius, elevation, motion and border values exist once, in
   `tokens/*.json`. A build step generates CSS custom properties and TypeScript types.
2. Components reference the semantic layer only. A preset change alters no component.
3. An automated check fails the build on any colour, font, spacing or radius literal outside
   the token layer, per §9.7.
4. The theme editor validates every foreground-and-background pairing against WCAG AA before
   accepting a value, and refuses a failing combination, per §28.2.
5. The editor previews a proposed theme across representative real screens before saving.
6. A reset restores the selected preset's published defaults.
7. Dark mode derives from the same semantic layer. It is optional and not required before
   Phase 14.

## Consequences

Visual regression runs against every preset, so the suite grows with each. Every preset is
maintained, so a new component must be checked in all of them. A further preset costs one
file, which is how Harbour was added.

The §31 acceptance criterion is satisfied whenever the Charcoal preset is active, and is
restated to say so.

## Amendment required

PRD §7.1 and the document-control row are revised to describe Navy as a shipped preset rather
than the default. §31 is restated to test the Charcoal preset specifically.
