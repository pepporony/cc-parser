"""Parse a KBank credit-card e-statement PDF.

The file is the bank original (Quadient Inspire), not a browser print.
Reading stops at the first line that contains ``TOTAL BALANCE``.
A transaction line looks like::

    01/01/26 02/01/26 EXAMPLE MERCHANT BANGKOK 10.00

The city, merchant id, or site stays inside the description. The statement
has no delimiter before that trailing location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

TWO_PLACES = Decimal("0.01")
LINE_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+(.+?)\s+(-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2})$"
)


class PdfError(Exception):
    """A statement the app can explain to the user."""


@dataclass(frozen=True)
class StatementRow:
    trans_date: date
    posting_date: date
    description: str
    amount: Decimal


def parse_pdf(data: bytes, password: str = "") -> list[StatementRow]:
    """Return statement lines in reading order.

    ``password`` is used only to decrypt an encrypted file. It is not stored.
    """
    reader = _open_reader(data, password)
    lines = _lines_until_total(reader)
    rows = []
    for line in lines:
        match = LINE_RE.match(line.strip())
        if match is None:
            continue
        rows.append(
            StatementRow(
                trans_date=_parse_date(match.group(1)),
                posting_date=_parse_date(match.group(2)),
                description=re.sub(r"\s+", " ", match.group(3)).strip(),
                amount=_money(match.group(4)),
            )
        )
    if not rows:
        raise PdfError(
            "No transactions found. Upload the KBank e-statement PDF "
            "(the file from the bank, not a browser print)."
        )
    return rows


def _open_reader(data: bytes, password: str) -> PdfReader:
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfError("Could not read this PDF.") from exc
    if not reader.is_encrypted:
        return reader
    if not password:
        raise PdfError("This PDF is password-protected.")
    try:
        status = reader.decrypt(password)
    except PdfReadError as exc:
        raise PdfError("Could not read this PDF.") from exc
    if status == 0:
        raise PdfError("Wrong PDF password.")
    return reader


def _lines_until_total(reader: PdfReader) -> list[str]:
    lines: list[str] = []
    try:
        pages = reader.pages
    except PdfReadError as exc:
        raise PdfError("Could not read this PDF.") from exc
    for page in pages:
        try:
            text = page.extract_text() or ""
        except PdfReadError as exc:
            raise PdfError("Could not read this PDF.") from exc
        for line in text.splitlines():
            if "TOTAL BALANCE" in line:
                return lines
            lines.append(line)
    return lines


def _parse_date(value: str) -> date:
    day_s, month_s, year_s = value.split("/")
    year = int(year_s)
    # Statements use a two-digit year. 00–69 are 2000–2069.
    year += 2000 if year < 70 else 1900
    return date(year, int(month_s), int(day_s))


def _money(value: str) -> Decimal:
    return Decimal(value.replace(",", "")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
