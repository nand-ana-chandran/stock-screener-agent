
# Each case is a list of turns (several turns test memory).
# Each turn has a prompt and the tool calls we expect.
# An arg check is a literal (exact match, case-insensitive for strings)
# or a function that returns True/False.
import re
import json
from datetime import date

from stock_screener.india_data import format_money, get_snapshot_date, load_snapshot

def between(lo, hi):
    return lambda v: v is not None and lo <= v <= hi

def at_least(n):
    return lambda v: v is not None and v >= n

def non_empty(v):
    return isinstance(v, str) and len(v.strip()) > 0


def infosys_snapshot_pattern(field, currency_field):
    snapshot = load_snapshot()
    record = snapshot["tickers"]["INFY.NS"]
    formatted = format_money(record[field], record[currency_field])
    return re.escape(formatted)


def infosys_snapshot_date_pattern():
    snapshot_date = date.fromisoformat(get_snapshot_date(load_snapshot()))
    formats = (
        snapshot_date.isoformat(),
        snapshot_date.strftime("%d %B %Y"),
        snapshot_date.strftime("%B %d, %Y"),
        snapshot_date.strftime("%d-%m-%Y"),
    )
    return r"(?:%s)" % "|".join(re.escape(value) for value in formats)


def industry_contains(value):
    return lambda actual: isinstance(actual, str) and value.casefold() in actual.casefold()


def industry_is_it(actual):
    return isinstance(actual, str) and (
        re.search(r"\bit\b", actual, re.IGNORECASE) is not None
        or "information technology" in actual.casefold()
        or "software" in actual.casefold()
    )


def check_it_cash_answer(answer, tool_outputs=None):
    issues = []
    figure_pattern = re.compile(
        r"(?<![\w,])(?P<number>(?:\d{1,2}(?:,\d{2})*,\d{3}|"
        r"\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
        r"(?P<unit>lakh\s+crore|crore|million|billion)\b",
        re.IGNORECASE,
    )
    currency_prefix = re.compile(r"(?:₹|\$|\bUSD)\s*$", re.IGNORECASE)
    currency_suffix = re.compile(
        r"^\s*(?:[-–—:(]\s*)?(?P<currency>INR|USD)\b",
        re.IGNORECASE,
    )
    ranked_currencies = set()
    list_item = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
    for line_number, line in enumerate(answer.splitlines()):
        is_ranked_row = list_item.match(line) is not None
        row_currencies = set()
        for match in figure_pattern.finditer(line):
            prefix = line[:match.start("number")]
            suffix = line[match.end("unit"):]
            has_prefix_label = currency_prefix.search(prefix) is not None
            suffix_match = currency_suffix.match(suffix)
            if not has_prefix_label and suffix_match is None:
                issues.append(
                    f"figure {match.group().strip()!r} on line {line_number + 1} "
                    "has no currency label"
                )
                continue
            label = (
                suffix_match.group("currency").upper()
                if suffix_match is not None
                else ("USD" if re.search(r"\bUSD\b|\$", prefix, re.IGNORECASE) else "INR")
            )
            currency = "USD" if label in {"USD", "$"} else "INR"
            if is_ranked_row:
                row_currencies.add(currency)
        if is_ranked_row:
            ranked_currencies.update(row_currencies)

    if ranked_currencies == {"USD", "INR"}:
        issues.append("ranked list mixes USD and INR amounts")

    exclusion_pattern = re.compile(
        r"\b(?:not ranked|excluded|not included|ranked separately|not compared)\b",
        re.IGNORECASE,
    )
    output_groups = {}
    for output in tool_outputs or []:
        try:
            parsed_output = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            continue
        groups = parsed_output.get("currency_groups")
        if isinstance(groups, dict):
            output_groups.update(groups)

    has_multiple_groups = len(output_groups) > 1
    if has_multiple_groups and exclusion_pattern.search(answer) is None:
        issues.append(
            "answer does not state that non-ranked currency groups are excluded, "
            "not ranked, not included, ranked separately, or not compared"
        )
    if has_multiple_groups:
        usd_count = output_groups.get("USD")
        mentions_usd_group = re.search(
            r"\bUSD group\b|\bUSD reporters\b|"
            r"\bUSD(?:[- ]reporting)?[- ]companies\b|"
            r"\bcompanies reporting in USD\b|"
            r"\bHCLTECH\.NS\b|\bINFY\.NS\b",
            answer,
            re.I,
        )
        mentions_count = (
            usd_count is not None
            and re.search(
                rf"\bUSD\b.{{0,20}}\b{usd_count}\b|\b{usd_count}\b.{{0,20}}\bUSD\b",
                answer,
                re.IGNORECASE,
            )
        )
        if not mentions_usd_group and not mentions_count:
            issues.append(
                f"answer does not mention the USD group or its count ({usd_count})"
            )
    return issues


def _india_tool_results(tool_outputs):
    parsed_results = []
    for output in tool_outputs or []:
        try:
            parsed = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
            parsed_results.append(parsed)
    return parsed_results


def _count_pattern(count):
    forms = {str(count), f"{count:,}"}
    small_number_words = {
        0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
        6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
        11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
        15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
        19: "nineteen", 20: "twenty",
    }
    if count in small_number_words:
        forms.add(small_number_words[count])
    return r"(?<![\w,])(?:" + "|".join(re.escape(form) for form in forms) + r")(?!\w)"


def check_india_total_matches_answer(answer, tool_outputs=None):
    results = _india_tool_results(tool_outputs)
    if not results:
        return ["India tool output with total_matches was unavailable to the answer check"]
    count = results[-1].get("total_matches")
    if not isinstance(count, int):
        return ["India tool output did not contain an integer total_matches"]
    count_pattern = _count_pattern(count)
    labeled_count = re.search(
        rf"(?:\b(?:total|matches|companies|stocks)\b.{{0,35}}{count_pattern}"
        rf"|{count_pattern}.{{0,35}}\b(?:total|matches|companies|stocks)\b)",
        answer,
        re.IGNORECASE,
    )
    if labeled_count is None:
        return [f"answer did not state the total of {count} matching companies or stocks"]
    return []


def check_india_debt_free_answer(answer, tool_outputs=None):
    issues = []
    results = _india_tool_results(tool_outputs)
    if not results:
        return ["India tool output with debt-free counts was unavailable to the answer check"]
    result = results[-1]
    debt_free_count = result.get("debt_free_count")
    missing_debt_count = result.get("excluded_by_field", {}).get("total_debt")
    if not isinstance(debt_free_count, int):
        issues.append("India tool output did not contain debt_free_count")
    elif not re.search(
        rf"(?:\bdebt[- ]free\b.{{0,35}}{_count_pattern(debt_free_count)}"
        rf"|{_count_pattern(debt_free_count)}.{{0,35}}\bdebt[- ]free\b)",
        answer,
        re.IGNORECASE,
    ):
        issues.append(f"answer did not state the {debt_free_count} debt-free matches")
    if not isinstance(missing_debt_count, int):
        issues.append("India tool output did not contain excluded_by_field.total_debt")
    elif not re.search(
        rf"(?:{_count_pattern(missing_debt_count)}.{{0,50}}"
        rf"(?:rows?|companies?).{{0,35}}(?:missing|lack|without|unavailable).{{0,20}}debt"
        rf"|(?:missing|lack|without|unavailable).{{0,20}}debt.{{0,50}}"
        rf"{_count_pattern(missing_debt_count)})",
        answer,
        re.IGNORECASE,
    ):
        issues.append(
            f"answer did not state that {missing_debt_count} rows lacked debt data"
        )
    return issues


def check_india_52w_order_answer(answer, tool_outputs=None):
    results = _india_tool_results(tool_outputs)
    if not results:
        return ["India tool output with sorted rows was unavailable to the answer check"]
    symbols = [
        row.get("symbol")
        for row in results[-1]["rows"]
        if isinstance(row, dict) and isinstance(row.get("symbol"), str)
    ]
    if not symbols:
        return []
    positions = [answer.casefold().find(symbol.casefold()) for symbol in symbols]
    if any(position < 0 for position in positions):
        return ["answer did not include every returned symbol in the 52-week-low ranking"]
    if positions != sorted(positions):
        return ["answer did not preserve pct_above_52w_low tool order"]
    return []


SMALL_CAP_TECH_GAINERS = {
    "tool": "custom_screener",
    "args": {
        "sector": "Technology",
        "sort_by": "percent_change",
        "ascending": False,
        "min_volume": at_least(500000),
        "min_market_cap_millions": between(100, 500),
        "max_market_cap_millions": between(1500, 3000),
    },
}

CASES = [
    # --- custom_screener: filters and sorting ---
    {"id": "smallcap_tech_gainers", "turns": [
        {"prompt": "Top small cap tech gainers today", "calls": [SMALL_CAP_TECH_GAINERS]}]},
    {"id": "largest_healthcare", "turns": [
        {"prompt": "Largest healthcare companies by market cap", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Healthcare", "sort_by": "market_cap", "ascending": False}}]}]},
    {"id": "midcap_energy_losers", "turns": [
        {"prompt": "Which mid cap energy stocks fell the most today?", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Energy", "sort_by": "percent_change", "ascending": True,
                "min_market_cap_millions": between(1500, 3000),
                "max_market_cap_millions": between(8000, 12000)}}]}]},
    {"id": "finance_bigcap_by_volume", "turns": [
        {"prompt": "Financial services stocks with market cap above 50 billion, sorted by trading volume", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Financial Services", "sort_by": "volume",
                "min_market_cap_millions": between(45000, 55000)}}]}]},
    {"id": "cheap_largecap_pe", "turns": [
        {"prompt": "Cheapest large cap stocks by P/E ratio", "calls": [
            {"tool": "custom_screener", "args": {
                "sort_by": "pe_ratio", "ascending": True,
                "min_market_cap_millions": at_least(10000)}}]}]},

    # --- simple_screener: predefined lists ---
    {"id": "most_actives", "turns": [
        {"prompt": "Most actively traded stocks today", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "most_actives", "offset": 0}}]}]},
    {"id": "top_mutual_funds", "turns": [
        {"prompt": "Show me the top mutual funds", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "top_mutual_funds", "offset": 0}}]}]},
    {"id": "high_yield_bonds", "turns": [
        {"prompt": "Show me high yield bond funds", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "high_yield_bond", "offset": 0}}]}]},
    {"id": "undervalued_largecaps", "turns": [
        {"prompt": "Show me undervalued large caps", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "undervalued_large_caps", "offset": 0}}]}]},

    # --- company_snapshot ---
    {"id": "snapshot_nvda", "turns": [
        {"prompt": "Tell me about NVDA", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "NVDA"}}]}]},
    {"id": "news_aapl", "turns": [
        {"prompt": "Any recent news on AAPL?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "AAPL"}}]}]},
    {"id": "compare_two", "turns": [
        {"prompt": "Compare MSFT and GOOGL", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "MSFT"}},
            {"tool": "company_snapshot", "args": {"symbol": "GOOGL"}}]}]},
    {"id": "unknown_ticker", "turns": [
        {"prompt": "Tell me about ZZZZNOTREAL", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "ZZZZNOTREAL"}}]}]},

    # --- memory and chaining ---
    {"id": "memory_next_five", "turns": [
        {"prompt": "Top small cap tech gainers today", "calls": [SMALL_CAP_TECH_GAINERS]},
        {"prompt": "Show me the next five", "calls": [
            {"tool": "custom_screener", "args": {
                "offset": 5, "sector": "Technology", "sort_by": "percent_change"}}]}]},
    {"id": "chain_screen_then_detail", "turns": [
        {"prompt": "Top small cap tech gainers today, then give me details on the first one", "calls": [
            SMALL_CAP_TECH_GAINERS,
            {"tool": "company_snapshot", "args": {"symbol": non_empty}}]}]},

    # --- should NOT call any tool ---
    {"id": "no_tool_pe_meaning", "turns": [
        {"prompt": "What does a P/E ratio mean?", "calls": []}]},
    {"id": "no_tool_weather", "turns": [
        {"prompt": "What's the weather in Kochi today?", "calls": []}]},
    {"id": "no_tool_greeting", "turns": [
        {"prompt": "hi", "calls": []}]},

    # --- india_screener: snapshot-only India universe ---
    {"id": "india_nifty_market_cap", "turns": [
        {"prompt": "Show the top 10 Nifty companies by market cap in India",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "market_cap", "sort_order": "desc", "limit": 10}}]}]},
    {"id": "india_sector_pe", "turns": [
        {"prompt": "For NSE technology companies, show trailing P/E from 10 to 25, lowest first",
         "calls": [{"tool": "india_screener", "args": {
             "sector": "Technology", "min_trailing_pe": 10,
             "max_trailing_pe": 25, "sort_by": "trailing_pe",
             "sort_order": "asc"}}]}]},
    {"id": "india_pharma_growth", "turns": [
        {"prompt": "Indian pharma industry companies with at least 10% revenue growth",
         "calls": [{"tool": "india_screener", "args": {
             "min_revenue_growth": at_least(10)},
             "any_args": [
                 {"sector": "Healthcare"},
                 {"industry": industry_contains("pharma")},
                 {"industry": industry_contains("drug")},
             ],
             "failure_message": (
                 "min_revenue_growth >= 10 and either sector Healthcare or "
                 "industry containing 'pharma' or 'drug'"
             )}]}]},
    {"id": "india_profit_margin", "turns": [
        {"prompt": "NSE companies with profit margin over 15%, highest margin first",
         "calls": [{"tool": "india_screener", "args": {
             "min_profit_margin": 15, "sort_by": "profit_margin",
             "sort_order": "desc"}}]}]},
    {"id": "india_market_cap_range", "turns": [
        {"prompt": "Indian stocks with market cap between 300 and 1000 crore rupees",
         "calls": [{"tool": "india_screener", "args": {
             "min_market_cap_crore": 300, "max_market_cap_crore": 1000}}]}]},
    {"id": "india_next_pe_page", "turns": [
        {"prompt": "Show NSE companies ranked 11 through 20 by lowest trailing P/E",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "trailing_pe", "sort_order": "asc",
             "limit": 10, "offset": 10}}]}]},
    {"id": "india_fastest_revenue_growth", "turns": [
        {"prompt": "Which Indian companies have the fastest revenue growth? Show five",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "revenue_growth", "sort_order": "desc",
             "limit": 5}}]}]},
    {"id": "india_small_cap_sector", "turns": [
        {"prompt": "List the 10 smallest market cap NSE banks in ascending order",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "market_cap", "sort_order": "asc",
             "industry": industry_contains("banks"),
             "sector": lambda value: value is None
             or value.strip().casefold() == "financial services"}}]}]},
    {"id": "india_infosys_cash_vs_market_cap", "turns": [
        {"prompt": "Using the India snapshot for Infosys's NSE ticker INFY.NS, compare total cash "
                   "with market cap. Include both exact formatted values. State cash is in USD "
                   "and market cap is in INR, and that they cannot be compared without an "
                   "exchange rate. Do not give a ratio.",
         "calls": [{"tool": "india_screener", "args": {
             "symbol": lambda value: isinstance(value, str)
             and value.upper().removesuffix(".NS") == "INFY"}}]}],
     "tool_call_limits": {
         "india_screener": {"min": 1, "max": 2},
         "company_snapshot": {"max": 0},
     },
     "answer_must_include": [
         r"\bUSD\b",
         r"\bINR\b",
         r"(?i)cash.{0,100}\bUSD\b",
         r"(?i)market cap.{0,100}\bINR\b",
         infosys_snapshot_date_pattern,
         lambda: infosys_snapshot_pattern("market_cap", "quote_currency"),
         lambda: infosys_snapshot_pattern("total_cash", "financial_currency"),
         r"(?i)(?:\b(?:cannot|can't|can['’]t|not)\b.{0,150}\bexchange rate\b|\bnot directly comparable\b|\bdifferent currencies\b|\bcommon currency\b)",
     ],
     "answer_must_not_match": [
         r"(?i)\b(?:cash[- ]to[- ]market[- ]cap|cash/market[- ]cap)\s+ratio\b",
         r"(?i)(?:cash|market cap).{0,40}(?:larger|higher|greater|exceeds|more than).{0,40}(?:cash|market cap)",
         r"(?i)market cap\W{0,6}(?:is\s+)?USD",
     ]},
    {"id": "india_it_companies_by_cash", "turns": [
        {"prompt": "Which Nifty 500 IT companies have the most cash? Rank by total cash if "
                   "supported, and label the currency for every row.",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "total_cash",
             "sort_order": "desc"},
             "any_args": [
                 {"industry": industry_is_it},
                 {"sector": lambda value: isinstance(value, str)
                 and value.casefold() == "technology"},
             ]}]}],
     "tool_call_limits": {"india_screener": {"min": 1, "max": 2}},
     "answer_checks": [check_it_cash_answer]},
    {"id": "india_debt_free", "turns": [
        {"prompt": "How many NSE companies are debt-free? List the debt-free companies.",
         "calls": [{"tool": "india_screener", "args": {
             "debt_free": True}}]}],
     "answer_checks": [check_india_debt_free_answer]},
    {"id": "india_price_under_100", "turns": [
        {"prompt": "How many NSE companies have a share price below 100 rupees?",
         "calls": [{"tool": "india_screener", "args": {
             "max_price": 100}}]}],
     "answer_checks": [check_india_total_matches_answer]},
    {"id": "india_bagmane_market_cap", "turns": [
        {"prompt": "What is BAGMANE.NS's market cap?",
         "calls": [{"tool": "india_screener", "args": {
             "symbol": "BAGMANE.NS"}}]}],
        "answer_must_include": [r"(?i)\bBAGMANE\.NS\b", r"(?i)\b(?:unavailable|not available|no market cap data|missing|N/A)\b"]},
    {"id": "india_52w_low", "turns": [
        {"prompt": "Which NSE stocks are closest to their 52-week low? Show the top 5.",
         "calls": [{"tool": "india_screener", "args": {
             "sort_by": "pct_above_52w_low", "sort_order": "asc", "limit": 5}}]}],
     "answer_must_include": [r"(?i)52[- ]week low"],
     "answer_checks": [check_india_52w_order_answer]},

    # --- US routing regression: India tool must not be used ---
    {"id": "us_query_does_not_route_to_india", "turns": [
        {"prompt": "Top small cap tech gainers in the US today",
         "calls": [SMALL_CAP_TECH_GAINERS]}]},
]