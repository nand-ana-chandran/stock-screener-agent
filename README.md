# Stock Screener Agent
![tests](https://github.com/nand-ana-chandran/stock-screener-agent/actions/workflows/tests.yml/badge.svg)

A chat app where you ask questions about stocks in plain English and an AI agent answers using live Yahoo Finance data. Built with LangGraph, Gemini and Streamlit, and evaluated with a custom test harness.

> Informational only, not financial advice. Data comes from Yahoo Finance through the unofficial `yfinance` library.

## What it does

- **Rankings and filters:** "Top small cap tech gainers today", "Largest healthcare companies by market cap". Filters by market cap range and sector, sorts by market cap, % change, price, volume or P/E, and pages through results ("show me the next five").
- **Predefined lists:** most active stocks, most shorted stocks, undervalued large caps, mutual funds, bond funds.
- **One company:** "Tell me about NVDA", "Is META overvalued?". Returns a business summary, price, P/E, margins, cash and debt, analyst rating and recent headlines. Two companies can be compared.
- **Follow-ups:** the chat has memory, so "tell me about the second one" works.
- **General questions** ("What does market cap mean?") are answered without calling any tool.

## How it works

```text
START -> chatbot (Gemini + tools) -> router -> tools -> chatbot -> ... -> END
```

| Tool | Purpose |
| --- | --- |
| `simple_screener` | Predefined Yahoo screens (stocks, funds, ETFs). `most_actives` is ranked locally by daily volume; other predefined screens are not ranked by the agent. |
| `custom_screener` | US-listed stocks with market cap, sector and volume filters and a real sort order. |
| `company_snapshot` | Fundamentals and up to 3 headlines for one ticker. |

Design choices:

- **Numbers are formatted in code** (`$1.54B`, `24.90%`) so raw decimals never reach the LLM, and the model copies values instead of computing them.
- **News failures never lose fundamentals**, because news is fetched separately.
- **Currency mismatches are handled.** If the quote and balance sheet currencies differ, the agent must name both currencies.
- **A tool-call guard** repairs near-miss tool names (see "Known limits").

## Setup

```powershell
pip install uv
git clone https://github.com/nand-ana-chandran/stock-screener-agent.git
cd stock-screener-agent
uv sync
```

Create a `.env` file (never commit it):

```text
GOOGLE_API_KEY=your-key-here
```

Run the app:

```powershell
uv run --env-file .env streamlit run app.py
```

Or the terminal chat:

```powershell
uv run --env-file .env python -m stock_screener.flow
```

## Evaluation

The agent is tested on whether it picks the right tool with the right arguments, using three kinds of checks.

1. **Tool selection and arguments** (`eval.py`): each case lists the expected tool calls and argument checks. Strict: a wrong, missing or extra call fails the turn.
2. **Held-out set** (`eval_holdout.py`): originally written after tuning, with different sectors and phrasing than the dev set. After issues were found and fixes were made against this set, it no longer represents a fully untouched final test set.
3. **Number grounding** (`grounding.py`): checks that every number in the final answer appears in the tool output or in the user's prompt or tool arguments. This is a separate metric and is never merged into the pass rate.

Run it:

```powershell
$env:CASES_MODULE = "eval_cases"      # or "eval_holdout"
uv run --env-file .env python evals/eval.py 3      # 3 repeats
uv run --env-file .env python evals/eval.py 1 snapshot_nvda  # one case
```

### Run offline regression tests

These tests do not call the Gemini API:

```powershell
uv run python tests/test_custom_screener.py
uv run python tests/test_fetch_all_quotes.py
uv run python tests/test_sort_order.py
uv run python tests/test_grounding.py
```

They cover custom screening filters, market-cap boundaries, positive-gainer filtering, sorting, pagination and result counts, plus number-grounding checks. Run them after changing the screener tools or evaluation logic.

### Results

| Set | Cases | Tool selection | Grounding | Notes |
| --- | --- | --- | --- | --- |
| Held-out, first clean run | 32 turns | 96.9% (31/32) | n/a | Historical baseline measured before subsequent fixes; not a final post-fix score. |
| Dev (`eval_cases`), final run | Not recorded | — | — | A final run result has not been recorded. |
| Held-out, post-fix run | Not recorded | — | — | The set was used to guide fixes, so this would not be an untouched test score. |

Honest reading of the numbers:

- 5 repeats or fewer per case is thin evidence. "No failures in N turns" is not the same as "100% accurate".
- After I fixed problems found on the held-out set, it effectively became a second dev set. A new clean score would need fresh prompts, written once and run once.

## What testing found

Writing these down because they are the useful part.

1. **A docstring change caused a regression.** Rewording the `simple_screener` description made two held-out cases (`ho_short_interest`, `ho_foreign_funds`) start making two calls. Cause: a missing `offset` argument was rejected and retried. Fix: `offset` defaults to 0. Re-run: 15/15 on those cases. I can't say whether the default or the docstring rewrite did the work, because they were changed together.
2. **"Safe stocks" returned index funds.** The agent used a fund screen (`portfolio_anchors`) for a stocks question, and the result header said "Stock screener results". Fix: split the docstring into STOCK and FUND screens, label results by query type (Stock, Fund, ETF), and add a rule never to use a fund screen for stock requests. `ho_beginner_safe_stocks` went from 0/5 to 5/5. It is a dev case, since I tuned against it. Before the fix, 4 of 5 runs chose `undervalued_large_caps`, a real stock screen, so part of the failure was a disputed expectation.
3. **Predefined screens ignored their sort order.** Tool selection and grounding were both near 100%, but reading the output showed `most_actives` returning tickers in reverse alphabetical order. `simple_screener` passed only each preset's query and dropped its stored `sortField` and `sortType`. Fixed, and the list now starts with high-volume names. The evals missed it because they measure the agent's behaviour, not whether the data source is correct.
4. **The grounding checker had false positives.** First run: 13 flags, all false. "52-Week" was read as the number 52, and filter values the model sent as tool arguments (`300`, `2000`, `500000`) never appear in the tool output. Fixed, with offline regression tests, including one that confirms an invented price is still flagged.
5. **Yahoo connection errors** crashed a turn. `simple_screener` now catches them and returns a readable message.

## Known limits

- **The model mangles one tool name.** `gemini-3.5-flash-lite` intermittently emits near-miss names for `simple_screener`: `simple_screen`, `simple_screeners`, `simple_screeners:` and `simple_screen_type`. The guard repairs near-miss names with fuzzy matching and every repair is counted separately, so the raw error rate stays visible. The guard cannot supply missing arguments: in one run the repaired call had empty arguments, was rejected, and the retry made the turn fail the strict test.
- **"Top mutual funds" means sorted by today's % change**, which is how Yahoo defines that preset. It is not a quality or long-term return ranking, and it often shows leveraged sector funds.
- **Grounding checks numbers, not claims.** It cannot tell whether a ranking, a "second one" or a comparison is correct. Numbers the model derives itself or filters it chooses (for example a $100B+ cutoff for "safe") can be flagged and need judging by hand.
- **US stocks only** for the custom screener. Indian tickers need Yahoo suffixes (`.NS`) and are not covered.
- **Unofficial data source.** `yfinance` can break or rate-limit without notice.
- **Free-tier API quota.** Full evaluation runs can hit Gemini's daily request limit.

## Project structure

```text
src/
  stock_screener/
    __init__.py
    flow.py              LangGraph agent, system prompt, tool-call guard
    tool.py              Stock-screening tools and number formatting
    grounding.py         Number-grounding checker
app.py                  Streamlit chat interface
evals/
  eval.py               Evaluation harness
  eval_cases.py         Development evaluation cases
  eval_holdout.py       Held-out evaluation cases
  regrade.py            Re-grade saved results offline
  check_*.py            Evaluation checks
tests/
  test_*.py             Offline regression tests
pyproject.toml          Project dependencies and configuration
uv.lock                 Locked dependency versions
```

## Disclaimer

For education and information only. Nothing here is financial advice.
