"""Portable XLSX download for the local Streamlit app."""
from io import BytesIO
import json
import math

import xlsxwriter

MAX_DATA_ROWS = 1_048_575
MAX_CELL_TEXT = 32_767


def _value(value):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 10**15:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, str):
        return value[:MAX_CELL_TEXT]
    return value


def _write_sheets(workbook, rows, sheet_name, header):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    for start in range(0, max(len(rows), 1), MAX_DATA_ROWS):
        number = start // MAX_DATA_ROWS + 1
        sheet = workbook.add_worksheet(sheet_name if number == 1 else f"{sheet_name} {number}")
        sheet.freeze_panes(1, 0)
        if not columns:
            sheet.write_string(0, 0, "Tidak ada hasil")
            continue
        data = rows[start:start + MAX_DATA_ROWS]
        sheet.write_row(0, 0, columns, header)
        for index, column in enumerate(columns):
            sheet.set_column(index, index, min(45, max(15, len(column) + 3)))
        sheet.autofilter(0, 0, len(data), len(columns) - 1)
        for index, row in enumerate(data, start=1):
            sheet.write_row(index, 0, [_value(row.get(column)) for column in columns])


def xlsx_bytes(rows, *, profile_metrics=()):
    output = BytesIO()
    options = {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
    with xlsxwriter.Workbook(output, options) as workbook:
        header = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#174A65"})
        _write_sheets(workbook, rows, "Hasil", header)
        if profile_metrics:
            _write_sheets(workbook, profile_metrics, "Metrik profil", header)
    return output.getvalue()
