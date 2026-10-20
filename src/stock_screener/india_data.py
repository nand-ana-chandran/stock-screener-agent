"""Helpers for reading and formatting the India fundamentals snapshot."""

import json
from datetime import datetime
from pathlib import Path


DEFAULT_SNAPSHOT_PATH = Path("data/india_snapshot.json")
RUPEES_PER_CRORE = 10_000_000
RUPEES_PER_LAKH_CRORE = 10_000_000_000_000


def load_snapshot(path=DEFAULT_SNAPSHOT_PATH):
    """Load an India snapshot JSON file from disk."""
    with Path(path).open(encoding="utf-8") as snapshot_file:
        return json.load(snapshot_file)


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


def format_money(value, currency):
    """Format INR values in crore and label non-INR values with their currency."""
    if value is None:
        return None
    amount = float(value)
    code = (currency or "").upper()
    if code == "INR":
        if abs(amount) > RUPEES_PER_LAKH_CRORE:
            return f"₹{_indian_grouping(amount / RUPEES_PER_LAKH_CRORE)} lakh crore"
        return f"₹{_indian_grouping(amount / RUPEES_PER_CRORE)} crore"
    formatted_amount = f"{amount:,.2f}"
    if not code:
        return f"{formatted_amount} (currency unknown)"
    return f"{code} {formatted_amount}"
