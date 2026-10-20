import os
import re
import json
from datetime import datetime, timezone

from langchain.tools import tool
import yfinance as yf
from yfinance import EquityQuery

from .india_data import (
    RUPEES_PER_CRORE,
    format_money,
    get_snapshot_date,
    load_snapshot,
)


def fmt_money(n, currency="USD"):
    """1540230144 -> '$1.54B'; for other currencies -> 'BRL 287.43B'."""
    if n is None:
        return None
    prefix = "$" if currency in (None, "USD") else f"{currency} "
    for limit, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M")]:
        if abs(n) >= limit:
            return f"{prefix}{n / limit:.2f}{suffix}"
    return f"{prefix}{n:,.0f}"


def fmt_pct(x):
    """0.249 -> '24.90%'. Done in code so raw decimals never reach the LLM."""
    return None if x is None else f"{x * 100:.2f}%"

def fmt_num(x, digits=2):
    """37.888252 -> 37.89. Keeps long raw decimals away from the LLM."""
    return None if x is None else round(x, digits)

# ---------- Pagination helper ----------
SCREENER_PAGE_SIZE = 250


def fetch_all_quotes(query, sort_field, sort_ascending):
    """Fetch every page from yf.screen and return deduplicated quotes.

    Returns:
        quotes (list): all deduplicated quote dicts.
        reported_total (int | None): total candidates Yahoo reported, or None.
        candidate_set_complete (bool): True when the full candidate set was retrieved.
        last_result (dict | None): the raw dict from the final yf.screen call.
    """
    quotes = []
    seen_symbols = set()
    api_offset = 0
    reported_total = None
    candidate_set_complete = False
    last_result = None

    while True:
        last_result = yf.screen(
            query,
            sortField=sort_field,
            sortAsc=sort_ascending,
            size=SCREENER_PAGE_SIZE,
            offset=api_offset,
        )
        if reported_total is None:
            reported_total = last_result.get("total")

        page_quotes = last_result.get("quotes", [])
        if not page_quotes:
            candidate_set_complete = (
                reported_total is None or len(quotes) >= reported_total
            )
            break

        previous_count = len(quotes)
        for quote in page_quotes:
            symbol = quote.get("symbol")
            if symbol is None or symbol not in seen_symbols:
                quotes.append(quote)
                if symbol is not None:
                    seen_symbols.add(symbol)

        if reported_total is not None and len(quotes) >= reported_total:
            candidate_set_complete = True
            break
        if len(quotes) == previous_count:
            break
        if reported_total is None and len(page_quotes) < SCREENER_PAGE_SIZE:
            candidate_set_complete = True
            break
        api_offset += len(page_quotes)

    return quotes, reported_total, candidate_set_complete, last_result


# ---------- Tool 1: predefined screener ----------
@tool
def simple_screener(screen_type: str, offset: int = 0, limit: int = 25) -> str:
    """
    Returns a predefined screened list. The 'most_actives' screen is ranked locally by
    daily trading volume, highest first; other predefined screens are not ranked.
    Prefer custom_screener when the user wants a market cap range or sector.
    This tool is for US predefined screens only. For Indian companies, NSE symbols,
    or rupee questions, use india_screener; never pass NSE symbols here.
    IMPORTANT: some screens return individual STOCKS and some return mutual FUNDS or bond
    funds. Choose by what the user is asking about. If the user asks for stocks or
    companies, never use a FUND screen. Use a FUND screen only when the user asks for
    funds, mutual funds, or bond funds.

    Args:
        screen_type, STOCK screens (return individual companies):
            'aggressive_small_caps', 'day_gainers', 'day_losers',
            'growth_technology_stocks', 'most_actives', 'most_shorted_stocks',
            'small_cap_gainers', 'undervalued_growth_stocks', 'undervalued_large_caps'
        screen_type, FUND screens (return mutual funds or index funds, NOT stocks):
            'conservative_foreign_funds', 'high_yield_bond', 'portfolio_anchors',
            'solid_large_growth_funds', 'solid_midcap_growth_funds', 'top_mutual_funds'
        offset: pagination start point, optional. For 'most_actives', this is the rank
            offset after sorting all retrieved candidates.
        limit: maximum number of results to return; defaults to 25.

    Returns:
        A JSON list. Most-active rows include their rank and actual daily volume;
        unavailable volumes are null and are not ranked. The first line identifies stocks,
        funds or ETFs.
    """

    if screen_type not in yf.PREDEFINED_SCREENER_QUERIES:
        valid = ", ".join(yf.PREDEFINED_SCREENER_QUERIES.keys())
        return f"Unknown screen_type '{screen_type}'. Valid options: {valid}"
    if limit < 1 or limit > 250:
        return "limit must be between 1 and 250."
    if offset < 0:
        return "offset must be zero or greater."

    preset = yf.PREDEFINED_SCREENER_QUERIES[screen_type]
    query = preset["query"]
    sort_field = preset.get("sortField")
    sort_ascending = str(preset.get("sortType", "DESC")).upper() == "ASC"
    try:
        if screen_type == "most_actives":
            quotes, reported_total, candidate_set_complete, result = fetch_all_quotes(
                query, sort_field, sort_ascending
            )

            def volume_key(quote):
                volume = quote.get("regularMarketVolume")
                has_volume = (
                    isinstance(volume, (int, float)) and not isinstance(volume, bool)
                )
                return has_volume, volume if has_volume else 0

            quotes.sort(key=volume_key, reverse=True)
            selected_quotes = quotes[offset:offset + limit]
        else:
            result = yf.screen(
                query,
                sortField=sort_field,
                sortAsc=sort_ascending,
                size=limit,
                offset=offset,
            )
            quotes = result.get("quotes", [])
            selected_quotes = quotes
    except Exception as e:
        return f"Could not fetch '{screen_type}' from Yahoo Finance right now: {e}"

    if os.getenv("DEBUG_DUMP"):
        with open("output.json", "w") as f:
            json.dump(result, f, indent=2)

    fields = [
        "bid", "ask", "exchange", "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow", "averageAnalystRating", "dividendYield", "symbol",
    ]
    rows = []
    for index, q in enumerate(selected_quotes, start=offset + 1):
        row = {"name": q.get("longName") or q.get("shortName")}
        row.update({k: q.get(k) for k in fields})
        if screen_type == "most_actives":
            volume = q.get("regularMarketVolume")
            row["rank"] = (
                index if isinstance(volume, (int, float)) and not isinstance(volume, bool)
                else None
            )
            row["regularMarketVolume"] = volume
        rows.append(row)

    kind = {"EquityQuery": "Stock", "FundQuery": "Fund", "ETFQuery": "ETF"}.get(
        type(query).__name__, "Asset")
    output = f"{kind} screener results ({screen_type}):\n" + json.dumps(rows, indent=2)
    if screen_type == "most_actives":
        if len(selected_quotes) < limit:
            output += (
                f"\n\nOnly {len(selected_quotes)} stocks were available among "
                f"{len(quotes)} unique Yahoo Finance screener candidates retrieved."
            )
            if reported_total is not None:
                output += f" Yahoo reported {reported_total} candidates."
            if not candidate_set_complete:
                output += " The candidate set may be incomplete."
            else:
                output += " Yahoo Finance screener coverage may not include every market listing."
        missing_volume = sum(
            not isinstance(q.get("regularMarketVolume"), (int, float))
            or isinstance(q.get("regularMarketVolume"), bool)
            for q in selected_quotes
        )
        if missing_volume:
            output += (
                f"\n{missing_volume} result(s) have no reported daily volume and "
                "are shown without a rank."
            )
    return output


# ---------- Tool 2: custom screener with filters + sorting ----------
SORT_FIELDS = {
    "market_cap": "intradaymarketcap",
    "percent_change": "percentchange",
    "price": "intradayprice",
    "volume": "dayvolume",
    "pe_ratio": "peratio.lasttwelvemonths",
}


@tool
def custom_screener(
    min_market_cap_millions: float = 0,
    max_market_cap_millions: float = 0,
    sector: str = "",
    sort_by: str = "market_cap",
    ascending: bool = False,
    min_volume: int | None = None,
    offset: int = 0,
    min_percent_change: float | None = None,
    limit: int = 5,
) -> str:
    """
    Screens US-listed stocks (NYSE/Nasdaq/AMEX) with custom filters and a real sort order.
    For Indian companies, NSE symbols, or rupee questions, use india_screener instead.
    Use this when the user wants a market cap range, a sector, or a ranking
    (for example 'top small-cap tech gainers today').

    Args:
        min_market_cap_millions: minimum market cap in millions of USD. 0 means no minimum.
            Rough guide: small cap is 300 to 2000, mid cap is 2000 to 10000, large cap is above 10000.
        max_market_cap_millions: maximum market cap in millions of USD. 0 means no maximum.
        sector: optional, one of 'Technology', 'Healthcare', 'Financial Services',
            'Consumer Cyclical', 'Consumer Defensive', 'Industrials', 'Energy',
            'Utilities', 'Real Estate', 'Basic Materials', 'Communication Services'.
            Empty string means all sectors.
        sort_by: one of 'market_cap', 'percent_change', 'price', 'volume', 'pe_ratio'.
        ascending: False sorts highest first (default), True sorts lowest first.
        min_volume: minimum shares traded today. When omitted for a percentage-change
            ranking, defaults to 500000; 0 means no minimum and an explicit value
            overrides the default.
        offset: pagination start point in the sorted results. 0 for the first page.
        min_percent_change: optional minimum daily percentage change in percentage points.
            Pass 0 to return gainers only (strictly greater than 0%); omit for no change filter.
        limit: maximum number of matching results to return. Defaults to five; set this
            to the requested number for requests such as 'top 10'.

    Returns:
        A JSON list of up to limit stocks, in sorted order. Market caps are pre-formatted.
    """
    if sort_by not in SORT_FIELDS:
        return f"Unknown sort_by '{sort_by}'. Valid options: {', '.join(SORT_FIELDS)}"
    if limit < 1:
        return "limit must be a positive integer."
    if offset < 0:
        return "offset must be zero or greater."

    effective_min_volume = min_volume
    if effective_min_volume is None:
        effective_min_volume = 500000 if sort_by == "percent_change" else 0

    filters = [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", "NMS", "NYQ", "NGM", "NCM", "ASE"]),
    ]
    if min_market_cap_millions > 0:
        filters.append(EquityQuery("gt", ["intradaymarketcap", min_market_cap_millions * 1_000_000]))
    if max_market_cap_millions > 0:
        filters.append(EquityQuery("lt", ["intradaymarketcap", max_market_cap_millions * 1_000_000]))
    if sector:
        filters.append(EquityQuery("eq", ["sector", sector]))
    if effective_min_volume > 0:
        filters.append(EquityQuery("gt", ["dayvolume", effective_min_volume]))
    if min_percent_change is not None:
        filters.append(EquityQuery("gt", ["percentchange", min_percent_change]))

    try:
        query = EquityQuery("and", filters)
        raw_quotes, reported_total, candidate_set_complete, result = fetch_all_quotes(
            query, SORT_FIELDS[sort_by], ascending
        )
    except Exception as e:
        return f"Screener error: {e}"

    retrieved_candidate_count = len(raw_quotes)

    if os.getenv("DEBUG_DUMP"):
        with open("output.json", "w") as f:
            json.dump(result, f, indent=2)

    fields = [
        "symbol", "shortName", "exchange", "regularMarketPrice",
        "regularMarketChangePercent", "marketCap", "regularMarketVolume",
        "trailingPE", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "averageAnalystRating",
    ]

    # Yahoo labels warrants as EQUITY, so filter their security names explicitly.
    raw_quotes = [
        quote for quote in raw_quotes
        if not re.search(
            r"\b(?:warrants?|wts)\b",
            f"{quote.get('shortName') or ''} {quote.get('longName') or ''}",
            flags=re.IGNORECASE,
        )
    ]

    # Yahoo may return quotes outside the query's market-cap bounds; validate
    # its raw numeric marketCap values before formatting them for the response.
    if min_market_cap_millions > 0 or max_market_cap_millions > 0:
        raw_quotes = [
            quote for quote in raw_quotes
            if isinstance(quote.get("marketCap"), (int, float))
            and (
                min_market_cap_millions <= 0
                or quote["marketCap"] > min_market_cap_millions * 1_000_000
            )
            and (
                max_market_cap_millions <= 0
                or quote["marketCap"] < max_market_cap_millions * 1_000_000
            )
        ]

    # Yahoo may return quotes below the volume predicate; enforce the same threshold
    # locally before sorting and selecting the requested result page.
    if effective_min_volume > 0:
        raw_quotes = [
            quote for quote in raw_quotes
            if isinstance(quote.get("regularMarketVolume"), (int, float))
            and not isinstance(quote.get("regularMarketVolume"), bool)
            and quote["regularMarketVolume"] > effective_min_volume
        ]

    # Yahoo's screener may return rows that do not satisfy its percentage filter.
    if min_percent_change is not None:
        raw_quotes = [
            q for q in raw_quotes
            if isinstance(q.get("regularMarketChangePercent"), (int, float))
            and q["regularMarketChangePercent"] > min_percent_change
        ]

    # Enforce the requested ranking locally too, rather than trusting the upstream order.
    local_sort_fields = {
        "market_cap": "marketCap",
        "percent_change": "regularMarketChangePercent",
        "price": "regularMarketPrice",
        "volume": "regularMarketVolume",
        "pe_ratio": "trailingPE",
    }
    sort_field = local_sort_fields[sort_by]

    def sort_value(quote):
        value = quote.get(sort_field)
        if isinstance(value, (int, float)):
            return value
        return float("inf") if ascending else float("-inf")

    raw_quotes = sorted(raw_quotes, key=sort_value, reverse=not ascending)
    matching_count = len(raw_quotes)
    raw_quotes = raw_quotes[offset:offset + limit]
    page_match_count = len(raw_quotes)
    rows = [{k: q.get(k) for k in fields} for q in raw_quotes]
    for row in rows:
        row["marketCap"] = fmt_money(row.get("marketCap"))
        for key in ("regularMarketPrice", "regularMarketChangePercent", "trailingPE",
                    "fiftyTwoWeekHigh", "fiftyTwoWeekLow"):
            row[key] = fmt_num(row.get(key))

    output = (
        ("No stocks matched those filters." if not rows else
         "Custom screener results (sorted):\n" + json.dumps(rows, indent=2))
    )
    if len(rows) < limit:
        if candidate_set_complete:
            note = (
                f"Only {page_match_count} matching stocks were available for this page "
                f"({matching_count} matches total) among "
                f"{retrieved_candidate_count} unique Yahoo Finance screener candidates retrieved."
            )
            if reported_total is not None:
                note += f" Yahoo reported {reported_total} candidates."
            note += " Yahoo Finance screener coverage may not include every market listing."
        else:
            note = (
                f"Only {page_match_count} matching stocks were available for this page "
                f"({matching_count} matches total) among "
                f"{retrieved_candidate_count} unique Yahoo Finance screener candidates retrieved; "
                f"the candidate set may be incomplete"
            )
            if reported_total is not None:
                note += f" (Yahoo reported {reported_total} candidates)"
            note += "."
        output += "\n\n" + note
    return output


# ---------- Tool 3: company snapshot (fundamentals + news) ----------
@tool
def company_snapshot(symbol: str) -> str:
    """
    Returns fundamentals and the latest news headlines for ONE company.
    Use this when the user asks about a specific ticker or company
    (for example 'tell me about NVDA' or 'any news on XNDU?').
    Do not use it to list or rank many stocks; use the screeners for that.
    This is for US-listed tickers only. Never pass an NSE symbol here, even if it
    also exists as a US ADR; use india_screener for Indian companies, NSE symbols,
    or rupee questions.

    Args:
        symbol: the stock ticker, for example 'NVDA' or 'AAPL'.

    Returns:
        JSON with business summary, valuation, growth, balance sheet, analyst view
        and up to 3 recent headlines. Money amounts and percentages are pre-formatted.
        A headline with a note field is a general market headline.
    """
    symbol = symbol.strip().upper()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
    except Exception as e:
        return f"Could not fetch data for {symbol}: {e}"

    if not info or not (info.get("longName") or info.get("shortName")):
        return f"No data found for '{symbol}'. Check the ticker symbol."

    summary = info.get("longBusinessSummary") or ""
    name = info.get("longName") or info.get("shortName")
    quote_cur = info.get("currency")
    fin_cur = info.get("financialCurrency")
    current_price = info.get("regularMarketPrice") or info.get("currentPrice")
    previous_close = info.get("regularMarketPreviousClose")
    daily_change_percent = (
        ((current_price / previous_close) - 1) * 100
        if isinstance(current_price, (int, float))
        and isinstance(previous_close, (int, float))
        and previous_close > 0
        else None
    )
    data = {
        "symbol": symbol,
        "name": name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "business_summary": summary[:400] + ("..." if len(summary) > 400 else ""),
        "price": fmt_num(current_price),
        "daily_change_percent": fmt_pct(daily_change_percent / 100) if daily_change_percent is not None else None,
        "quote_currency": quote_cur,
        "market_cap": fmt_money(info.get("marketCap"), quote_cur),
        "trailing_pe": fmt_num(info.get("trailingPE")),
        "forward_pe": fmt_num(info.get("forwardPE")),
        "revenue_growth": fmt_pct(info.get("revenueGrowth")),
        "profit_margin": fmt_pct(info.get("profitMargins")),
        "total_cash": fmt_money(info.get("totalCash"), fin_cur),
        "total_debt": fmt_money(info.get("totalDebt"), fin_cur),
        "balance_sheet_currency": fin_cur,
        "fifty_two_week_high": fmt_num(info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": fmt_num(info.get("fiftyTwoWeekLow")),
        "analyst_rating": info.get("averageAnalystRating"),
        "analyst_target_mean_price": fmt_num(info.get("targetMeanPrice")),
        "short_percent_of_float": fmt_pct(info.get("shortPercentOfFloat")),
    }

    # First word of the company name, e.g. 'NVIDIA' or 'Xanadu' (skips a leading 'The')
    name_word = re.sub(r"^the\s+", "", name, flags=re.I).split(" ")[0].strip(",.")

    def mentions_company(title):
        title = title or ""
        if re.search(rf"\b{re.escape(symbol)}\b", title, re.I):
            return True
        return len(name_word) > 2 and re.search(rf"\b{re.escape(name_word)}", title, re.I) is not None

    # News is fetched separately so a news failure never loses the fundamentals.
    # yf.Search is used because Ticker.news returned nothing in testing.
    headlines = []
    try:
        news = yf.Search(symbol, news_count=3).news or []
        for item in news[:3]:
            ts = item.get("providerPublishTime")
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if ts else None
            )
            title = item.get("title")
            entry = {
                "title": title,
                "publisher": item.get("publisher"),
                "published": published,
            }
            if not mentions_company(title):
                entry["note"] = "general market headline, not specifically about this company"
            headlines.append(entry)
    except Exception:
        headlines = []
    data["recent_headlines"] = headlines

    return "Company snapshot:\n" + json.dumps(data, indent=2)


INDIA_SORT_FIELDS = {
    "market_cap": "market_cap",
    "trailing_pe": "trailing_pe",
    "total_cash": "total_cash",
    "total_debt": "total_debt",
    "profit_margin": "profit_margin",
    "revenue_growth": "revenue_growth",
    "price": "price",
    "sector": "sector",
    "industry": "industry",
}


@tool
def india_screener(
    symbol: str | None = None,
    company_name: str | None = None,
    min_market_cap_crore: float | None = None,
    max_market_cap_crore: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    min_trailing_pe: float | None = None,
    max_trailing_pe: float | None = None,
    min_profit_margin: float | None = None,
    min_revenue_growth: float | None = None,
    sort_by: str = "market_cap",
    sort_order: str = "desc",
    limit: int = 10,
    offset: int = 0,
) -> str:
    """Filter and sort Indian company fundamentals from the local NSE snapshot.

    Use only for requests about India, NSE, Nifty, Indian companies, or rupees.
    This tool reads the saved snapshot and never fetches live data.
    Indian companies and NSE symbols must use this tool, not company_snapshot or
    the US screeners. Real sector values include Technology, Healthcare,
    Financial Services, Consumer Cyclical, Consumer Defensive, Industrials,
    Energy, Utilities, Real Estate, Basic Materials, and Communication Services.
    Common sector filters include IT for Technology. Industry filters include
    Banks and Drug Manufacturers (pharma).

    Args:
        symbol: Optional NSE symbol with or without .NS, matched case-insensitively.
        company_name: Optional case-insensitive substring of the company name.
        min_market_cap_crore: Optional minimum market cap in INR crore.
        max_market_cap_crore: Optional maximum market cap in INR crore.
        sector: Optional case-insensitive sector substring; IT means Technology.
        industry: Optional case-insensitive industry substring; pharma means Drug
            Manufacturers and banks means Banks.
        min_trailing_pe: Optional minimum trailing P/E.
        max_trailing_pe: Optional maximum trailing P/E.
        min_profit_margin: Optional minimum profit margin in percentage points (10 means 10%).
        min_revenue_growth: Optional minimum revenue growth in percentage points (10 means 10%).
        sort_by: One of market_cap, trailing_pe, total_cash, total_debt,
            profit_margin, revenue_growth, price, sector, industry.
        sort_order: "asc" or "desc".
        limit: Maximum rows to return; defaults to 10.
        offset: Number of matching rows to skip.

    Returns:
        JSON containing snapshot_date, total_matches, excluded_missing_data, and rows.
        Money fields carry their quote or financial currency labels. If a requested
        sector or industry returns no matches, the response includes the valid values
        present in the snapshot.
    """
    if sort_by not in INDIA_SORT_FIELDS:
        return f"Unknown sort_by '{sort_by}'. Valid options: {', '.join(INDIA_SORT_FIELDS)}"
    if sort_order not in ("asc", "desc"):
        return "sort_order must be 'asc' or 'desc'."
    if limit < 1:
        return "limit must be a positive integer."
    if offset < 0:
        return "offset must be zero or greater."
    sector_filter = sector.strip() if sector else None
    industry_filter = industry.strip() if industry else None
    if sector_filter and sector_filter.casefold() == "it":
        sector_filter = "Technology"
    elif sector_filter and sector_filter.casefold() == "pharma":
        sector_filter = None
        industry_filter = "Drug Manufacturers"
    elif sector_filter and sector_filter.casefold() == "banks":
        sector_filter = None
        industry_filter = "Banks"
    if industry_filter and industry_filter.casefold() == "pharma":
        industry_filter = "Drug Manufacturers"
    elif industry_filter and industry_filter.casefold() == "banks":
        industry_filter = "Banks"
    normalized_symbol = symbol.strip().upper() if symbol else None
    if normalized_symbol:
        normalized_symbol = normalized_symbol.removesuffix(".NS")
        normalized_symbol = f"{normalized_symbol}.NS"
    name_filter = company_name.strip().casefold() if company_name else None

    try:
        snapshot = load_snapshot()
    except FileNotFoundError:
        return "India snapshot is unavailable. Build it with scripts/refresh_india.py."
    tickers = snapshot.get("tickers", {})
    requested_filters = {
        "market_cap": min_market_cap_crore is not None or max_market_cap_crore is not None,
        "sector": sector_filter is not None,
        "industry": industry_filter is not None,
        "trailing_pe": min_trailing_pe is not None or max_trailing_pe is not None,
        "profit_margin": min_profit_margin is not None,
        "revenue_growth": min_revenue_growth is not None,
        "name": name_filter is not None,
    }
    required_fields = {field for field, requested in requested_filters.items() if requested}
    sort_field = INDIA_SORT_FIELDS[sort_by]
    required_fields.add(sort_field)
    if sort_field in {"total_cash", "total_debt"}:
        required_fields.add("financial_currency")
    excluded_missing_data = snapshot.get("skipped_ticker_count", 0)
    matches = []

    for ticker_symbol, record in tickers.items():
        normalized_record_symbol = ticker_symbol.upper().removesuffix(".NS") + ".NS"
        if normalized_symbol and normalized_record_symbol != normalized_symbol:
            continue
        if record.get("status") in ("failed", "skipped"):
            excluded_missing_data += 1
            continue
        values = {
            key: record.get(key)
            for key in (
                "market_cap", "sector", "industry", "trailing_pe",
                "profit_margin", "revenue_growth", "price",
                "name", "total_cash", "total_debt",
                "financial_currency",
            )
        }
        quote_currency = record.get("quote_currency")
        if (
            "market_cap" in required_fields
            and (quote_currency or "").upper() != "INR"
        ):
            values["market_cap"] = None
        missing = any(
            values[field] is None
            or (
                field in {
                    "market_cap", "trailing_pe", "profit_margin",
                    "revenue_growth", "price", "total_cash", "total_debt",
                }
                and (
                    isinstance(values[field], bool)
                    or not isinstance(values[field], (int, float))
                )
            )
            or (
                field in {"sector", "industry", "name"}
                and not isinstance(values[field], str)
            )
            or (
                field == "financial_currency"
                and not isinstance(values[field], str)
            )
            for field in required_fields
        )
        if missing:
            excluded_missing_data += 1
            continue

        market_cap_crore = (
            values["market_cap"] / RUPEES_PER_CRORE
            if values["market_cap"] is not None
            and (quote_currency or "").upper() == "INR"
            else None
        )
        profit_margin_pct = (
            values["profit_margin"] * 100
            if values["profit_margin"] is not None
            else None
        )
        revenue_growth_pct = (
            values["revenue_growth"] * 100
            if values["revenue_growth"] is not None
            else None
        )
        if min_market_cap_crore is not None and market_cap_crore < min_market_cap_crore:
            continue
        if max_market_cap_crore is not None and market_cap_crore > max_market_cap_crore:
            continue
        if sector_filter is not None and sector_filter.casefold() not in values["sector"].casefold():
            continue
        if industry_filter is not None and industry_filter.casefold() not in values["industry"].casefold():
            continue
        if name_filter is not None:
            company = values["name"]
            if not isinstance(company, str):
                excluded_missing_data += 1
                continue
            if name_filter not in company.casefold():
                continue
        if min_trailing_pe is not None and values["trailing_pe"] < min_trailing_pe:
            continue
        if max_trailing_pe is not None and values["trailing_pe"] > max_trailing_pe:
            continue
        if min_profit_margin is not None and profit_margin_pct < min_profit_margin:
            continue
        if min_revenue_growth is not None and revenue_growth_pct < min_revenue_growth:
            continue

        record_for_sort = {
            **values,
            "market_cap": market_cap_crore,
            "profit_margin": profit_margin_pct,
            "revenue_growth": revenue_growth_pct,
        }
        matches.append((ticker_symbol, record, record_for_sort))

    descending = sort_order == "desc"
    if sort_field in {"total_cash", "total_debt"}:
        currency_groups = {}
        for match in matches:
            currency = match[1]["financial_currency"].upper()
            currency_groups.setdefault(currency, []).append(match)
        matches = []
        for currency in sorted(currency_groups):
            matches.extend(sorted(
                currency_groups[currency],
                key=lambda item: item[2][sort_field],
                reverse=descending,
            ))
    else:
        matches.sort(
            key=lambda item: (
                item[2][sort_field].casefold()
                if isinstance(item[2][sort_field], str)
                else item[2][sort_field]
            ),
            reverse=descending,
        )
    total_matches = len(matches)
    page = matches[offset:offset + limit]
    rows = []
    for symbol, record, _ in page:
        quote_currency = record.get("quote_currency")
        financial_currency = record.get("financial_currency")
        rows.append({
            "symbol": symbol,
            "name": record.get("name"),
            "market_cap": format_money(record.get("market_cap"), quote_currency),
            "price": format_money(record.get("price"), quote_currency, scale_inr=False),
            "trailing_pe": fmt_num(record.get("trailing_pe")),
            "sector": record.get("sector"),
            "industry": record.get("industry"),
            "fifty_two_week_low": format_money(
                record.get("fifty_two_week_low"), quote_currency, scale_inr=False
            ),
            "fifty_two_week_high": format_money(
                record.get("fifty_two_week_high"), quote_currency, scale_inr=False
            ),
            "revenue_growth": fmt_pct(record.get("revenue_growth")),
            "profit_margin": fmt_pct(record.get("profit_margin")),
            "total_cash": format_money(record.get("total_cash"), financial_currency),
            "total_debt": format_money(record.get("total_debt"), financial_currency),
            "quote_currency": quote_currency,
            "financial_currency": financial_currency,
        })

    response = {
        "snapshot_date": get_snapshot_date(snapshot),
        "total_matches": total_matches,
        "excluded_missing_data": excluded_missing_data,
        "rows": rows,
    }
    if sort_field in {"total_cash", "total_debt"}:
        response["sort_note"] = (
            "Sorted within each financial currency; amounts in different currencies "
            "are not compared."
        )
    if total_matches == 0 and (sector_filter is not None or industry_filter is not None):
        response["available_sectors"] = sorted({
            record["sector"]
            for record in tickers.values()
            if record.get("status") not in ("failed", "skipped")
            and isinstance(record.get("sector"), str)
        })
        response["available_industries"] = sorted({
            record["industry"]
            for record in tickers.values()
            if record.get("status") not in ("failed", "skipped")
            and isinstance(record.get("industry"), str)
        })
    return json.dumps(response, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    print(company_snapshot.invoke({"symbol": "XP"}))
    print(custom_screener.invoke({"min_market_cap_millions": 300, "max_market_cap_millions": 2000,
                                  "sector": "Technology", "sort_by": "percent_change", "min_volume": 500000}))