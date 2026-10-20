"""Sample yfinance coverage probe for 50 well-known Nifty 50 members.

This hardcoded ticker list is a sample, not the official Nifty 50 index list.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import yfinance as yf


SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "LT",
    "SBIN", "BHARTIARTL", "AXISBANK", "KOTAKBANK", "HINDUNILVR",
    "BAJFINANCE", "MARUTI", "SUNPHARMA", "TATAMOTORS", "TATASTEEL",
    "NTPC", "POWERGRID", "ONGC", "WIPRO", "HCLTECH", "ASIANPAINT",
    "ULTRACEMCO", "TITAN", "ADANIENT", "ADANIPORTS", "JSWSTEEL",
    "COALINDIA", "NESTLEIND", "TECHM", "INDUSINDBK", "BAJAJFINSV",
    "GRASIM", "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "HEROMOTOCO",
    "TATACONSUM", "BPCL", "IOC", "HINDALCO", "UPL", "SBILIFE",
    "HDFCLIFE", "APOLLOHOSP", "BRITANNIA", "LTIM", "M&M",
]

FIELD_KEYS = {
    "price": ("regularMarketPrice", "currentPrice"),
    "market_cap": ("marketCap",),
    "trailing_pe": ("trailingPE",),
    "sector": ("sector",),
    "revenue_growth": ("revenueGrowth",),
    "profit_margin": ("profitMargins",),
    "total_cash": ("totalCash",),
    "total_debt": ("totalDebt",),
}
SUMMARY_FIELDS = [
    "price",
    "market_cap",
    "trailing_pe",
    "sector",
    "52_week_range",
    "revenue_growth",
    "profit_margin",
    "total_cash",
    "total_debt",
]

HTTP_ERROR_PATTERN = re.compile(r"HTTP(?:\s+Error)?\s*[:=]?\s*(401|429)\b", re.I)


class HTTPStatusCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.statuses = []

    def emit(self, record):
        match = HTTP_ERROR_PATTERN.search(record.getMessage())
        if match:
            status = int(match.group(1))
            if status not in self.statuses:
                self.statuses.append(status)


def _first_value(info, keys):
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    return None


def _exception_http_status(error):
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None) or getattr(current, "status_code", None)
        if status in (401, 429):
            return status
        match = HTTP_ERROR_PATTERN.search(str(current))
        if match:
            return int(match.group(1))
        current = current.__cause__ or current.__context__
    return None


def _fetch_ticker(symbol, logger):
    ticker = f"{symbol}.NS"
    captured = HTTPStatusCapture()
    logger.addHandler(captured)
    info = {}
    error_type = None
    try:
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as error:
            error_type = type(error).__name__
            status = _exception_http_status(error)
            if status and status not in captured.statuses:
                captured.statuses.append(status)
    finally:
        logger.removeHandler(captured)

    price = _first_value(info, FIELD_KEYS["price"])
    low = info.get("fiftyTwoWeekLow")
    high = info.get("fiftyTwoWeekHigh")
    fields = {
        "price": price,
        "market_cap": _first_value(info, FIELD_KEYS["market_cap"]),
        "trailing_pe": _first_value(info, FIELD_KEYS["trailing_pe"]),
        "sector": _first_value(info, FIELD_KEYS["sector"]),
        "52_week_range": {"low": low, "high": high} if low is not None or high is not None else None,
        "revenue_growth": _first_value(info, FIELD_KEYS["revenue_growth"]),
        "profit_margin": _first_value(info, FIELD_KEYS["profit_margin"]),
        "total_cash": _first_value(info, FIELD_KEYS["total_cash"]),
        "total_debt": _first_value(info, FIELD_KEYS["total_debt"]),
    }
    return {
        "symbol": ticker,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fields": fields,
        "quote_currency": info.get("currency"),
        "financial_currency": info.get("financialCurrency"),
        "http_errors": [f"HTTP {status}" for status in captured.statuses],
        "error_type": error_type,
    }


def main():
    started = time.monotonic()
    logger = logging.getLogger("yfinance")
    results = []

    for index, symbol in enumerate(SYMBOLS):
        results.append(_fetch_ticker(symbol, logger))
        if index + 1 < len(SYMBOLS):
            time.sleep(0.5)

    for result in results:
        print(
            f"{result['symbol']} | fetched_at={result['fetched_at']} | "
            f"quote_currency={result['quote_currency']} | "
            f"financial_currency={result['financial_currency']} | "
            f"http_errors={','.join(result['http_errors']) or 'none'} | "
            f"error_type={result['error_type'] or 'none'}"
        )
        for field, value in result["fields"].items():
            print(f"  {field}: {json.dumps(value, default=str)}")

    print("\nField availability")
    print(f"{'Field':<20} {'Present':>8} {'Null':>8}")
    print("-" * 38)
    for field in SUMMARY_FIELDS:
        present = sum(
            (
                result["fields"][field] is not None
                and (
                    field != "52_week_range"
                    or all(value is not None for value in result["fields"][field].values())
                )
            )
            for result in results
        )
        print(f"{field:<20} {present:>8} {len(results) - present:>8}")

    rate_limit_errors = sum(
        status in ("HTTP 401", "HTTP 429")
        for result in results
        for status in result["http_errors"]
    )
    print(f"\nTickers queried: {len(results)}")
    print(f"Total runtime: {time.monotonic() - started:.2f} seconds")
    print(f"Rate-limit errors (HTTP 401/429): {rate_limit_errors}")


if __name__ == "__main__":
    main()
