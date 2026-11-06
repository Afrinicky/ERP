"""The stylesheets a document is laid out with.

Two sheets, because two media. The page sheet is for stock a person files or posts:
generous leading, a ruled table, a totals block aligned right. The roll sheet is for a
thermal printer, where there is no colour, no greyscale worth the name, and 48 to 72
millimetres to work in.

Colour comes in as literal values rather than as the interface's custom properties: a
PDF is rendered outside the browser, where no theme is installed, and a document must
look the same whichever palette the operator happens to be using.
"""

from __future__ import annotations

from nickland.domain.documents import PageFormat

INK = "#111827"
MUTED = "#5B6B79"
RULE = "#D4DCE2"
RULE_STRONG = "#9AA8B2"
BAND = "#F2F6F7"
BRAND_NAVY = "#042249"
BRAND_TEAL = "#119598"


def page_stylesheet(page: PageFormat, *, accent: str = BRAND_TEAL) -> str:
    """The sheet for printed stock: A4, A5, Letter or Legal."""
    geometry = page.geometry
    return f"""
@page {{
  size: {geometry.css_size};
  margin: {geometry.margin_top_mm}mm {geometry.margin_right_mm}mm
          {geometry.margin_bottom_mm}mm {geometry.margin_left_mm}mm;
  @bottom-left {{
    content: element(runningfoot);
  }}
  @bottom-right {{
    content: "Page " counter(page) " of " counter(pages);
    font-family: "DejaVu Sans", "Liberation Sans", sans-serif;
    font-size: 7.5pt;
    color: {MUTED};
  }}
}}

* {{ box-sizing: border-box; }}

html {{
  font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif;
  font-size: 9pt;
  line-height: 1.42;
  color: {INK};
}}

body {{ margin: 0; }}

/* ------------------------------------------------------------------ masthead */

/* The issuer's identity sits left and the mark sits right, which is the structure
   of the reference documents: the eye reads the sender, then the title, then the
   references, down the left and across to the right. */
.masthead {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10mm;
  padding-bottom: 4mm;
  border-bottom: 0.5pt solid {RULE_STRONG};
}}

.masthead__identity {{ max-width: 110mm; }}

.masthead__name {{
  margin: 0 0 1mm;
  font-size: 13pt;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: {BRAND_NAVY};
}}

.masthead__strapline {{
  margin: 0 0 2mm;
  font-size: 8.5pt;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: {accent};
}}

.masthead__address {{
  margin: 0;
  font-size: 8pt;
  line-height: 1.45;
  color: {MUTED};
}}

.masthead__address span {{ display: block; }}

.masthead__mark {{ flex: 0 0 auto; }}
.masthead__mark img, .masthead__mark svg {{ width: 24mm; height: auto; }}

/* -------------------------------------------------------------------- title */

/* The title and the reference block start together at the top. Aligning their
   baselines instead pushed the title down the page whenever the references ran to
   more than three rows, which they usually do. */
.title-row {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10mm;
  margin: 5mm 0 4mm;
}}

.title {{
  margin: 0;
  font-size: 17pt;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: {BRAND_NAVY};
}}

.references {{
  min-width: 62mm;
  font-size: 8.5pt;
}}

.references dl {{
  display: grid;
  grid-template-columns: auto auto;
  gap: 0.8mm 5mm;
  margin: 0;
  justify-content: end;
}}

.references dt {{
  font-weight: 700;
  color: {MUTED};
  text-align: right;
  white-space: nowrap;
}}
.references dd {{ margin: 0; text-align: right; font-variant-numeric: tabular-nums; }}

/* ------------------------------------------------------------------- parties */

.parties {{
  display: flex;
  gap: 8mm;
  padding: 3mm 0 4mm;
  border-top: 0.5pt solid {RULE};
  border-bottom: 0.5pt solid {RULE};
}}

.party {{ flex: 1 1 0; font-size: 8.5pt; }}

.party__label {{
  margin: 0 0 1.2mm;
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: {MUTED};
}}

.party__name {{ font-weight: 700; }}
.party p {{ margin: 0; }}
.party span {{ display: block; }}

/* --------------------------------------------------------------------- items */

.items {{
  width: 100%;
  margin-top: 5mm;
  border-collapse: collapse;
  font-size: 8.5pt;
}}

.items thead {{ display: table-header-group; }}
.items tfoot {{ display: table-row-group; }}

.items th {{
  padding: 2mm 2mm;
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  text-align: left;
  color: {BRAND_NAVY};
  background: {BAND};
  border-top: 0.5pt solid {RULE_STRONG};
  border-bottom: 0.5pt solid {RULE_STRONG};
}}

.items td {{
  padding: 1.8mm 2mm;
  vertical-align: top;
  border-bottom: 0.5pt solid {RULE};
}}

.items tbody tr {{ page-break-inside: avoid; }}

.items .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.items .sku {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
.items .idx {{ text-align: right; color: {MUTED}; width: 8mm; }}
.items__note {{ display: block; font-size: 7.5pt; color: {MUTED}; }}

.items__carried td {{
  font-weight: 700;
  background: {BAND};
}}

/* -------------------------------------------------------------------- totals */

.closing {{
  display: flex;
  gap: 10mm;
  margin-top: 5mm;
  page-break-inside: avoid;
}}

.closing__left {{ flex: 1 1 0; }}
.closing__right {{ flex: 0 0 72mm; }}

.totals {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; }}
.totals td {{ padding: 1.4mm 2mm; }}
.totals td:last-child {{
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}}
.totals tr.rule td {{ border-top: 0.5pt solid {RULE}; }}
.totals tr.grand td {{
  font-size: 10pt;
  font-weight: 700;
  color: {BRAND_NAVY};
  border-top: 1pt solid {BRAND_NAVY};
  border-bottom: 1pt solid {BRAND_NAVY};
}}

.in-words {{
  margin: 3mm 0 0;
  font-size: 8pt;
  font-style: italic;
  color: {MUTED};
}}

/* --------------------------------------------------------------------- blocks */

.block {{ margin-top: 5mm; page-break-inside: avoid; }}

.block__heading {{
  margin: 0 0 1.5mm;
  font-size: 8pt;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: {BRAND_NAVY};
}}

.block dl {{
  display: grid;
  grid-template-columns: 42mm 1fr;
  gap: 0.8mm 4mm;
  margin: 0;
  font-size: 8.5pt;
}}

.block dt {{ color: {MUTED}; }}
.block dd {{ margin: 0; }}
.block p {{ margin: 0 0 1mm; font-size: 8.5pt; }}

.standout {{
  padding: 2mm 3mm;
  font-size: 8.5pt;
  font-weight: 700;
  border-left: 1.5pt solid {accent};
  background: {BAND};
}}

/* ------------------------------------------------------------------ signature */

.signatures {{
  display: flex;
  gap: 12mm;
  margin-top: 10mm;
  page-break-inside: avoid;
}}

.signature {{ flex: 1 1 0; }}

.signature__rule {{
  height: 10mm;
  border-bottom: 0.5pt solid {RULE_STRONG};
}}

.signature__label {{
  margin: 1.2mm 0 0;
  font-size: 7.5pt;
  color: {MUTED};
}}

/* --------------------------------------------------------------------- footer */

.runningfoot {{
  position: running(runningfoot);
  font-size: 7.5pt;
  color: {MUTED};
}}

/* ------------------------------------------------------------------ watermark */

/* A preview must be unmistakable on paper as well as on screen, so the mark is
   printed rather than drawn behind the content where a mono printer would lose it. */
.watermark {{
  position: fixed;
  top: 44%;
  left: 0;
  right: 0;
  text-align: center;
  font-size: 54pt;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: rgba(17, 149, 152, 0.14);
  transform: rotate(-24deg);
}}
"""


def roll_stylesheet(page: PageFormat) -> str:
    """The sheet for a thermal roll.

    A thermal head prints black dots on heat-sensitive paper. Greys dither into
    smudge, hairlines disappear, and the paper is 48 or 72 millimetres wide. So this
    sheet uses no colour, no fill, dashed rules, and a monospaced face that keeps the
    figure columns aligned on a printer with no font of its own.
    """
    geometry = page.geometry
    narrow = page is PageFormat.ROLL_58
    base = "8pt" if narrow else "8.5pt"
    return f"""
@page {{
  size: {geometry.css_size};
  margin: {geometry.margin_top_mm}mm {geometry.margin_right_mm}mm
          {geometry.margin_bottom_mm}mm {geometry.margin_left_mm}mm;
}}

* {{ box-sizing: border-box; }}

html {{
  font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-size: {base};
  line-height: 1.3;
  color: #000000;
}}

body {{ margin: 0; }}

.receipt {{ width: 100%; }}

.receipt__name {{
  margin: 0;
  font-size: {"10pt" if narrow else "11pt"};
  font-weight: 700;
  text-align: center;
  letter-spacing: 0.02em;
}}

.receipt__strapline,
.receipt__contact {{
  margin: 0;
  text-align: center;
  font-size: {"6.5pt" if narrow else "7pt"};
}}

.receipt__title {{
  margin: 2mm 0 1mm;
  text-align: center;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}

.rule {{
  margin: 1.5mm 0;
  border: 0;
  border-top: 1px dashed #000000;
}}

.pair {{
  display: flex;
  justify-content: space-between;
  gap: 2mm;
}}

.pair span:last-child {{ text-align: right; white-space: nowrap; }}

.line {{ margin-bottom: 1mm; }}
.line__name {{ display: block; }}
.line__detail {{
  display: flex;
  justify-content: space-between;
  gap: 2mm;
}}

.total {{ font-weight: 700; font-size: {"9pt" if narrow else "10pt"}; }}

.receipt__footer {{
  margin-top: 2mm;
  text-align: center;
  font-size: {"6.5pt" if narrow else "7pt"};
}}

.receipt__words {{
  margin: 1mm 0 0;
  font-size: {"6.5pt" if narrow else "7pt"};
}}

/* The feed the cutter needs, so the last line is not sheared off. */
.receipt__feed {{ height: 10mm; }}
"""
