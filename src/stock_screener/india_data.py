"""Helpers for reading and formatting the India fundamentals snapshot."""

import json
from datetime import datetime
from pathlib import Path


DEFAULT_SNAPSHOT_PATH = Path("data/india_snapshot.json")
RUPEES_PER_CRORE = 10_000_000
RUPEES_PER_LAKH_CRORE = 1_000_000_000_000


def load_snapshot(path=DEFAULT_SNAPSHOT_PATH):
    """Load a snapshot with skipped tickers marked unavailable."""
    with Path(path).open(encoding="utf-8") as snapshot_file:
        snapshot = json.load(snapshot_file)
    tickers = snapshot.get("tickers", {})
    skipped_symbols = [
        symbol
        for symbol, record in tickers.items()
        if record.get("status") == "skipped"
    ]
    if skipped_symbols:
        snapshot["tickers"] = {
            symbol: record
            for symbol, record in tickers.items()
            if record.get("status") != "skipped"
        }
        snapshot["skipped_ticker_count"] = (
            snapshot.get("skipped_ticker_count", 0) + len(skipped_symbols)
        )
    return snapshot


def get_snapshot_date(snapshot):
    """Return the source snapshot date, falling back to the fetch date."""
    snapshot_date = snapshot.get("snapshot_date")
    if snapshot_date:
        return snapshot_date
    fetched_at = snapshot.get("fetched_at")
    if not fetched_at:
        return None
    return datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).date().isoformat()


def _indian_grouping(value):
    integer, separator, fraction = f"{abs(value):.2f}".partition(".")
    if len(integer) <= 3:
        grouped = integer
    else:
        trailing = integer[-3:]
        remaining = integer[:-3]
        groups = []
        while remaining:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        grouped = ",".join(groups + [trailing])
    sign = "-" if value < 0 else ""
    return sign + grouped + (separator + fraction if separator else "")


def _compact_indian_amount(value):
    integer, separator, fraction = _indian_grouping(value).partition(".")
    fraction = fraction.rstrip("0")
    return integer + (separator + fraction if fraction else "")


def format_money(value, currency, scale_inr=True):
    """Format INR values in crore, or as rupees when scale_inr is false."""
    if value is None:
        return None
    amount = float(value)
    code = (currency or "").upper()
    if code == "INR":
        if not scale_inr:
            return f"₹{_indian_grouping(amount)}"
        if abs(amount) >= RUPEES_PER_LAKH_CRORE:
            return f"₹{_compact_indian_amount(amount / RUPEES_PER_LAKH_CRORE)} lakh crore"
        return f"₹{_indian_grouping(amount / RUPEES_PER_CRORE)} crore"
    formatted_amount = f"{amount:,.2f}"
    if not code:
        return f"{formatted_amount} (currency unknown)"
    return f"{code} {formatted_amount}"
