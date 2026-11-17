"""The dependency-free .xlsx writer, verified by reading the file back with openpyxl."""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

import openpyxl

from app.core.xlsx import Sheet, build_workbook, column_letter


def test_column_letters() -> None:
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_letter(52) == "AZ"


def test_workbook_roundtrip_preserves_types_and_arabic() -> None:
    data = build_workbook(
        [
            Sheet(
                "ملخص",
                ["البند", "القيمة"],
                [
                    ["عدد المباني", 3],
                    ["إجمالي", Decimal("12500.50")],
                    ["تاريخ", dt.date(2026, 11, 17)],
                    ["ملاحظة", "مبنى النور & شقة 3"],
                ],
                widths=[30, 20],
            ),
            Sheet("الدفعات", ["التاريخ", "المبلغ"], [["2026-11-01", 5000]]),
        ]
    )
    assert data[:2] == b"PK"  # a real zip container

    book = openpyxl.load_workbook(io.BytesIO(data))
    assert book.sheetnames == ["ملخص", "الدفعات"]

    sheet = book["ملخص"]
    assert [cell.value for cell in sheet[1]] == ["البند", "القيمة"]
    assert sheet["A1"].font.bold is True
    assert sheet["A1"].fill.fgColor.rgb.endswith("0F766E")
    assert sheet.sheet_view.rightToLeft is True
    assert sheet.freeze_panes == "A2"

    assert sheet["B2"].value == 3 and isinstance(sheet["B2"].value, int)
    assert float(sheet["B3"].value) == 12500.5
    assert sheet["B4"].value == "2026-11-17"
    assert sheet["B5"].value == "مبنى النور & شقة 3"

    assert book["الدفعات"]["B2"].value == 5000


def test_empty_sheet_still_opens() -> None:
    data = build_workbook([Sheet("فارغ", ["الاسم", "الهاتف"], [])])
    book = openpyxl.load_workbook(io.BytesIO(data))
    sheet = book["فارغ"]
    assert sheet.max_row == 1
    assert [cell.value for cell in sheet[1]] == ["الاسم", "الهاتف"]


def test_sheet_names_are_sanitised_and_unique() -> None:
    data = build_workbook(
        [
            Sheet("a/b:c", ["x"], [[1]]),
            Sheet("a b c", ["x"], [[2]]),
            Sheet("", ["x"], [[3]]),
        ]
    )
    book = openpyxl.load_workbook(io.BytesIO(data))
    assert len(book.sheetnames) == 3
    assert len({name.lower() for name in book.sheetnames}) == 3
    assert all("/" not in name and ":" not in name for name in book.sheetnames)


def test_formula_like_text_is_stored_as_string() -> None:
    data = build_workbook([Sheet("s", ["v"], [["=SUM(A1:A2)"], ["<b>bold</b>"]])])
    book = openpyxl.load_workbook(io.BytesIO(data))
    sheet = book["s"]
    assert sheet["A2"].value == "=SUM(A1:A2)"
    assert sheet["A2"].data_type == "s"
    assert sheet["A3"].value == "<b>bold</b>"
