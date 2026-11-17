"""Minimal, dependency-free ``.xlsx`` writer (Office Open XML spreadsheet).

Why not openpyxl?  The bot runs on Vercel where the install set is pinned to
``aiogram`` + ``fastapi`` + ``psycopg``. Producing a real Excel file needs only
a zip and a few XML parts, so this module keeps the deployment small and the
runtime predictable. Everything Excel/Sheets/LibreOffice needs is generated:

* ``[Content_Types].xml`` + package relationships,
* ``xl/workbook.xml`` (one entry per sheet),
* ``xl/styles.xml`` (header band + right-aligned RTL body),
* one ``xl/worksheets/sheetN.xml`` per sheet, with ``rightToLeft="1"`` and a
  frozen header row.

Cell values are written as inline strings (so Arabic needs no shared-string
table) or as numbers, which keeps sums working after export.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ILLEGAL_SHEET = re.compile(r"[\[\]:*?/\\]")


def _escape(text: str) -> str:
    cleaned = _ILLEGAL_XML.sub("", str(text))
    return (
        cleaned.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _sheet_name(name: str, used: set[str]) -> str:
    clean = _ILLEGAL_SHEET.sub(" ", name or "ورقة").strip() or "ورقة"
    clean = clean[:31]
    candidate = clean
    counter = 2
    while candidate.lower() in used:
        suffix = f" {counter}"
        candidate = clean[: 31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate.lower())
    return candidate


@dataclass
class Sheet:
    """One worksheet: a header row plus body rows of plain Python values."""

    name: str
    header: Sequence[str]
    rows: Iterable[Sequence[Any]] = field(default_factory=list)
    widths: Sequence[float] | None = None


def _cell_value(value: Any) -> tuple[str, str] | None:
    """Return ``(type, rendered)`` where type is ``n`` (number) or ``s``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "s", "نعم" if value else "لا"
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        text = format(number.normalize(), "f")
        return "n", text
    if isinstance(value, (date, datetime)):
        return "s", value.isoformat()[:10]
    text = str(value).strip()
    if not text:
        return None
    return "s", text


def _sheet_xml(sheet: Sheet) -> str:
    header = list(sheet.header)
    rows = [list(row) for row in sheet.rows]
    width_count = len(header)
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView rightToLeft="1" workbookViewId="0">',
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>',
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>',
        "</sheetView></sheetViews>",
        '<sheetFormatPr defaultRowHeight="15"/>',
    ]
    if sheet.widths:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="{float(width):.1f}" customWidth="1"/>'
            for i, width in enumerate(sheet.widths, start=1)
        )
    else:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="22" customWidth="1"/>'
            for i in range(1, max(width_count, 1) + 1)
        )
    parts.append(f"<cols>{cols}</cols>")
    parts.append("<sheetData>")

    parts.append('<row r="1" ht="22" customHeight="1">')
    for index, title in enumerate(header, start=1):
        ref = f"{column_letter(index)}1"
        parts.append(
            f'<c r="{ref}" s="1" t="inlineStr"><is><t xml:space="preserve">'
            f"{_escape(title)}</t></is></c>"
        )
    parts.append("</row>")

    for row_number, row in enumerate(rows, start=2):
        parts.append(f'<row r="{row_number}">')
        for index, value in enumerate(row, start=1):
            rendered = _cell_value(value)
            if rendered is None:
                continue
            kind, text = rendered
            ref = f"{column_letter(index)}{row_number}"
            if kind == "n":
                parts.append(f'<c r="{ref}" s="2"><v>{text}</v></c>')
            else:
                parts.append(
                    f'<c r="{ref}" s="0" t="inlineStr"><is><t xml:space="preserve">'
                    f"{_escape(text)}</t></is></c>"
                )
        parts.append("</row>")

    parts.append("</sheetData></worksheet>")
    return "".join(parts)


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border>
<left style="thin"><color rgb="FFCFDEDB"/></left>
<right style="thin"><color rgb="FFCFDEDB"/></right>
<top style="thin"><color rgb="FFCFDEDB"/></top>
<bottom style="thin"><color rgb="FFCFDEDB"/></bottom>
<diagonal/>
</border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"
    applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1"
    applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"
    wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"
    applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""


def build_workbook(sheets: Sequence[Sheet]) -> bytes:
    """Render ``sheets`` into the bytes of an ``.xlsx`` file."""
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")

    used: set[str] = set()
    named = [(Sheet(_sheet_name(sheet.name, used), sheet.header, sheet.rows, sheet.widths)) for sheet in sheets]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(named) + 1)
        )
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}"
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        sheet_tags = "".join(
            f'<sheet name="{_escape(sheet.name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, sheet in enumerate(named, start=1)
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheet_tags}</sheets>"
            "</workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(named) + 1)
        )
        rels += (
            f'<Relationship Id="rId{len(named) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}"
            "</Relationships>",
        )
        archive.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(named, start=1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))

    return buffer.getvalue()
