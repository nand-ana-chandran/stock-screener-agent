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

YAHOO_BLOCKED_MESSAGE = (
    "Yahoo Finance is temporarily refusing requests from this server "
    "(rate limiting). No data was retrieved."
)


def yahoo_blocked_error(error):
    """Return a structured response for Yahoo authentication/rate-limit blocks."""
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        status = (
            getattr(current, "status_code", None)
            or getattr(current, "code", None)
            or getattr(response, "status_code", None)
        )
        message = str(current)
        if (
            str(status) in {"401", "429"}
            or re.search(r"\b(?:401|429)\b", message)
            or re.search(r"invalid crumb|too many requests", message, re.I)
        ):
            return json.dumps({
                "error": "yahoo_blocked",
                "message": YAHOO_BLOCKED_MESSAGE,
            })
        current = current.__cause__ or current.__context__
    return None


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
        blocked = yahoo_blocked_error(e)
        if blocked:
            return blocked
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
        blocked = yahoo_blocked_error(e)
        if blocked:
            return blocked
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
        blocked = yahoo_blocked_error(e)
        if blocked:
            return blocked
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
    "pct_above_52w_low": "pct_above_52w_low",
}


@tool
def india_screener(
    symbol: str | None = None,
    company_name: str | None = None,
    min_market_cap_crore: float | None = None,
    max_market_cap_crore: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    financial_currency: str | None = None,
    debt_free: bool = False,
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
    revenue_growth is latest-quarter YoY revenue growth (Yahoo).
    Price filters are in INR and only include rows with quote_currency="INR";
    "below 100 rupees" uses max_price=100.
    debt_free=True returns total_debt == 0 and reports missing debt in
    excluded_by_field.total_debt.
    Sort by pct_above_52w_low (price / fifty_two_week_low - 1) to rank proximity
    to the 52-week low; sorting by price is not a substitute.
    Rows with price below their 52-week low are excluded from this sort as
    inconsistent Yahoo data and counted in excluded_by_field.price_vs_52w_low.
    Indian companies and NSE symbols must use this tool, not company_snapshot or
    the US screeners. Real sector values include Technology, Healthcare,
    Financial Services, Consumer Cyclical, Consumer Defensive, Industrials,
    Energy, Utilities, Real Estate, Basic Materials, and Communication Services.
    For "tech", "technology", or "technology sector", use sector="Technology".
    Pass industry="IT" only for IT-related wording such as "IT", "IT companies",
    "IT sector", or "IT services". This covers Information Technology Services
    and software industries, including TCS, Infosys, Wipro, and HCLTech.
    "software" alone covers software industries only.
    This tool cannot compare two fields for a row; sorting by total_debt does not
    identify companies whose debt exceeds their cash.
    debt_free=True returns only rows with total_debt equal to zero. Missing debt is
    reported separately in excluded_by_field.

    Args:
        symbol: Optional NSE symbol with or without .NS, matched case-insensitively.
        company_name: Optional case-insensitive substring of the company name.
        min_market_cap_crore: Optional minimum market cap in INR crore.
        max_market_cap_crore: Optional maximum market cap in INR crore.
        min_price: Optional minimum quote price in INR.
        max_price: Optional maximum quote price in INR.
        sector: Optional sector substring; use "Technology" for tech, technology,
            or technology sector requests.
        industry: Optional case-insensitive industry substring; pass "IT" only for
            IT-related wording (IT companies/sector/services), "software" for software
            industries only, pharma for Drug Manufacturers, and banks for Banks.
        financial_currency: Optional financial statement currency, INR or USD.
        debt_free: If true, include rows with total_debt equal to zero only.
        min_trailing_pe: Optional minimum trailing P/E.
        max_trailing_pe: Optional maximum trailing P/E.
        min_profit_margin: Optional minimum profit margin in percentage points (10 means 10%).
        min_revenue_growth: Optional minimum revenue growth in percentage points (10 means 10%).
        sort_by: One of market_cap, trailing_pe, total_cash, total_debt,
            profit_margin, revenue_growth, price, sector, industry,
            pct_above_52w_low (price / fifty_two_week_low - 1).
        sort_order: "asc" or "desc".
        limit: Maximum rows to return; defaults to 10.
        offset: Number of matching rows to skip.

    Returns:
        JSON containing snapshot_date, total_matches, excluded_missing_data, and rows.
        For debt_free queries, debt_free_count gives the returned match count and
        excluded_by_field.total_debt counts rows missing debt data. When total_matches
        exceeds the requested page size, use total_matches to report the full count.
        A pct_above_52w_low sort excludes rows missing price or 52-week low, or with a
        low at or below zero. It also excludes rows where price is below the 52-week
        low, reporting that count and note in excluded_by_field and note.
        Money fields carry their quote or financial currency labels. If a requested
        revenue_growth is latest-quarter YoY revenue growth (Yahoo); values above 50%
        or below -50% include a caution because they often reflect mergers or accounting
        changes. If a sector or industry filter returns no matches, the response
        includes the valid values present in the snapshot. When cash or debt sorting
        matches multiple financial currencies, currency_groups and note report the
        per-currency counts and explain
        that rows are ranked only within each currency; the note directs the caller to
        use financial_currency to view another group. Sorting does not compare debt
        with cash; Financial Services rows' debt and cash are not comparable with
        those values in other sectors.
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
    aliases_applied = {}
    it_aliases = {"it", "it companies", "it services", "it services companies"}
    it_industry_filter = False
    if sector_filter and sector_filter.casefold() in it_aliases:
        sector_filter = None
        it_industry_filter = True
    elif sector_filter and sector_filter.casefold() == "pharma":
        sector_filter = None
        industry_filter = "Drug Manufacturers"
        aliases_applied["pharma"] = {"industry": "Drug Manufacturers"}
    elif sector_filter and sector_filter.casefold() == "banks":
        sector_filter = None
        industry_filter = "Banks"
        aliases_applied["banks"] = {"industry": "Banks"}
    elif sector_filter and sector_filter.casefold() == "fmcg":
        sector_filter = "Consumer Defensive"
        aliases_applied["FMCG"] = {"sector": "Consumer Defensive"}
    elif sector_filter and sector_filter.casefold() == "auto":
        sector_filter = "Consumer Cyclical"
        industry_filter = "Auto"
        aliases_applied["auto"] = {
            "sector": "Consumer Cyclical",
            "industry": "Auto",
        }
    if industry_filter and industry_filter.casefold() in it_aliases:
        industry_filter = None
        it_industry_filter = True
    elif industry_filter and industry_filter.casefold() == "pharma":
        industry_filter = "Drug Manufacturers"
        aliases_applied["pharma"] = {"industry": "Drug Manufacturers"}
    elif industry_filter and industry_filter.casefold() == "banks":
        industry_filter = "Banks"
        aliases_applied["banks"] = {"industry": "Banks"}
    elif industry_filter and industry_filter.casefold() == "fmcg":
        industry_filter = None
        sector_filter = "Consumer Defensive"
        aliases_applied["FMCG"] = {"sector": "Consumer Defensive"}
    elif industry_filter and industry_filter.casefold() == "auto":
        industry_filter = "Auto"
        sector_filter = "Consumer Cyclical"
        aliases_applied["auto"] = {
            "sector": "Consumer Cyclical",
            "industry": "Auto",
        }
    if financial_currency is not None:
        financial_currency = financial_currency.strip().upper()
        if financial_currency not in {"INR", "USD"}:
            return "financial_currency must be 'INR' or 'USD'."
    normalized_symbol = symbol.strip().upper() if symbol else None
    if normalized_symbol:
        normalized_symbol = normalized_symbol.removesuffix(".NS")
        normalized_symbol = f"{normalized_symbol}.NS"
    name_filter = company_name.strip().casefold() if company_name else None
    lookup_mode = normalized_symbol is not None or name_filter is not None

    try:
        snapshot = load_snapshot()
    except (OSError, ValueError, TypeError, AttributeError):
        return (
            "India data unavailable: the India snapshot could not be loaded. "
            "Build or restore it with scripts/refresh_india.py."
        )
    tickers = snapshot.get("tickers", {})
    requested_filters = {
        "market_cap": (
            (min_market_cap_crore is not None and min_market_cap_crore > 0)
            or max_market_cap_crore is not None
        ),
        "sector": sector_filter is not None,
        "industry": industry_filter is not None or it_industry_filter,
        "financial_currency": financial_currency is not None,
        "trailing_pe": min_trailing_pe is not None or max_trailing_pe is not None,
        "profit_margin": min_profit_margin is not None,
        "revenue_growth": min_revenue_growth is not None,
        "price": min_price is not None or max_price is not None,
        "total_debt": debt_free,
        "name": name_filter is not None,
    }
    filter_required_fields = {
        field for field, requested in requested_filters.items() if requested
    }
    required_fields = set(filter_required_fields)
    sort_field = INDIA_SORT_FIELDS[sort_by]
    if not lookup_mode:
        if sort_field == "pct_above_52w_low":
            required_fields.update({
                "price", "fifty_two_week_low", "pct_above_52w_low",
            })
        else:
            required_fields.add(sort_field)
    if not lookup_mode and sort_field in {"total_cash", "total_debt"}:
        required_fields.add("financial_currency")
    excluded_missing_data = 0
    excluded_by_field = {}
    excluded_below_52w_low = 0
    matches = []

    for ticker_symbol, record in tickers.items():
        normalized_record_symbol = ticker_symbol.upper().removesuffix(".NS") + ".NS"
        if normalized_symbol and normalized_record_symbol != normalized_symbol:
            continue
        values = {
            key: record.get(key)
            for key in (
                "market_cap", "sector", "industry", "trailing_pe",
                "profit_margin", "revenue_growth", "price",
                "name", "total_cash", "total_debt",
                "financial_currency", "fifty_two_week_low", "fifty_two_week_high",
            )
        }
        if record.get("status") in ("failed", "skipped"):
            values = {key: None for key in values}
        quote_currency = record.get("quote_currency")
        values["quote_currency"] = quote_currency
        pct_above_52w_low = None
        price = values["price"]
        fifty_two_week_low = values["fifty_two_week_low"]
        if (
            isinstance(price, (int, float))
            and not isinstance(price, bool)
            and isinstance(fifty_two_week_low, (int, float))
            and not isinstance(fifty_two_week_low, bool)
            and fifty_two_week_low > 0
        ):
            pct_above_52w_low = price / fifty_two_week_low - 1
        values["pct_above_52w_low"] = pct_above_52w_low
        if (
            "market_cap" in required_fields
            and (quote_currency or "").upper() != "INR"
        ):
            values["market_cap"] = None
        if (
            requested_filters["price"]
            and (quote_currency or "").upper() != "INR"
        ):
            values["price"] = None
        missing_fields = {
            field
            for field in required_fields
            if values[field] is None
            or (
                field in {
                    "market_cap", "trailing_pe", "profit_margin",
                    "revenue_growth", "price", "total_cash", "total_debt",
                    "fifty_two_week_low", "pct_above_52w_low",
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
        }

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
        if (
            "market_cap" not in missing_fields
            and min_market_cap_crore is not None
            and min_market_cap_crore > 0
            and market_cap_crore < min_market_cap_crore
        ):
            continue
        if (
            "market_cap" not in missing_fields
            and max_market_cap_crore is not None
            and market_cap_crore > max_market_cap_crore
        ):
            continue
        if (
            "price" not in missing_fields
            and min_price is not None
            and values["price"] < min_price
        ):
            continue
        if (
            "price" not in missing_fields
            and max_price is not None
            and values["price"] > max_price
        ):
            continue
        if (
            "sector" not in missing_fields
            and sector_filter is not None
            and sector_filter.casefold() not in values["sector"].casefold()
        ):
            continue
        if (
            "industry" not in missing_fields
            and industry_filter is not None
            and industry_filter.casefold() not in values["industry"].casefold()
        ):
            continue
        if "industry" not in missing_fields and it_industry_filter:
            industry_value = values["industry"].casefold()
            if not (
                industry_value == "information technology services"
                or industry_value.startswith("software")
            ):
                continue
        if name_filter is not None and "name" not in missing_fields:
            company = values["name"]
            if name_filter not in company.casefold():
                continue
        if (
            "trailing_pe" not in missing_fields
            and min_trailing_pe is not None
            and values["trailing_pe"] < min_trailing_pe
        ):
            continue
        if (
            "trailing_pe" not in missing_fields
            and max_trailing_pe is not None
            and values["trailing_pe"] > max_trailing_pe
        ):
            continue
        if (
            "profit_margin" not in missing_fields
            and min_profit_margin is not None
            and profit_margin_pct < min_profit_margin
        ):
            continue
        if (
            "revenue_growth" not in missing_fields
            and min_revenue_growth is not None
            and revenue_growth_pct < min_revenue_growth
        ):
            continue
        if (
            "financial_currency" not in missing_fields
            and financial_currency is not None
            and values["financial_currency"].upper() != financial_currency
        ):
            continue
        if (
            debt_free
            and "total_debt" not in missing_fields
            and values["total_debt"] != 0
        ):
            continue
        debt_missing = debt_free and "total_debt" in missing_fields
        if debt_missing:
            other_filter_missing = (
                missing_fields & filter_required_fields
            ) - {"total_debt"}
            if not other_filter_missing:
                excluded_by_field["total_debt"] = (
                    excluded_by_field.get("total_debt", 0) + 1
                )
            missing_fields.discard("total_debt")
        if missing_fields:
            excluded_missing_data += 1
            continue
        if debt_missing:
            continue
        if (
            sort_field == "pct_above_52w_low"
            and not lookup_mode
            and values["price"] < values["fifty_two_week_low"]
        ):
            excluded_below_52w_low += 1
            continue

        record_for_sort = {
            **values,
            "market_cap": market_cap_crore,
            "profit_margin": profit_margin_pct,
            "revenue_growth": revenue_growth_pct,
            "pct_above_52w_low": pct_above_52w_low,
        }
        matches.append((ticker_symbol, record, record_for_sort))

    descending = sort_order == "desc"

    def sort_present_before_missing(items, field):
        present = [item for item in items if item[2][field] is not None]
        missing = [item for item in items if item[2][field] is None]
        present.sort(
            key=lambda item: (
                item[2][field].casefold()
                if isinstance(item[2][field], str)
                else item[2][field]
            ),
            reverse=descending,
        )
        return present + missing

    if sort_field in {"total_cash", "total_debt"}:
        currency_groups = {}
        for match in matches:
            currency = (match[1].get("financial_currency") or "unavailable").upper()
            currency_groups.setdefault(currency, []).append(match)
        matches = []
        for currency in sorted(currency_groups):
            matches.extend(sort_present_before_missing(currency_groups[currency], sort_field))
    else:
        matches = sort_present_before_missing(matches, sort_field)
    total_matches = len(matches)
    page = matches[offset:offset + limit]
    rows = []

    def present(value):
        return "unavailable" if lookup_mode and value is None else value

    for symbol, record, sort_values in page:
        quote_currency = record.get("quote_currency")
        financial_currency = record.get("financial_currency")
        rows.append({
            "symbol": symbol,
            "name": present(record.get("name")),
            "market_cap": present(format_money(record.get("market_cap"), quote_currency)),
            "price": present(format_money(record.get("price"), quote_currency, scale_inr=False)),
            "trailing_pe": present(fmt_num(record.get("trailing_pe"))),
            "sector": present(record.get("sector")),
            "industry": present(record.get("industry")),
            "fifty_two_week_low": present(format_money(
                record.get("fifty_two_week_low"), quote_currency, scale_inr=False
            )),
            "fifty_two_week_high": present(format_money(
                record.get("fifty_two_week_high"), quote_currency, scale_inr=False
            )),
            "pct_above_52w_low": present(fmt_pct(sort_values.get("pct_above_52w_low"))),
            "revenue_growth": present(fmt_pct(record.get("revenue_growth"))),
            "profit_margin": present(fmt_pct(record.get("profit_margin"))),
            "total_cash": present(format_money(record.get("total_cash"), financial_currency)),
            "total_debt": present(format_money(record.get("total_debt"), financial_currency)),
            "quote_currency": present(quote_currency),
            "financial_currency": present(financial_currency),
        })

    response = {
        "snapshot_date": get_snapshot_date(snapshot),
        "total_matches": total_matches,
        "excluded_missing_data": excluded_missing_data,
        "revenue_growth_label": "latest-quarter YoY revenue growth (Yahoo)",
        "rows": rows,
    }
    if debt_free or sort_field == "pct_above_52w_low":
        if debt_free:
            excluded_by_field["total_debt"] = excluded_by_field.get("total_debt", 0)
        if sort_field == "pct_above_52w_low":
            excluded_by_field["price_vs_52w_low"] = excluded_below_52w_low
            response["note"] = (
                f"Excluded {excluded_below_52w_low} row(s) with price below the "
                "52-week low because the Yahoo data is inconsistent."
            )
        response["excluded_by_field"] = excluded_by_field
    if debt_free:
        response["debt_free_count"] = total_matches
    if name_filter is not None:
        response["company_name_filter"] = company_name.strip()
    if aliases_applied:
        matched_sectors = sorted({
            record.get("sector")
            for _, record, _ in matches
            if isinstance(record.get("sector"), str)
        })
        matched_industries = sorted({
            record.get("industry")
            for _, record, _ in matches
            if isinstance(record.get("industry"), str)
        })
        response["aliases_applied"] = {
            alias: {
                **resolution,
                "matched_sectors": matched_sectors,
                "matched_industries": matched_industries,
            }
            for alias, resolution in aliases_applied.items()
        }
    if any(
        record.get("sector") == "Financial Services"
        for _, record, _ in matches
    ):
        response["financial_services_note"] = (
            "For Financial Services rows, debt and cash are not comparable with "
            "debt and cash values from other sectors."
        )
    high_revenue_growth = [
        f"{symbol} ({fmt_pct(record.get('revenue_growth'))})"
        for symbol, record, _ in page
        if isinstance(record.get("revenue_growth"), (int, float))
        and not isinstance(record.get("revenue_growth"), bool)
        and (
            record["revenue_growth"] > 0.5
            or record["revenue_growth"] < -0.5
        )
    ]
    if high_revenue_growth:
        response["revenue_growth_caution"] = (
            "Latest-quarter YoY revenue growth above 50% or below -50% often "
            "reflects mergers or accounting changes; treat these values with caution. "
            "Flagged rows: " + ", ".join(high_revenue_growth) + "."
        )
    if sort_field in {"total_cash", "total_debt"}:
        response["sort_note"] = (
            "Sorted within each financial currency; amounts in different currencies "
            "are not compared."
        )
        if len(currency_groups) > 1:
            group_counts = {
                currency: len(group)
                for currency, group in sorted(currency_groups.items())
            }
            response["currency_groups"] = group_counts
            ranked_currency = "INR" if "INR" in currency_groups else next(
                iter(sorted(currency_groups))
            )
            excluded_groups = [
                (currency, group)
                for currency, group in sorted(currency_groups.items())
                if currency != ranked_currency
            ]
            excluded_descriptions = []
            for currency, group in excluded_groups:
                description = f"{currency} ({len(group)})"
                if len(group) <= 5:
                    symbols = ", ".join(match[0] for match in group)
                    description += f": {symbols}"
                excluded_descriptions.append(description)
            response["note"] = (
                f"Rows reporting in {ranked_currency} ({len(currency_groups[ranked_currency])}) "
                "are ranked within that currency "
                "only. Rows in other currency groups are excluded from this ranking: "
                + "; ".join(excluded_descriptions)
                + ". Use "
                + " or ".join(
                    f'financial_currency="{currency}"'
                    for currency, _ in excluded_groups
                )
                + " to see those groups."
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