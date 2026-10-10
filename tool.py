# 8. Create tools
import re
import json
from datetime import datetime, timezone

from langchain.tools import tool
import yfinance as yf
from yfinance import EquityQuery


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

# ---------- Tool 1: predefined screener ----------
@tool
def simple_screener(screen_type: str, offset: int = 0, limit: int = 25) -> str:
    """
    Returns a predefined screened list. The 'most_actives' screen is ranked locally by
    daily trading volume, highest first; other predefined screens are not ranked.
    Prefer custom_screener when the user wants a market cap range or sector.
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
    # ... body unchanged from your current version ...
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
            quotes = []
            seen_symbols = set()
            api_offset = 0
            reported_total = None
            candidate_set_complete = False
            while True:
                result = yf.screen(
                    query,
                    sortField=sort_field,
                    sortAsc=sort_ascending,
                    size=SCREENER_PAGE_SIZE,
                    offset=api_offset,
                )
                if reported_total is None:
                    reported_total = result.get("total")
                page_quotes = result.get("quotes", [])
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
SCREENER_PAGE_SIZE = 250


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
        result = None
        raw_quotes = []
        seen_symbols = set()
        api_offset = 0
        reported_total = None
        candidate_set_complete = False

        while True:
            result = yf.screen(
                query,
                sortField=SORT_FIELDS[sort_by],
                sortAsc=ascending,
                size=SCREENER_PAGE_SIZE,
                offset=api_offset,
            )
            if reported_total is None:
                reported_total = result.get("total")

            page_quotes = result.get("quotes", [])
            if not page_quotes:
                candidate_set_complete = (
                    reported_total is None or len(raw_quotes) >= reported_total
                )
                break

            previous_count = len(raw_quotes)
            for quote in page_quotes:
                symbol = quote.get("symbol")
                if symbol is None or symbol not in seen_symbols:
                    raw_quotes.append(quote)
                    if symbol is not None:
                        seen_symbols.add(symbol)

            if reported_total is not None and len(raw_quotes) >= reported_total:
                candidate_set_complete = True
                break
            if len(raw_quotes) == previous_count:
                break
            if reported_total is None and len(page_quotes) < SCREENER_PAGE_SIZE:
                candidate_set_complete = True
                break
            api_offset += len(page_quotes)
    except Exception as e:
        return f"Screener error: {e}"

    retrieved_candidate_count = len(raw_quotes)

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


if __name__ == "__main__":
    print(company_snapshot.invoke({"symbol": "XP"}))
    print(custom_screener.invoke({"min_market_cap_millions": 300, "max_market_cap_millions": 2000,
                                  "sector": "Technology", "sort_by": "percent_change", "min_volume": 500000}))