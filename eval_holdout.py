# Held-out set: written AFTER tuning, with different sectors and phrasing than eval_cases.py.
# Rule: run it once with the final agent. If you change the agent because of it,
# it becomes a dev set and you need NEW held-out prompts.
from eval_cases import between, at_least, non_empty

CASES = [
    # --- custom_screener: new sectors, phrasings, combined constraints ---
    {"id": "ho_tech_under_1b_gainers", "turns": [
        {"prompt": "Which tech stocks are up the most today with a market cap under 1 billion?", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Technology", "sort_by": "percent_change", "ascending": False,
                "max_market_cap_millions": between(800, 1200)}}]}]},
    {"id": "ho_biggest_utilities", "turns": [
        {"prompt": "Biggest utilities companies", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Utilities", "sort_by": "market_cap", "ascending": False}}]}]},
    {"id": "ho_realestate_volume", "turns": [
        {"prompt": "Real estate stocks with the highest trading volume today", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Real Estate", "sort_by": "volume", "ascending": False}}]}]},
    {"id": "ho_cyclical_cheapest_price", "turns": [
        {"prompt": "Cheapest consumer cyclical stocks by share price with at least 5 billion market cap", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Consumer Cyclical", "sort_by": "price", "ascending": True,
                "min_market_cap_millions": between(4500, 5500)}}]}]},
    {"id": "ho_healthcare_largecap_losers", "turns": [
        {"prompt": "Worst performing large cap healthcare stocks today", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Healthcare", "sort_by": "percent_change", "ascending": True,
                "min_market_cap_millions": between(9000, 11000)}}]}]},
    {"id": "ho_comm_services_pe_desc", "turns": [
        {"prompt": "Communication services companies sorted by P/E, highest first", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Communication Services", "sort_by": "pe_ratio", "ascending": False}}]}]},
    {"id": "ho_industrials_midcap_gainers", "turns": [
        {"prompt": "Mid cap industrials gaining today", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Industrials", "sort_by": "percent_change", "ascending": False,
                "min_market_cap_millions": between(1500, 3000),
                "max_market_cap_millions": between(8000, 12000)}}]}]},
    {"id": "ho_typo_energy", "turns": [
        {"prompt": "wats the biggest energy cos rn", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Energy", "sort_by": "market_cap", "ascending": False}}]}]},
    {"id": "ho_tech_2_to_10b_volume", "turns": [
        {"prompt": "Tech stocks between 2 and 10 billion market cap, sorted by trading volume", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Technology", "sort_by": "volume", "ascending": False,
                "min_market_cap_millions": between(1800, 2200),
                "max_market_cap_millions": between(9000, 11000)}}]}]},

    # --- simple_screener: predefined lists, phrased in natural language ---
    {"id": "ho_short_interest", "turns": [
        {"prompt": "Which stocks have the highest short interest?", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "most_shorted_stocks", "offset": 0}}]}]},
    {"id": "ho_foreign_funds", "turns": [
        {"prompt": "Show me conservative funds that invest outside the US", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "conservative_foreign_funds", "offset": 0}}]}]},
    {"id": "ho_undervalued_growth", "turns": [
        {"prompt": "Show me undervalued growth stocks", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "undervalued_growth_stocks", "offset": 0}}]}]},

    # --- company_snapshot: company names, not tickers ---
    {"id": "ho_tesla", "turns": [
        {"prompt": "How is Tesla doing lately?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "TSLA"}}]}]},
    {"id": "ho_zscaler_news", "turns": [
        {"prompt": "What's the latest news on Zscaler?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "ZS"}}]}]},
    {"id": "ho_compare_amzn_wmt", "turns": [
        {"prompt": "Compare Amazon and Walmart", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "AMZN"}},
            {"tool": "company_snapshot", "args": {"symbol": "WMT"}}]}]},
    {"id": "ho_meta_valuation", "turns": [
        {"prompt": "Is META overvalued?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "META"}}]}]},

    # --- memory and follow-ups ---
    {"id": "ho_memory_change_sort", "turns": [
        {"prompt": "Largest financial services companies", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Financial Services", "sort_by": "market_cap", "ascending": False}}]},
        {"prompt": "Now rank those by trading volume instead", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Financial Services", "sort_by": "volume", "ascending": False}}]}]},
    {"id": "ho_memory_second_one", "turns": [
        {"prompt": "Top 5 healthcare companies by market cap", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Healthcare", "sort_by": "market_cap", "ascending": False}}]},
        {"prompt": "Tell me more about the second one", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": non_empty}}]}]},
    {"id": "ho_memory_next_page", "turns": [
        {"prompt": "Largest technology companies", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Technology", "sort_by": "market_cap", "ascending": False}}]},
        {"prompt": "and the next five", "calls": [
            {"tool": "custom_screener", "args": {
                "sector": "Technology", "sort_by": "market_cap", "offset": 5}}]}]},

    # --- should NOT call any tool ---
    {"id": "ho_no_tool_market_cap_meaning", "turns": [
        {"prompt": "Explain what market cap means", "calls": []}]},
    {"id": "ho_no_tool_poem", "turns": [
        {"prompt": "Write me a short poem about the stock market", "calls": []}]},
    {"id": "ho_no_tool_joke", "turns": [
        {"prompt": "Tell me a joke", "calls": []}]},
    # --- beginner-style prompts (added later, written in plain layperson wording) ---
    {"id": "ho_beginner_safe_stocks", "turns": [
        # Judgment call: "safe" is vague. I count a large-cap screen as a reasonable reading.
        {"prompt": "I'm new to investing. What are some safe stocks for a beginner?", "calls": [
            {"tool": "custom_screener", "args": {"min_market_cap_millions": at_least(10000)}}]}]},
    {"id": "ho_should_i_buy_apple", "turns": [
        {"prompt": "Should I buy Apple stock?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "AAPL"}}]}]},
    {"id": "ho_most_valuable_companies", "turns": [
        {"prompt": "Which are the most valuable companies in the world right now?", "calls": [
            {"tool": "custom_screener", "args": {"sort_by": "market_cap", "ascending": False}}]}]},
    {"id": "ho_memory_most_traded_then_first", "turns": [
        {"prompt": "Show me the most traded stocks today", "calls": [
            {"tool": "simple_screener", "args": {"screen_type": "most_actives", "offset": 0}}]},
        {"prompt": "Tell me about the first one", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": non_empty}}]}]},
    {"id": "ho_no_tool_all_savings", "turns": [
        {"prompt": "Is it a good idea to put all my savings into one stock?", "calls": []}]},
    # Stretch case: Indian company. Yahoo needs the exchange suffix (.NS = NSE).
    # Expected to be hard, since my custom screener only covers US-listed stocks.
    {"id": "ho_stretch_reliance", "turns": [
        {"prompt": "What is the share price of Reliance Industries?", "calls": [
            {"tool": "company_snapshot", "args": {"symbol": "RELIANCE.NS"}}]}]},
]