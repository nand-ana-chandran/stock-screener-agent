# Each case is a list of turns (several turns test memory).
# Each turn has a prompt and the tool calls we expect.
# An arg check is a literal (exact match, case-insensitive for strings)
# or a function that returns True/False.

def between(lo, hi):
    return lambda v: v is not None and lo <= v <= hi

def at_least(n):
    return lambda v: v is not None and v >= n

def non_empty(v):
    return isinstance(v, str) and len(v.strip()) > 0


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
             "industry": "Pharmaceutical", "min_revenue_growth": 10,
             "sort_by": "revenue_growth", "sort_order": "desc"}}]}]},
    {"id": "india_profit_margin", "turns": [
        {"prompt": "NSE companies with profit margin over 15%, highest margin first",
         "calls": [{"tool": "india_screener", "args": {
             "min_profit_margin": 15, "sort_by": "profit_margin",
             "sort_order": "desc"}}]}]},
    {"id": "india_market_cap_range", "turns": [
        {"prompt": "Indian stocks with market cap between 300 and 1000 crore rupees",
         "calls": [{"tool": "india_screener", "args": {
             "min_market_cap_crore": 300, "max_market_cap_crore": 1000,
             "sort_by": "market_cap", "sort_order": "desc"}}]}]},
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
             "sector": "Financial Services", "sort_by": "market_cap",
             "sort_order": "asc", "limit": 10}}]}]},
]