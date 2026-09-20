"""Reusable CSV / XLSX export engine (BUSINESS_RULES X1-X4).

A dataset is described by a list of `Column`s and a list of row dicts. This module knows nothing about
products or stock, so purchases, sales, returns, expenses, khata and reports can reuse it in later phases:
each just supplies its columns and rows.

Safety and Excel-friendliness:
  * CSV is UTF-8 **with BOM**, so Excel shows Hindi text and the rupee sign correctly.
  * XLSX cells are real Excel types: numbers are numbers, dates are date cells (formatted dd/mm/yyyy).
  * **Formula injection**: a text value that starts with `=`, `+`, `-`, `@`, tab or carriage return could be
    run as a formula when the file is opened in Excel/Sheets. Such text gets a leading apostrophe, which
    makes spreadsheets treat it as plain text. Only *text* is changed; genuine numbers such as -5 are not.
  * Amounts are written from `Decimal`, never through a float.
Callers must pass only rows that already belong to the current shop; this module does not query anything.
"""

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CSV_BOM = "﻿"
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class Kind(StrEnum):
    """How a column is stored in the file."""

    TEXT = "text"
    INTEGER = "integer"
    MONEY = "money"  # 2 decimals
    QUANTITY = "quantity"  # 3 decimals
    DATE = "date"
    DATETIME = "datetime"


@dataclass(frozen=True)
class Column:
    key: str  # key in each row dict
    header: str
    kind: Kind = Kind.TEXT


@dataclass(frozen=True)
class ExportFile:
    filename: str
    media_type: str
    content: bytes


_NUMBER_FORMATS = {
    Kind.INTEGER: "0",
    Kind.MONEY: "#,##0.00",
    Kind.QUANTITY: "#,##0.000",
    Kind.DATE: "dd/mm/yyyy",
    Kind.DATETIME: "dd/mm/yyyy hh:mm",
}


def neutralize_formula(value: str) -> str:
    """Make text safe to open in a spreadsheet. Text that could be read as a formula gets a leading '."""
    return "'" + value if value.startswith(DANGEROUS_PREFIXES) else value


def _clean(value: Any) -> Any:
    return neutralize_formula(value) if isinstance(value, str) else value


def _csv_text(value: Any, kind: Kind) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return neutralize_formula(value)
    return str(value)


def render_csv(columns: Sequence[Column], rows: Iterable[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([neutralize_formula(column.header) for column in columns])
    for row in rows:
        writer.writerow([_csv_text(row.get(column.key), column.kind) for column in columns])
    return (CSV_BOM + buffer.getvalue()).encode("utf-8")


def render_xlsx(columns: Sequence[Column], rows: Iterable[Mapping[str, Any]], *, sheet_name: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]  # Excel's limit

    sheet.append([neutralize_formula(column.header) for column in columns])
    header_fill = PatternFill("solid", start_color="E2F0E8")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"

    widths = [len(column.header) for column in columns]
    for row in rows:
        values = [_clean(row.get(column.key)) for column in columns]
        sheet.append(values)
        for index, (column, value) in enumerate(zip(columns, values, strict=True)):
            cell = sheet.cell(row=sheet.max_row, column=index + 1)
            if value is None:
                continue
            if column.kind in _NUMBER_FORMATS:
                cell.number_format = _NUMBER_FORMATS[column.kind]
            if isinstance(value, str):
                cell.data_type = "s"  # never let the library interpret text as a formula
            widths[index] = max(widths[index], len(str(value)))

    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 8), 50)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def render(
    export_format: ExportFormat,
    *,
    name: str,
    columns: Sequence[Column],
    rows: Iterable[Mapping[str, Any]],
    on_date: date,
) -> ExportFile:
    """Build the downloadable file. `name` is a slug like "products"; `on_date` is the shop's local date."""
    stem = f"{name}_{on_date.isoformat()}"
    if export_format is ExportFormat.CSV:
        return ExportFile(f"{stem}.csv", CSV_MEDIA_TYPE, render_csv(columns, rows))
    return ExportFile(
        f"{stem}.xlsx", XLSX_MEDIA_TYPE, render_xlsx(columns, rows, sheet_name=name.replace("_", " ").title())
    )
