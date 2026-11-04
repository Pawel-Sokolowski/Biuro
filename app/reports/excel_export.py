"""Stream Excel output using XlsxWriter, publishing only complete workbooks."""
import os
from pathlib import Path
import tempfile

from xlsxwriter import Workbook

from app.utils.xlsx_safe import safe_row


def write_report(filepath, title, headers, rows):
    target = Path(filepath)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".xlsx", dir=target.parent)
    os.close(fd)
    try:
        with Workbook(temporary, {"constant_memory": True, "strings_to_formulas": False,
                                  "strings_to_urls": False}) as book:
            sheet = book.add_worksheet(title)
            sheet.write_row(0, 0, safe_row(headers), book.add_format({"bold": True}))
            for row_number, row in enumerate(rows, start=1):
                sheet.write_row(row_number, 0, safe_row(row))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
