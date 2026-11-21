"""Match KBank statement lines to an Expense Manager CSV.

Assumptions
-----------
* Amounts are compared at two decimal places, as absolute values.
* A leading "-" on a CSV amount is a debit sign, not a refund. Every CSV row
  is a candidate. Payment Method is not a filter.
* A statement description that contains both "PAYMENT" and "THANK YOU"
  (spaces and dashes ignored) is a card payment. It is ignored. It is not
  matched and it is not a reversal.
* Any other negative statement amount is a reversal when a positive line has
  the same absolute amount. The positive counterpart is the one with the same
  description, then the same trans date, then the nearest trans date, then
  the earliest line. Both lines are dropped. A negative line with no positive
  twin is reported on its own and is not treated as a purchase.
* The match key is the absolute amount. Trans date breaks ties when more than
  one statement line has that amount: same-date CSV rows are used first.
  A statement amount that appears once still matches when the CSV date
  differs, and that pair is flagged "date differs". Leftover duplicates pair
  in statement order and are flagged "amount only".
* Each CSV row is used at most once. Extra CSV rows are expected.
* Two-digit statement years 00–69 are 2000–2069.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO

from kbank_pdf import StatementRow

TWO_PLACES = Decimal("0.01")


class CsvError(Exception):
    """A CSV the app can explain to the user."""


@dataclass(frozen=True)
class CsvRow:
    index: int
    date: date
    amount: Decimal
    category: str
    description: str


@dataclass(frozen=True)
class Reversal:
    positive: StatementRow
    negative: StatementRow


@dataclass(frozen=True)
class Pair:
    statement: StatementRow
    csv: CsvRow
    flag: str


@dataclass(frozen=True)
class ReconcileResult:
    extracted: list[StatementRow]
    payments: list[StatementRow]
    reversals: list[Reversal]
    unpaired_reversals: list[StatementRow]
    matched: list[Pair]
    unmatched_pdf: list[StatementRow]
    csv_rows: list[CsvRow]
    unused_csv: list[CsvRow]

    @property
    def kept(self) -> int:
        return len(self.matched) + len(self.unmatched_pdf)

    @property
    def reversals_removed(self) -> int:
        return len(self.reversals) * 2


def load_csv(data: bytes) -> list[CsvRow]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvError("CSV must be UTF-8.") from exc
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    reader = csv.DictReader(StringIO("\n".join(lines)))
    if not reader.fieldnames:
        raise CsvError("CSV has no header row.")
    fields = {(name or "").strip().lower(): name for name in reader.fieldnames}
    missing = [label for label in ("Date", "Amount") if label.lower() not in fields]
    if missing:
        raise CsvError("CSV is missing column(s): " + ", ".join(missing) + ".")
    date_key = fields["date"]
    amount_key = fields["amount"]
    category_key = fields.get("category")
    description_key = fields.get("description")
    rows: list[CsvRow] = []
    for number, raw in enumerate(reader, start=2):
        if raw is None or not any((value or "").strip() for value in raw.values()):
            continue
        try:
            booked = datetime.strptime((raw.get(date_key) or "").strip(), "%Y-%m-%d").date()
            amount = _money(raw.get(amount_key) or "")
        except (ValueError, ArithmeticError) as exc:
            raise CsvError(f"CSV row {number} has an unreadable date or amount.") from exc
        rows.append(
            CsvRow(
                index=number,
                date=booked,
                amount=abs(amount),
                category=(raw.get(category_key) or "").strip() if category_key else "",
                description=(raw.get(description_key) or "").strip() if description_key else "",
            )
        )
    if not rows:
        raise CsvError("CSV has no data rows.")
    return rows


def reconcile(statement: list[StatementRow], csv_rows: list[CsvRow]) -> ReconcileResult:
    payments = [row for row in statement if is_payment(row.description)]
    rest = [row for row in statement if not is_payment(row.description)]
    reversals, unpaired, kept = _drop_reversals(rest)
    matched, unmatched, used = _match(kept, csv_rows)
    unused = [row for row in csv_rows if row.index not in used]
    return ReconcileResult(
        extracted=list(statement),
        payments=payments,
        reversals=reversals,
        unpaired_reversals=unpaired,
        matched=matched,
        unmatched_pdf=unmatched,
        csv_rows=list(csv_rows),
        unused_csv=unused,
    )


def is_payment(description: str) -> bool:
    compact = re.sub(r"[\s\-]+", "", description).upper()
    return "PAYMENT" in compact and "THANKYOU" in compact


def _drop_reversals(
    rows: list[StatementRow],
) -> tuple[list[Reversal], list[StatementRow], list[StatementRow]]:
    positives = [(index, row) for index, row in enumerate(rows) if row.amount > 0]
    taken: set[int] = set()
    reversals: list[Reversal] = []
    unpaired: list[StatementRow] = []
    for index, negative in enumerate(rows):
        if negative.amount >= 0:
            continue
        target = -negative.amount
        candidates = [
            (pos_index, positive)
            for pos_index, positive in positives
            if pos_index not in taken and positive.amount == target
        ]
        if not candidates:
            unpaired.append(negative)
            taken.add(index)
            continue
        pos_index, positive = min(candidates, key=lambda item: _reversal_rank(negative, item))
        taken.add(pos_index)
        taken.add(index)
        reversals.append(Reversal(positive=positive, negative=negative))
    kept = [row for index, row in enumerate(rows) if index not in taken]
    return reversals, unpaired, kept


def _reversal_rank(negative: StatementRow, item: tuple[int, StatementRow]) -> tuple:
    pos_index, positive = item
    same_description = _norm(positive.description) == _norm(negative.description)
    same_date = positive.trans_date == negative.trans_date
    distance = abs((positive.trans_date - negative.trans_date).days)
    return (not same_description, not same_date, distance, pos_index)


def _match(
    statement: list[StatementRow], csv_rows: list[CsvRow]
) -> tuple[list[Pair], list[StatementRow], set[int]]:
    groups: dict[Decimal, list[StatementRow]] = {}
    order: dict[int, int] = {}
    for index, row in enumerate(statement):
        groups.setdefault(row.amount, []).append(row)
        order[id(row)] = index
    csv_groups: dict[Decimal, list[CsvRow]] = {}
    for row in csv_rows:
        csv_groups.setdefault(row.amount, []).append(row)

    matched: list[Pair] = []
    unmatched: list[StatementRow] = []
    used: set[int] = set()
    for amount, pdf_rows in groups.items():
        pool = [row for row in csv_groups.get(amount, []) if row.index not in used]
        if len(pdf_rows) == 1:
            pdf_row = pdf_rows[0]
            chosen = _first(pool, pdf_row.trans_date) or (pool[0] if pool else None)
            if chosen is None:
                unmatched.append(pdf_row)
                continue
            flag = "date" if chosen.date == pdf_row.trans_date else "date differs"
            matched.append(Pair(pdf_row, chosen, flag))
            used.add(chosen.index)
            continue
        remaining = list(pool)
        leftover: list[StatementRow] = []
        for pdf_row in pdf_rows:
            chosen = _first(remaining, pdf_row.trans_date)
            if chosen is None:
                leftover.append(pdf_row)
                continue
            remaining.remove(chosen)
            matched.append(Pair(pdf_row, chosen, "date"))
            used.add(chosen.index)
        for pdf_row in leftover:
            if not remaining:
                unmatched.append(pdf_row)
                continue
            chosen = remaining.pop(0)
            flag = "date" if chosen.date == pdf_row.trans_date else "amount only"
            matched.append(Pair(pdf_row, chosen, flag))
            used.add(chosen.index)
    matched.sort(key=lambda pair: order[id(pair.statement)])
    unmatched.sort(key=lambda row: order[id(row)])
    return matched, unmatched, used


def _first(rows: list[CsvRow], booked: date) -> CsvRow | None:
    for row in rows:
        if row.date == booked:
            return row
    return None


def _norm(description: str) -> str:
    return re.sub(r"\s+", " ", description).casefold().strip()


def _money(value: str) -> Decimal:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        raise ValueError("blank amount")
    return Decimal(cleaned).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
