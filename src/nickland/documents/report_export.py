"""A report written to PDF, Excel and CSV. PRD §29.8.

All three are written from the one ``Report`` the screen draws, after it has been rounded
once (``domain.reporting.normalise``). CSV and Excel carry each figure as the exact decimal
the screen shows; the PDF writes it out exactly as the screen does: grouped with a space,
negatives in parentheses, the currency stated once in the header.
"""

from __future__ import annotations

import csv
import html
import io
import re
import zipfile
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from nickland.documents.styles import BAND, BRAND_NAVY, BRAND_TEAL, INK, MUTED, RULE, RULE_STRONG
from nickland.domain.reporting import ColumnKind, Report, Row, Table, Value, encode

NBSP_FREE = re.compile(r"[^A-Za-z0-9]+")


# -- presentation --------------------------------------------------------------------------


def _group(whole: str) -> str:
    return re.sub(r"\B(?=(\d{3})+(?!\d))", " ", whole)


def present(value: Value, kind: ColumnKind) -> str:
    """A figure as the screen writes it. Mirrors ``figure()`` in ``ui/src/lib/format.ts``."""
    if value is None or value == "":
        return ""
    if kind is ColumnKind.DATE and isinstance(value, date):
        return f"{value:%d %b %Y}"
    if kind is ColumnKind.TEXT or not isinstance(value, (Decimal, int)):
        return str(value)
    text = format(Decimal(value), "f")
    negative = text.startswith("-")
    digits = text.lstrip("-")
    whole, _, fraction = digits.partition(".")
    if kind is ColumnKind.MONEY:
        body = f"{_group(whole)}.{(fraction + '00')[:2]}"
        return f"({body})" if negative else body
    if kind is ColumnKind.COUNT:
        return ("-" if negative else "") + _group(whole)
    fraction = fraction.rstrip("0")
    body = _group(whole) + (f".{fraction}" if fraction else "")
    body = ("-" if negative else "") + body
    if kind is ColumnKind.PERCENT:
        return f"{body}%"
    return body


def filename(report: Report, extension: str) -> str:
    stem = NBSP_FREE.sub("-", f"{report.title} {report.header.period}").strip("-").lower()
    return f"{stem}.{extension}"


def _header_lines(report: Report) -> list[tuple[str, str]]:
    head = report.header
    lines = [
        ("Business", head.business),
        ("Period", head.period),
        ("Currency", head.currency),
        ("Accounting basis", head.basis),
        (
            "Generated",
            f"{head.generated_at:%d %b %Y %H:%M} UTC"
            + (f" by {head.generated_by}" if head.generated_by else ""),
        ),
    ]
    if head.comparison:
        lines.append(
            (
                "Compared with",
                f"{head.comparison}"
                + (
                    f", {head.comparison_starts_on:%d %b %Y} to {head.comparison_ends_on:%d %b %Y}"
                    if head.comparison_starts_on and head.comparison_ends_on
                    else ""
                ),
            )
        )
    lines.extend(head.filters)
    if report.withheld:
        lines.append(("Withheld", "Cost and value columns, which you may not see"))
    return lines


def _cells(table: Table, row: Row) -> list[Value]:
    return [row.cells.get(column.key) for column in table.columns]


# -- CSV -----------------------------------------------------------------------------------


def to_csv(report: Report, notes: dict[str, str] | None = None) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([report.title])
    for label, value in _header_lines(report):
        writer.writerow([label, value])
    for section in report.sections:
        writer.writerow([])
        writer.writerow([section.title])
        if section.figures:
            writer.writerow(["Figure", "Value", "Prior", "Change %"])
            for figure in section.figures:
                writer.writerow(
                    [
                        figure.label,
                        encode(figure.value),
                        encode(figure.prior),
                        encode(figure.change),
                    ]
                )
        for table in section.tables:
            writer.writerow([])
            writer.writerow([table.title])
            writer.writerow([column.label for column in table.columns])
            for row in [*table.rows, *([table.total] if table.total else [])]:
                writer.writerow(
                    ["" if value is None else encode(value) for value in _cells(table, row)]
                )
        if section.checks:
            writer.writerow([])
            writer.writerow(["Check", "Reported", "Ledger", "Difference", "Agrees"])
            for check in section.checks:
                writer.writerow(
                    [
                        check.label,
                        encode(check.reported),
                        encode(check.ledger),
                        encode(check.difference),
                        "Yes" if check.passed else "No",
                    ]
                )
        for sentence in section.commentary:
            writer.writerow([sentence])
        if notes and notes.get(section.key):
            writer.writerow(["Note", notes[section.key]])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# -- Excel ---------------------------------------------------------------------------------

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="5">
<numFmt numFmtId="164" formatCode="#,##0.00;(#,##0.00)"/>
<numFmt numFmtId="165" formatCode="0.0&quot;%&quot;"/>
<numFmt numFmtId="166" formatCode="dd mmm yyyy"/>
<numFmt numFmtId="167" formatCode="#,##0.###"/>
<numFmt numFmtId="168" formatCode="#,##0"/>
</numFmts>
<fonts count="4">
<font><sz val="10"/><name val="Calibri"/></font>
<font><b/><sz val="10"/><name val="Calibri"/></font>
<font><b/><sz val="14"/><name val="Calibri"/></font>
<font><sz val="9"/><color rgb="FF5B6B79"/><name val="Calibri"/></font>
</fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF2F6F7"/></patternFill></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="13">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="167" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="168" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="168" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="167" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
</cellXfs>
</styleSheet>"""

PLAIN, BOLD, MONEY, MONEY_BOLD, PERCENT, DATE, QUANTITY, TITLE, MUTED_TEXT, HEAD = range(10)
COUNT, COUNT_BOLD, QUANTITY_BOLD = 10, 11, 12
EPOCH = date(1899, 12, 30)


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


class _Sheet:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.widths: dict[int, int] = {}

    def add(self, cells: list[tuple[Value, int]]) -> None:
        number = len(self.rows) + 1
        parts = []
        for index, (value, style) in enumerate(cells):
            if value is None or value == "":
                continue
            ref = f"{_column(index)}{number}"
            if isinstance(value, bool):
                value = "Yes" if value else "No"
            if isinstance(value, date):
                serial = (value - EPOCH).days
                parts.append(f'<c r="{ref}" s="{DATE}"><v>{serial}</v></c>')
                self.widths[index] = max(self.widths.get(index, 0), 12)
            elif isinstance(value, (Decimal, int)):
                parts.append(f'<c r="{ref}" s="{style}"><v>{format(Decimal(value), "f")}</v></c>')
                self.widths[index] = max(self.widths.get(index, 0), 14)
            else:
                text = str(value)
                parts.append(
                    f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(text)}</t></is></c>"
                )
                if style not in (TITLE, MUTED_TEXT):
                    self.widths[index] = max(self.widths.get(index, 0), min(len(text) + 2, 60))
        self.rows.append(f'<row r="{number}">{"".join(parts)}</row>')

    def blank(self) -> None:
        self.rows.append(f'<row r="{len(self.rows) + 1}"/>')

    def xml(self) -> str:
        cols = "".join(
            f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>'
            for index, width in sorted(self.widths.items())
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + (f"<cols>{cols}</cols>" if cols else "")
            + f"<sheetData>{''.join(self.rows)}</sheetData></worksheet>"
        )


def _style(kind: ColumnKind, bold: bool) -> int:
    if kind is ColumnKind.MONEY:
        return MONEY_BOLD if bold else MONEY
    if kind is ColumnKind.PERCENT:
        return PERCENT
    if kind is ColumnKind.COUNT:
        return COUNT_BOLD if bold else COUNT
    if kind in (ColumnKind.QUANTITY, ColumnKind.DAYS):
        return QUANTITY_BOLD if bold else QUANTITY
    return BOLD if bold else PLAIN


def to_xlsx(report: Report, notes: dict[str, str] | None = None) -> bytes:
    sheet = _Sheet()
    sheet.add([(report.title, TITLE)])
    for label, value in _header_lines(report):
        sheet.add([(label, MUTED_TEXT), (value, PLAIN)])
    for section in report.sections:
        sheet.blank()
        sheet.add([(section.title, TITLE)])
        if section.figures:
            sheet.add([("Figure", HEAD), ("Value", HEAD), ("Prior", HEAD), ("Change %", HEAD)])
            for figure in section.figures:
                style = _style(figure.kind, False)
                sheet.add(
                    [
                        (figure.label, PLAIN),
                        (figure.value, style),
                        (figure.prior, style),
                        (figure.change, PERCENT),
                    ]
                )
        for table in section.tables:
            sheet.blank()
            sheet.add([(table.title, BOLD)])
            sheet.add([(column.label, HEAD) for column in table.columns])
            for row in [*table.rows, *([table.total] if table.total else [])]:
                bold = row.emphasis in ("subtotal", "total", "heading")
                sheet.add(
                    [
                        (value, _style(column.kind, bold))
                        for column, value in zip(table.columns, _cells(table, row), strict=True)
                    ]
                )
            if table.note:
                sheet.add([(table.note, MUTED_TEXT)])
        if section.checks:
            sheet.blank()
            sheet.add(
                [
                    ("Check", HEAD),
                    ("Reported", HEAD),
                    ("Ledger", HEAD),
                    ("Difference", HEAD),
                    ("Agrees", HEAD),
                ]
            )
            for check in section.checks:
                sheet.add(
                    [
                        (check.label, PLAIN),
                        (check.reported, MONEY),
                        (check.ledger, MONEY),
                        (check.difference, MONEY),
                        ("Yes" if check.passed else "No", PLAIN),
                    ]
                )
        for sentence in section.commentary:
            sheet.add([(sentence, PLAIN)])
        if notes and notes.get(section.key):
            sheet.add([("Note", BOLD), (notes[section.key], PLAIN)])

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/></Relationships>',
        )
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", sheet.xml())
    return buffer.getvalue()


# -- PDF -----------------------------------------------------------------------------------


def _stylesheet(landscape: bool) -> str:
    return f"""
@page {{
  size: A4 {"landscape" if landscape else "portrait"};
  margin: 16mm 14mm 18mm 14mm;
  @bottom-left {{ content: string(reportname); font-size: 7.5pt; color: {MUTED}; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages);
                  font-size: 7.5pt; color: {MUTED}; }}
}}
* {{ box-sizing: border-box; }}
body {{ font-family: "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 8.6pt;
       color: {INK}; line-height: 1.4; }}
.masthead {{ display: flex; justify-content: space-between; align-items: flex-end;
            border-bottom: 2pt solid {BRAND_NAVY}; padding-bottom: 8pt; margin-bottom: 10pt; }}
@page {{ @bottom-left {{ font-family: "Inter", "Helvetica Neue", Arial, sans-serif; }}
         @bottom-right {{ font-family: "Inter", "Helvetica Neue", Arial, sans-serif; }} }}
.masthead h1 {{ white-space: nowrap; font-size: 17pt; margin: 0; color: {BRAND_NAVY}; letter-spacing: -0.01em;
               string-set: reportname content(); }}
.masthead .business {{ font-size: 9.5pt; font-weight: 600; color: {BRAND_TEAL}; }}
.masthead .period {{ font-size: 11pt; font-weight: 600; text-align: right; }}
dl.facts {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 4pt 16pt;
           margin: 0 0 14pt; }}
dl.facts div {{ break-inside: avoid; }}
dl.facts dt {{ font-size: 7pt; text-transform: uppercase; letter-spacing: 0.06em;
              color: {MUTED}; }}
dl.facts dd {{ margin: 0; font-weight: 500; }}
section {{ margin-top: 14pt; }}
section.page {{ break-before: page; }}
h2 {{ font-size: 12pt; color: {BRAND_NAVY}; margin: 0 0 6pt; border-bottom: 0.6pt solid {RULE};
     padding-bottom: 3pt; break-after: avoid; }}
h3 {{ font-size: 9.5pt; margin: 10pt 0 4pt; break-after: avoid; }}
.figures {{ display: flex; flex-wrap: wrap; gap: 6pt; margin-bottom: 6pt; }}
.figure {{ flex: 1 1 22%; background: {BAND}; padding: 6pt 8pt; border-radius: 3pt;
          break-inside: avoid; }}
.figure .label {{ font-size: 7.2pt; color: {MUTED}; }}
.figure .value {{ font-size: 12pt; font-weight: 600; font-variant-numeric: tabular-nums; }}
.figure .prior {{ font-size: 7.2pt; color: {MUTED}; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 4pt; }}
thead {{ display: table-header-group; }}
th {{ text-align: left; font-size: 7.2pt; font-weight: 600; color: {MUTED};
     border-bottom: 0.8pt solid {RULE_STRONG}; padding: 3pt 4pt; }}
td {{ padding: 2.6pt 4pt; border-bottom: 0.4pt solid {RULE}; vertical-align: top; }}
tr {{ break-inside: avoid; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.heading td {{ font-weight: 600; color: {BRAND_NAVY}; border-bottom: none; padding-top: 7pt; }}
tr.subtotal td {{ font-weight: 600; border-top: 0.6pt solid {RULE_STRONG}; }}
tr.total td {{ font-weight: 700; border-top: 0.8pt solid {INK};
              border-bottom: 1.6pt double {INK}; }}
td.level1 {{ padding-left: 14pt; }}
.note, .empty {{ font-size: 7.4pt; color: {MUTED}; margin: 2pt 0 6pt; }}
.commentary p {{ margin: 3pt 0; }}
.checks td.state {{ font-weight: 600; }}
.annotation {{ border: 0.6pt solid {RULE_STRONG}; padding: 6pt 8pt; margin-top: 6pt;
              border-radius: 3pt; }}
.annotation strong {{ display: block; font-size: 7.2pt; color: {MUTED}; }}
"""


def to_html(report: Report, notes: dict[str, str] | None = None) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    wide = any(len(table.columns) > 8 for section in report.sections for table in section.tables)
    head = report.header
    facts = "".join(
        f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>"
        for label, value in _header_lines(report)
        if label not in ("Business", "Period")
    )
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(report.title)}</title>"
        f"<style>{_stylesheet(wide)}</style></head><body>",
        "<header class='masthead'><div>"
        f"<div class='business'>{esc(head.business)}</div><h1>{esc(report.title)}</h1></div>"
        f"<div class='period'>{esc(head.period)}</div></header>",
        f"<dl class='facts'>{facts}</dl>",
    ]
    many = len(report.sections) > 4
    for index, section in enumerate(report.sections):
        page = (
            " page"
            if many
            and index > 0
            and section.tables
            and any(len(table.rows) > 18 for table in section.tables)
            else ""
        )
        parts.append(f"<section class='{page.strip()}'><h2>{esc(section.title)}</h2>")
        if section.figures:
            parts.append("<div class='figures'>")
            for figure in section.figures:
                prior = ""
                if figure.prior is not None:
                    prior = (
                        f"<div class='prior'>{esc(head.comparison or 'Prior')}: "
                        f"{esc(present(figure.prior, figure.kind))}"
                    )
                    if figure.change is not None:
                        prior += f" ({esc(present(figure.change, ColumnKind.PERCENT))})"
                    prior += "</div>"
                parts.append(
                    f"<div class='figure'><div class='label'>{esc(figure.label)}</div>"
                    f"<div class='value'>{esc(present(figure.value, figure.kind)) or '—'}</div>"
                    f"{prior}</div>"
                )
            parts.append("</div>")
        for table in section.tables:
            if len(section.tables) > 1 or table.title != section.title:
                parts.append(f"<h3>{esc(table.title)}</h3>")
            if not table.rows:
                parts.append(f"<p class='empty'>{esc(table.empty)}</p>")
                continue
            parts.append("<table><thead><tr>")
            for column in table.columns:
                numeric = " class='num'" if column.kind.numeric else ""
                parts.append(f"<th{numeric}>{esc(column.label)}</th>")
            parts.append("</tr></thead><tbody>")
            for row in [*table.rows, *([table.total] if table.total else [])]:
                parts.append(f"<tr class='{row.emphasis or ''}'>")
                for position, column in enumerate(table.columns):
                    classes = []
                    if column.kind.numeric:
                        classes.append("num")
                    if position == 0 and row.level:
                        classes.append("level1")
                    attribute = f" class='{' '.join(classes)}'" if classes else ""
                    parts.append(
                        f"<td{attribute}>{esc(present(row.cells.get(column.key), column.kind))}"
                        "</td>"
                    )
                parts.append("</tr>")
            parts.append("</tbody></table>")
            if table.note:
                parts.append(f"<p class='note'>{esc(table.note)}</p>")
        if section.checks:
            parts.append(
                "<h3>Reconciliation to the ledger</h3><table class='checks'><thead><tr>"
                "<th>Check</th><th class='num'>Reported</th><th class='num'>Ledger</th>"
                "<th class='num'>Difference</th><th>Agrees</th></tr></thead><tbody>"
            )
            for check in section.checks:
                parts.append(
                    f"<tr><td>{esc(check.label)}</td>"
                    f"<td class='num'>{esc(present(check.reported, ColumnKind.MONEY))}</td>"
                    f"<td class='num'>{esc(present(check.ledger, ColumnKind.MONEY))}</td>"
                    f"<td class='num'>{esc(present(check.difference, ColumnKind.MONEY))}</td>"
                    f"<td class='state'>{'Yes' if check.passed else 'No'}</td></tr>"
                )
            parts.append("</tbody></table>")
        if section.commentary:
            parts.append(
                "<div class='commentary'>"
                + "".join(f"<p>{esc(sentence)}</p>" for sentence in section.commentary)
                + "</div>"
            )
        if notes and notes.get(section.key):
            parts.append(
                f"<div class='annotation'><strong>Note</strong>{esc(notes[section.key])}</div>"
            )
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def to_pdf(report: Report, notes: dict[str, str] | None = None) -> bytes:
    from weasyprint import HTML

    content = HTML(string=to_html(report, notes), base_url=None).write_pdf()
    assert content is not None
    return bytes(content)


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def export(report: Report, fmt: str, notes: dict[str, str] | None = None) -> tuple[bytes, str, str]:
    """The report in a format: content, media type and file name."""
    writer = {"pdf": to_pdf, "xlsx": to_xlsx, "csv": to_csv}[fmt]
    return writer(report, notes), MEDIA_TYPES[fmt], filename(report, fmt)


__all__ = ["MEDIA_TYPES", "export", "filename", "present", "to_csv", "to_html", "to_pdf", "to_xlsx"]
