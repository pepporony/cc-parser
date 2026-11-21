# Card reconcile

Match a Kasikorn Bank (KBank) credit-card e-statement PDF to a CSV export from Expense Manager.

The PDF has to be the file from the bank (Quadient Inspire). A browser "print to PDF" is not parsed. If the file is still locked, the password is used only to decrypt that upload and is not stored.

## Statement lines

Reading stops at the first line that contains `TOTAL BALANCE`. A transaction line looks like:

```
01/01/26 02/01/26 EXAMPLE MERCHANT BANGKOK 10.00
```

That is two dates (`DD/MM/YY`), a description, and an amount. The city or site stays inside the description. Previous balance is skipped. A two-digit year `00`–`69` is `2000`–`2069`.

## Matching

1. A description that contains both `PAYMENT` and `THANK YOU` (spaces and dashes ignored) is a card payment. It is ignored.
2. Any other negative amount is a reversal when some positive line has the same absolute amount. The counterpart is the same description, then the same transaction date, then the nearest date, then the earliest line. Both lines are dropped. A negative line with no positive twin is reported on its own.
3. What remains is matched to CSV rows on the absolute amount at two decimals. Each CSV row is used once. A leading `-` on a CSV amount is a debit sign.
   - One statement line with that amount prefers the same transaction date. A different date still matches and is flagged `date differs`.
   - Several statement lines with the same amount take same-date CSV rows first. Leftovers pair in statement order and are flagged `amount only`.

## Files

| File | Role |
| --- | --- |
| `app.py` | Streamlit UI |
| `kbank_pdf.py` | Decrypt and read statement lines |
| `reconcile.py` | CSV load, payments, reversals, matching |

## Run

Python 3.12, with `streamlit` and `pypdf`:

```bash
streamlit run app.py
```

Upload the PDF and the CSV, then press Process.
