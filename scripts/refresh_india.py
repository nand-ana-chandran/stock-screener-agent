"""Fetch and incrementally refresh fundamentals for a Nifty 500 CSV universe."""

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / "india_snapshot.json"
RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1
REQUEST_DELAY_SECONDS = 0.5
FIELDS = {
    "price": ("regularMarketPrice", "currentPrice"),
    "market_cap": ("marketCap",),
    "trailing_pe": ("trailingPE",),
    "sector": ("sector",),
    "industry": ("industry",),
    "revenue_growth": ("revenueGrowth",),
    "profit_margin": ("profitMargins",),
    "total_cash": ("totalCash",),
    "total_debt": ("totalDebt",),
    "quote_currency": ("currency",),
    "financial_currency": ("financialCurrency",),
}
REQUIRED_FIELDS = tuple(FIELDS) + ("fifty_two_week_low", "fifty_two_week_high")


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        help="CSV path; defaults to the newest data/nifty500_<date>.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Snapshot output path (default: {OUTPUT_PATH})",
    )
    return parser.parse_args()


def _find_input_csv(csv_path):
    if csv_path is not None:
        if not csv_path.is_file():
            raise FileNotFoundError(f"Input CSV not found: {csv_path}")
        return csv_path
    candidates = sorted(DATA_DIR.glob("nifty500_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No data/nifty500_<date>.csv file found in {DATA_DIR.resolve()}"
        )
    return candidates[-1]


def _snapshot_date(csv_path):
    match = re.fullmatch(r"nifty500_(\d{4}-\d{2}-\d{2})\.csv", csv_path.name)
    return match.group(1) if match else None


def _load_symbols(csv_path):
    with csv_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {csv_path}")
        symbol_column = next(
            (
                name
                for name in reader.fieldnames
                if name.strip().lower() in ("symbol", "ticker")
            ),
            None,
        )
        if symbol_column is None:
            raise ValueError("CSV must have a 'Symbol' or 'Ticker' column")
        symbols = []
        seen = set()
        for row in reader:
            symbol = (row.get(symbol_column) or "").strip().upper()
            if not symbol:
                continue
            symbol = re.sub(r"\.NS$", "", symbol, flags=re.IGNORECASE)
            normalized = f"{symbol}.NS"
            if normalized not in seen:
                symbols.append(normalized)
                seen.add(normalized)
    return symbols


def _fetch_info(symbol):
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            return yf.Ticker(symbol).info or {}, None
        except Exception as error:
            last_error = error
            if attempt < RETRIES:
                time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** attempt))
    return {}, type(last_error).__name__


def _extract_fields(info):
    result = {}
    for field, source_keys in FIELDS.items():
        result[field] = next(
            (info.get(key) for key in source_keys if info.get(key) is not None),
            None,
        )
    result["fifty_two_week_low"] = info.get("fiftyTwoWeekLow")
    result["fifty_two_week_high"] = info.get("fiftyTwoWeekHigh")
    return result


def _make_record(symbol, info, error_type):
    fields = _extract_fields(info)
    if error_type:
        status = "failed"
    elif not any(value is not None for value in fields.values()):
        status = "failed"
        error_type = "NoDataError"
    elif any(fields[field] is None for field in REQUIRED_FIELDS):
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error_type": error_type,
        **fields,
    }


def _write_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as destination:
        json.dump(snapshot, destination, indent=2, ensure_ascii=False)
        destination.write("\n")
    temporary_path.replace(path)


def refresh(csv_path, output_path=OUTPUT_PATH):
    csv_path = _find_input_csv(csv_path)
    symbols = _load_symbols(csv_path)
    snapshot_date = _snapshot_date(csv_path)
    output_path = Path(output_path)

    if output_path.is_file():
        with output_path.open(encoding="utf-8") as source:
            previous = json.load(source)
    else:
        previous = {}
    if previous.get("source_file") != csv_path.name:
        tickers = {}
    else:
        previous_tickers = previous.get("tickers", {})
        tickers = {
            symbol: previous_tickers[symbol]
            for symbol in symbols
            if symbol in previous_tickers
        }

    snapshot = {
        "snapshot_date": snapshot_date,
        "source_file": csv_path.name,
        "fetched_at": previous.get("fetched_at"),
        "tickers": tickers,
    }
    pending = [symbol for symbol in symbols if tickers.get(symbol, {}).get("status") not in ("ok", "partial")]
    total_requests = len(pending)
    for index, symbol in enumerate(pending):
        info, error_type = _fetch_info(symbol)
        tickers[symbol] = _make_record(symbol, info, error_type)
        snapshot["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_snapshot(output_path, snapshot)
        if index + 1 < total_requests:
            time.sleep(REQUEST_DELAY_SECONDS)

    snapshot["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_snapshot(output_path, snapshot)
    counts = {
        status: sum(record.get("status") == status for record in tickers.values())
        for status in ("ok", "partial", "failed")
    }
    failed_symbols = [
        symbol for symbol in symbols if tickers.get(symbol, {}).get("status") == "failed"
    ]
    print(
        f"ok={counts['ok']} partial={counts['partial']} failed={counts['failed']}"
    )
    print(f"Failed symbols: {', '.join(failed_symbols) if failed_symbols else 'none'}")
    return snapshot


def main():
    args = _parse_args()
    refresh(args.csv_path, args.output)


if __name__ == "__main__":
    main()
