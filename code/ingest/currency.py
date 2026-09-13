"""
Dated currency conversion.

Per problem_statement.md: "Balances, requests, payment options, and output
amounts use the user's home_currency." So requests.csv, financial_profiles.csv
and request_payment_options.csv are ALREADY in home currency — no conversion
needed there. The place conversion actually matters is financial_events.csv,
where an individual event may be recorded in a foreign currency (e.g. a
salary paid in USD for a user whose home_currency is INR). Those get
converted once, at ledger-build time, using the dated rate for that event's
date and currency pair.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from ingest.schema import CANDIDATES, resolve_column


class RateTable:
    """Dated from->to exchange rates, with same-day lookup and a nearest
    earlier-date fallback (documented explicitly since it's a design
    decision, not given verbatim in the spec)."""

    def __init__(self, exchange_rates_df: pd.DataFrame):
        self._rows = []
        if exchange_rates_df is None or exchange_rates_df.empty:
            return
        c = CANDIDATES["exchange_rates"]
        date_col = resolve_column(exchange_rates_df, c["rate_date"])
        from_col = resolve_column(exchange_rates_df, c["from_currency"])
        to_col = resolve_column(exchange_rates_df, c["to_currency"])
        rate_col = resolve_column(exchange_rates_df, c["rate"])
        for _, row in exchange_rates_df.iterrows():
            self._rows.append(
                (row[date_col], str(row[from_col]).upper(), str(row[to_col]).upper(), float(row[rate_col]))
            )

    def rate(self, on_date: dt.date, from_currency: str, to_currency: str) -> Optional[float]:
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        if from_currency == to_currency:
            return 1.0

        exact = [r for r in self._rows if r[0] == on_date and r[1] == from_currency and r[2] == to_currency]
        if exact:
            return exact[0][3]
        # inverse pair on the same date
        exact_inv = [r for r in self._rows if r[0] == on_date and r[1] == to_currency and r[2] == from_currency]
        if exact_inv:
            return 1.0 / exact_inv[0][3]

        # Fallback: nearest earlier date for the same pair (dated rates are
        # "fixed", so using the closest prior published rate is the safest
        # conservative choice rather than inventing a rate).
        candidates = [r for r in self._rows if r[1] == from_currency and r[2] == to_currency and r[0] <= on_date]
        if candidates:
            candidates.sort(key=lambda r: r[0])
            return candidates[-1][3]
        candidates_inv = [r for r in self._rows if r[1] == to_currency and r[2] == from_currency and r[0] <= on_date]
        if candidates_inv:
            candidates_inv.sort(key=lambda r: r[0])
            return 1.0 / candidates_inv[-1][3]

        return None

    def convert(self, amount: float, on_date: dt.date, from_currency: str, to_currency: str) -> float:
        r = self.rate(on_date, from_currency, to_currency)
        if r is None:
            raise ValueError(
                f"No exchange rate available to convert {from_currency}->{to_currency} on or before {on_date}."
            )
        return amount * r
