"""KBank statement vs Expense Manager CSV."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from kbank_pdf import PdfError, StatementRow, parse_pdf
from reconcile import CsvError, CsvRow, Pair, ReconcileResult, load_csv, reconcile

UNMATCHED_COLUMNS = ("Trans date", "Posting date", "Description", "Amount")
_GREY = "color: #8b919a"


def _amount(value) -> str:
    return f"{value:,.2f}"


def _statement_records(rows: list[StatementRow]) -> list[dict]:
    return [
        {
            "Trans date": row.trans_date.isoformat(),
            "Posting date": row.posting_date.isoformat(),
            "Description": row.description,
            "Amount": _amount(row.amount),
        }
        for row in rows
    ]


def _pair_records(pairs: list[Pair]) -> list[dict]:
    return [
        {
            "PDF date": pair.statement.trans_date.isoformat(),
            "PDF description": pair.statement.description,
            "PDF amount": _amount(pair.statement.amount),
            "CSV date": pair.csv.date.isoformat(),
            "CSV amount": _amount(pair.csv.amount),
            "CSV category": pair.csv.category,
            "CSV description": pair.csv.description,
            "Match": pair.flag,
        }
        for pair in pairs
    ]


def _csv_records(rows: list[CsvRow]) -> list[dict]:
    return [
        {
            "Date": row.date.isoformat(),
            "Amount": _amount(row.amount),
            "Category": row.category,
            "Description": row.description,
        }
        for row in rows
    ]


def _unmatched_table(rows: list[StatementRow], ignored: set[int]) -> Styler:
    """Same grid as the other tables, plus an Ignore checkbox. Ticked rows are grey."""
    frame = pd.DataFrame(_statement_records(rows))
    frame["Ignore"] = [index in ignored for index in range(len(rows))]

    def paint(row: pd.Series) -> list[str]:
        if index_ignored(row.name, ignored):
            return [_GREY] * len(row)
        return [""] * len(row)

    return frame.style.apply(paint, axis=1)


def index_ignored(index: object, ignored: set[int]) -> bool:
    return isinstance(index, int) and index in ignored


def _show_unmatched(rows: list[StatementRow]) -> None:
    ignored: set[int] = st.session_state.setdefault("ignored_unmatched", set())
    open_count = sum(1 for index in range(len(rows)) if index not in ignored)
    st.caption(f"{open_count} still open, {len(rows) - open_count} greyed out. Tick Ignore to grey a line.")
    edited = st.data_editor(
        _unmatched_table(rows, ignored),
        column_config={
            "Ignore": st.column_config.CheckboxColumn("Ignore", help="Grey this line out and keep it in the table."),
        },
        disabled=list(UNMATCHED_COLUMNS),
        hide_index=True,
        width="stretch",
        key="unmatched_editor",
    )
    ticked = {index for index, flag in enumerate(edited["Ignore"].tolist()) if bool(flag)}
    if ticked != set(ignored):
        st.session_state["ignored_unmatched"] = ticked
        st.rerun()


st.set_page_config(page_title="Card reconcile", layout="wide")
st.title("Card reconcile")
st.caption("Match a KBank credit-card statement to an Expense Manager CSV. Nothing is stored.")

with st.expander("How matching works"):
    st.markdown(
        """
* Upload the KBank e-statement PDF (the file from the bank). A browser "print to PDF" will not work.
* If the PDF asks for a password, type it here. It is used only to unlock that file and is not saved.
* Reading stops at **TOTAL BALANCE**. Earlier lines shaped like `01/01/26  02/01/26  EXAMPLE MERCHANT BANGKOK  10.00` are transactions. Previous balance is skipped.
* **PAYMENT - THANK YOU** lines are ignored.
* A negative statement amount is a reversal when the statement also has the same positive amount. Both lines are dropped. The match prefers the same description, then the same date.
* CSV amounts are matched on their absolute value. A leading minus is a debit. Every CSV row is a candidate, whatever the payment method.
* The match is the amount at two decimals. When several statement lines share an amount, the transaction date picks the CSV row. A unique amount still matches when the dates differ, and that pair is marked.
* One CSV row is used once. Extra CSV rows are normal.
        """
    )

left, right = st.columns(2)
pdf_file = left.file_uploader("KBank statement (PDF)", type=["pdf"])
csv_file = right.file_uploader("Expense Manager export (CSV)", type=["csv"])
password = st.text_input(
    "PDF password",
    type="password",
    help="Leave this blank when the file is already unlocked.",
)
show_preview = st.checkbox("Preview extracted statement lines")

if st.button("Process", type="primary"):
    if pdf_file is None or csv_file is None:
        st.session_state.pop("result", None)
        st.error("Upload both a PDF statement and a CSV export.")
    else:
        try:
            # The password stays in this widget. It is not copied into the result.
            statement = parse_pdf(pdf_file.getvalue(), password)
            csv_rows = load_csv(csv_file.getvalue())
            st.session_state["result"] = reconcile(statement, csv_rows)
            st.session_state["ignored_unmatched"] = set()
            st.session_state.pop("unmatched_editor", None)
        except (PdfError, CsvError) as exc:
            st.session_state.pop("result", None)
            st.session_state["ignored_unmatched"] = set()
            st.session_state.pop("unmatched_editor", None)
            st.error(str(exc))

def _show_result(result: ReconcileResult) -> None:
    counts = st.columns(7)
    counts[0].metric("PDF rows kept", result.kept)
    counts[1].metric("Payments ignored", len(result.payments))
    counts[2].metric("Reversals removed", result.reversals_removed)
    counts[3].metric("Matched", len(result.matched))
    counts[4].metric("Unmatched PDF", len(result.unmatched_pdf))
    counts[5].metric("CSV rows", len(result.csv_rows))
    counts[6].metric("CSV unused", len(result.unused_csv))

    if show_preview:
        st.subheader("Extracted statement lines")
        st.dataframe(_statement_records(result.extracted), width="stretch", hide_index=True)

    st.subheader("Unmatched statement entries")
    if result.unmatched_pdf:
        _show_unmatched(result.unmatched_pdf)
    else:
        st.success("Every statement line has a CSV row.")

    st.subheader("Matched pairs")
    if result.matched:
        st.dataframe(_pair_records(result.matched), width="stretch", hide_index=True)
    else:
        st.info("No pairs matched.")

    with st.expander("Ignored payments"):
        if result.payments:
            st.dataframe(_statement_records(result.payments), width="stretch", hide_index=True)
        else:
            st.write("None.")

    with st.expander("Reversals removed"):
        if result.reversals:
            st.dataframe(
                [
                    {
                        "Trans date": item.positive.trans_date.isoformat(),
                        "Description": item.positive.description,
                        "Amount": _amount(item.positive.amount),
                        "Reversal date": item.negative.trans_date.isoformat(),
                        "Reversal description": item.negative.description,
                        "Reversal amount": _amount(item.negative.amount),
                    }
                    for item in result.reversals
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.write("None.")

    with st.expander("Unpaired reversals"):
        if result.unpaired_reversals:
            st.dataframe(
                _statement_records(result.unpaired_reversals),
                width="stretch",
                hide_index=True,
            )
        else:
            st.write("None.")

    with st.expander("Unused CSV rows"):
        if result.unused_csv:
            st.dataframe(_csv_records(result.unused_csv), width="stretch", hide_index=True)
        else:
            st.write("None.")


result: ReconcileResult | None = st.session_state.get("result")
if result is None:
    st.stop()
else:
    _show_result(result)
