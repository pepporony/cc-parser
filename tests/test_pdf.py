import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfWriter

from kbank_pdf import PdfError, parse_pdf

SAMPLE_PDF = Path(__file__).resolve().parents[1] / "samples" / "example-statement.pdf"


def _encrypted(password: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_missing_password_on_encrypted_pdf():
    with pytest.raises(PdfError, match="password-protected"):
        parse_pdf(_encrypted("secret"), "")


def test_wrong_password():
    with pytest.raises(PdfError, match="Wrong PDF password"):
        parse_pdf(_encrypted("secret"), "nope")


def test_right_password_on_empty_pdf_reports_no_transactions():
    with pytest.raises(PdfError, match="No transactions found"):
        parse_pdf(_encrypted("secret"), "secret")


def test_sample_statement_stops_at_total_balance():
    rows = parse_pdf(SAMPLE_PDF.read_bytes())
    assert len(rows) == 5
    assert rows[0].trans_date == date(2026, 2, 1)
    assert rows[0].posting_date == date(2026, 2, 2)
    assert rows[0].description == "NORTH CAFE"
    assert rows[0].amount == Decimal("12.50")
    assert rows[-1].description == "PAYMENT - THANK YOU - APP"
    assert rows[-1].amount == Decimal("-100.00")
    assert not any(row.amount == Decimal("99.00") for row in rows)
    assert not any("SHOULD NOT BE READ" in row.description for row in rows)
