from datetime import date
from decimal import Decimal

import pytest

from kbank_pdf import StatementRow
from reconcile import CsvError, load_csv, reconcile


def row(day, description, amount, month=2, posting=None):
    booked = date(2026, month, day)
    return StatementRow(
        trans_date=booked,
        posting_date=posting or booked,
        description=description,
        amount=Decimal(amount),
    )


def csv_text(lines):
    header = "Date,Amount,Category,Subcategory,Payment Method,Description\n"
    return (header + "\n".join(lines) + "\n").encode()


def test_duplicate_amounts_use_trans_date():
    statement = [row(1, "NORTH CAFE", "12.50"), row(3, "NORTH CAFE", "12.50")]
    csv_rows = load_csv(
        csv_text(
            [
                "2026-02-03,-12.50,Food,Cafe,Credit Card,North Cafe",
                "2026-02-01,-12.50,Food,Cafe,Cash,North Cafe",
            ]
        )
    )
    result = reconcile(statement, csv_rows)
    assert [(pair.statement.trans_date.day, pair.csv.date.day, pair.flag) for pair in result.matched] == [
        (1, 1, "date"),
        (3, 3, "date"),
    ]
    assert result.unmatched_pdf == []


def test_unique_amount_matches_when_date_differs():
    statement = [row(4, "BLUE BOOKSHOP", "40.00")]
    csv_rows = load_csv(csv_text(["2026-02-05,-40.00,Personal,Books,Credit Card,Blue Bookshop"]))
    result = reconcile(statement, csv_rows)
    assert len(result.matched) == 1
    assert result.matched[0].flag == "date differs"
    assert result.unmatched_pdf == []


def test_leftover_duplicates_pair_in_order_and_flag_amount_only():
    statement = [
        row(1, "NORTH CAFE", "12.50"),
        row(3, "CITY PARKING", "12.50"),
        row(6, "NORTH CAFE", "12.50"),
    ]
    csv_rows = load_csv(
        csv_text(
            [
                "2026-02-01,-12.50,Food,Cafe,Credit Card,North Cafe",
                "2026-02-09,-12.50,Food,Cafe,Credit Card,North Cafe",
            ]
        )
    )
    result = reconcile(statement, csv_rows)
    assert [(pair.statement.trans_date.day, pair.flag) for pair in result.matched] == [
        (1, "date"),
        (3, "amount only"),
    ]
    assert [item.trans_date.day for item in result.unmatched_pdf] == [6]
    assert result.unused_csv == []


def test_reversal_prefers_same_description():
    statement = [
        row(4, "OTHER SHOP", "40.00"),
        row(4, "BLUE BOOKSHOP", "40.00"),
        row(5, "BLUE BOOKSHOP", "-40.00"),
    ]
    result = reconcile(statement, load_csv(csv_text(["2026-02-04,-40.00,Personal,Books,Credit Card,Other Shop"])))
    assert len(result.reversals) == 1
    assert result.reversals[0].positive.description == "BLUE BOOKSHOP"
    assert result.reversals_removed == 2
    assert result.unmatched_pdf == []
    assert result.matched[0].statement.description == "OTHER SHOP"


def test_payment_is_ignored_even_when_csv_has_the_amount():
    statement = [
        row(8, "PAYMENT - THANK YOU - APP", "-100.00"),
        row(8, "NORTH CAFE", "12.50"),
    ]
    csv_rows = load_csv(
        csv_text(
            [
                "2026-02-08,-100.00,Uncategorized,,Credit Card,Payment",
                "2026-02-08,-12.50,Food,Cafe,Credit Card,North Cafe",
            ]
        )
    )
    result = reconcile(statement, csv_rows)
    assert len(result.payments) == 1
    assert result.payments[0].amount == Decimal("-100.00")
    assert result.reversals == []
    assert [pair.statement.description for pair in result.matched] == ["NORTH CAFE"]
    assert result.unused_csv[0].amount == Decimal("100.00")


def test_unpaired_negative_is_not_a_purchase():
    statement = [row(2, "SOMETHING ELSE", "-15.00")]
    result = reconcile(statement, load_csv(csv_text(["2026-02-02,-15.00,Food,Cafe,Credit Card,Something"])))
    assert result.unpaired_reversals[0].description == "SOMETHING ELSE"
    assert result.matched == []
    assert result.unmatched_pdf == []
    assert len(result.unused_csv) == 1


def test_each_csv_row_is_used_once():
    statement = [row(5, "BLUE BOOKSHOP", "40.00"), row(5, "BLUE BOOKSHOP", "40.00")]
    csv_rows = load_csv(csv_text(["2026-02-05,-40.00,Personal,Books,Credit Card,Blue Bookshop"]))
    result = reconcile(statement, csv_rows)
    assert len(result.matched) == 1
    assert len(result.unmatched_pdf) == 1
    assert result.unused_csv == []


def test_csv_strips_debit_sign_and_accepts_bom():
    raw = b"\xef\xbb\xbf\nDate,Amount,Category,Description\n2026-02-01,-12.50,Food,North Cafe\n"
    rows = load_csv(raw)
    assert rows[0].amount == Decimal("12.50")
    assert rows[0].description == "North Cafe"


def test_missing_amount_column():
    with pytest.raises(CsvError, match="Amount"):
        load_csv(b"Date,Category\n2026-02-01,Food\n")


def test_bad_csv_row_reports_the_line():
    with pytest.raises(CsvError, match="row 2"):
        load_csv(b"Date,Amount\nnot-a-date,-10\n")
