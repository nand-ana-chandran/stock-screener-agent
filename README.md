# Stock Screener Agent

![tests](https://github.com/nand-ana-chandran/stock-screener-agent/actions/workflows/tests.yml/badge.svg)

**[Live app](https://stock-screener-agent-kudqvwniajbj3tqj24ly4b.streamlit.app)**: US stocks via Yahoo Finance, NSE stocks via a dated Nifty 500 snapshot.

A chat app where you ask questions about stocks in plain English and an AI agent answers from tool data. Built with LangGraph, Gemini and Streamlit, and evaluated with a custom test harness that includes a number-grounding checker.

> Informational only, not financial advice. US data comes from Yahoo Finance through the unofficial `yfinance` library. India data comes from a committed snapshot dated 2026-10-20.

## What it does

- **Rankings and filters (US):** "Top small cap tech gainers today", "Largest healthcare companies by market cap". Filters by market cap range and sector, sorts by market cap, % change, price, volume or P/E, and pages through results ("show me the next five").
- **Predefined lists:** most active stocks, most shorted stocks, undervalued large caps, mutual funds, bond funds.
- **One company:** "Tell me about NVDA", "Is META overvalued?". Returns a business summary, price, P/E, margins, cash and debt, analyst rating and recent headlines. Two companies can be compared.
- **NSE stocks:** "Nifty IT companies by total cash", "Largest NSE banks by market cap", "Indian pharma companies with revenue growth above 10%". See the India section below.
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
| `india_screener` | NSE stocks from the local Nifty 500 snapshot: filters, sorting, pagination, and lookup by symbol or company name. Ranks within one currency group and reports rows excluded for missing data. |

Design choices:

- **Numbers are formatted in code** (`$1.54B`, `24.90%`, `₹8.9 lakh crore`) so raw decimals never reach the LLM, and the model copies values instead of computing them.
- **News failures never lose fundamentals**, because news is fetched separately.
- **Currency mismatches are handled.** If the quote and balance sheet currencies differ, the agent must name both currencies.
- **A tool-call guard** repairs near-miss tool names (see "Known limits").

## India (NSE) support

- `india_screener` runs on a committed snapshot (`data/india_snapshot.json`), built from the Nifty 500 constituents list (`data/nifty500_2026-10-20.csv`) by `scripts/refresh_india.py`. It filters by market cap, trailing P/E, profit margin, revenue growth, price, sector, industry and debt-free status, sorts (including by distance above the 52-week low), paginates, and looks up companies by symbol or name.
- India answers include the snapshot date from the tool output.
- **Currency-aware:** some companies quote in INR but report financials in USD (for example Infosys and HCLTech). Rankings are done within one currency group, the answer names what was excluded, every amount is labelled, and USD values are never converted or compared with rupee values.
- **Missing data is counted and reported, never treated as zero.** A row missing a field that a query needs is excluded, and the tool reports how many were.
- Rupee values are formatted in code in crore and lakh crore with Indian digit grouping.
- The grounding checker understands Indian formats (the rupee sign, crore, lakh crore, digit grouping like `1,62,660`).
- The India tool uses no Yahoo calls at question time, so India questions keep working when Yahoo is rate-limiting the hosting machine. The Gemini API is still needed.

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

Refresh the India snapshot (about 10 minutes for 500 tickers, resumable):

```powershell
uv run python scripts/refresh_india.py
```

### Deployment

The hosted app runs on Streamlit Community Cloud, installing from `uv.lock`. The Gemini key is read from Streamlit secrets first, then from the environment, and is never logged. Each browser session is limited to 15 user messages, and the app shows a plain message when the Gemini quota is exhausted or Yahoo is rate-limiting the host.

## Evaluation

The agent is tested on whether it picks the right tool with the right arguments, using three kinds of checks.

1. **Tool selection and arguments** (`evals/eval.py`): each case lists the expected tool calls and argument checks. Strict: a wrong, missing or extra call fails the turn. India cases also check the answer text, for example that the currency of each row is labelled.
2. **Held-out sets:**
   - **US** (`evals/eval_holdout.py`): originally written after tuning, with different sectors and phrasing than the dev set. After issues were found and fixes were made against it, it no longer represents a fully untouched test set.
   - **India v1** (`evals/heldout_india.py`, tag `heldout-v1`): written before it was run, frozen, and scored once. Its failures drove fixes, so it is now a development set too.
3. **Number grounding** (`src/stock_screener/grounding.py`): checks that every number in the final answer appears in the tool output or in the user's prompt or tool arguments. This is a separate metric and is never merged into the pass rate.

Run it (Gemini API calls, free-tier quota applies):

```powershell
uv run --env-file .env python evals/eval.py 3                                  # dev set, 3 repeats
uv run --env-file .env python evals/eval.py 1 snapshot_nvda                    # one case
uv run --env-file .env python evals/eval.py 3 --prefix india_                  # India dev cases
uv run --env-file .env python evals/eval.py 1 --cases evals/heldout_india.py   # India held-out v1
$env:CASES_MODULE = "eval_holdout"                                             # US held-out set
uv run --env-file .env python evals/eval.py 1
```

### Run offline regression tests

These tests do not call the Gemini API or Yahoo, and CI runs them on every push and pull request:

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object { uv run python $_.FullName }
```

On macOS or Linux: `for f in tests/test_*.py; do uv run python "$f"; done`

They cover custom screening filters, market-cap boundaries, sorting and pagination, the India screener (filters, currency groups, missing-data counts), snapshot loading, tool routing guidance, eval-case selection, and number-grounding checks. Run them after changing the screener tools or evaluation logic.

### Results

| Set | Result | Notes |
| --- | --- | --- |
| Dev eval (33 cases, 1 run) | 31/33 cases, 32/34 turns | Grounding 31/31 answers. Cases were tuned and some checks were relaxed during development (for example the BAGMANE wording check), so this shows regressions, not generalization. `high_yield_bonds` failed once and passed on rerun. |
| US held-out (`eval_holdout`) | 31/32 turns (96.9%) | Historical baseline measured before later fixes. Not re-run, because the set guided fixes. |
| India held-out v1 (15 cases, tag `heldout-v1`, commit `0b7de92`) | 11/15 cases, 13/17 turns | Grounding 16/17 answers (the one flag looks like a false positive on a list number). Written before it was run and frozen. Scored once, before fixes. The set now guides development, so it is no longer an untouched score. |

India held-out v1 failures, as scored: debt-free (counts and missing data not stated), price under 100 and nearest to 52-week low (the tool had no such filter or sort, so the model sorted by price), and the BAGMANE lookup (a name filter dropped a row with missing data and answered "not found"). These were fixed afterwards and covered by new dev cases. Raw results are in `evals/results/`. This is a small, author-written set, not an independent benchmark. Runs used a single repeat, which is thin evidence.

## What testing found

Writing these down because they are the useful part.

1. **A docstring change caused a regression.** Rewording the `simple_screener` description made two held-out cases (`ho_short_interest`, `ho_foreign_funds`) start making two calls. Cause: a missing `offset` argument was rejected and retried. Fix: `offset` defaults to 0. Re-run: 15/15 on those cases. I can't say whether the default or the docstring rewrite did the work, because they were changed together.
2. **"Safe stocks" returned index funds.** The agent used a fund screen (`portfolio_anchors`) for a stocks question, and the result header said "Stock screener results". Fix: split the docstring into STOCK and FUND screens, label results by query type (Stock, Fund, ETF), and add a rule never to use a fund screen for stock requests. `ho_beginner_safe_stocks` went from 0/5 to 5/5. It is a dev case, since I tuned against it. Before the fix, 4 of 5 runs chose `undervalued_large_caps`, a real stock screen, so part of the failure was a disputed expectation.
3. **Predefined screens ignored their sort order.** Tool selection and grounding were both near 100%, but reading the output showed `most_actives` returning tickers in reverse alphabetical order. `simple_screener` passed only each preset's query and dropped its stored `sortField` and `sortType`. Fixed, and the list now starts with high-volume names. The evals missed it because they measure the agent's behaviour, not whether the data source is correct.
4. **The grounding checker had false positives.** First run: 13 flags, all false. "52-Week" was read as the number 52, and filter values the model sent as tool arguments (`300`, `2000`, `500000`) never appear in the tool output. Fixed, with offline regression tests, including one that confirms an invented price is still flagged.
5. **Yahoo connection errors** crashed a turn. `simple_screener` now catches them and returns a readable message.
6. **Grounded did not mean correct.** On the India held-out set, a "closest to 52-week low" answer used only real numbers but listed the wrong stocks, because the tool had no such sort and the model sorted by price instead. Vodafone Idea was listed first at ₹12.28 against a 52-week low of ₹8.13, while the stocks actually closest to their lows were different names. The grounding checker could not catch this, since every number came from tool output. Fixed with a dedicated sort and covered by a dev case.
7. **A name filter turned missing data into "not found".** Searching for BAGMANE dropped the row because its market cap is empty in the snapshot, so the answer said no such company existed. Lookups now keep rows with missing fields and report them as unavailable.
8. **Mixed currencies break naive comparisons.** Infosys quotes in INR but reports cash and debt in USD, so comparing cash to market cap would be wrong by roughly 80x. The tool now ranks within one currency group, names what it excluded, and labels every amount.
9. **Missing-data counts were overstated.** The count included rows that other filters had already removed. Fixed to count exclusions only among rows that pass the other filters, with tests.

## Known limits

- **The model mangles one tool name.** `gemini-3.5-flash-lite` intermittently emits near-miss names for `simple_screener`: `simple_screen`, `simple_screeners`, `simple_screeners:` and `simple_screen_type`. The guard repairs near-miss names with fuzzy matching and every repair is counted separately, so the raw error rate stays visible. The guard cannot supply missing arguments: in one run the repaired call had empty arguments, was rejected, and the retry made the turn fail the strict test.
- **"Top mutual funds" means sorted by today's % change**, which is how Yahoo defines that preset. It is not a quality or long-term return ranking, and it often shows leveraged sector funds.
- **Grounding checks numbers, not claims.** It cannot tell whether a ranking, a "second one" or a comparison is correct (see finding 6). Numbers the model derives itself or filters it chooses (for example a $100B+ cutoff for "safe") can be flagged and need judging by hand.
- **The custom screener is US-only.** NSE stocks are covered by `india_screener`, which runs on a dated snapshot rather than live data.
- **India data is a snapshot, not live.** 472 of about 500 tickers are complete; the rest have some fields missing (often trailing P/E for loss-making companies, and REITs). Refresh with `scripts/refresh_india.py`.
- **No exchange-rate data.** USD-reported values are shown in USD only.
- **The India screener cannot compare two fields for one company** (for example, debt greater than cash).
- **Revenue growth** is Yahoo's latest-quarter year-over-year figure and can be distorted by mergers or accounting changes.
- **Sector and industry labels come from Yahoo.** An "IT" query can include companies Yahoo labels as software or payments technology (Paytm and Pine Labs appeared in an IT cash ranking).
- **Unofficial data source.** `yfinance` can break or rate-limit without notice. The hosted US tools sometimes get rate-limited because Yahoo throttles cloud hosts; the app then says Yahoo is temporarily refusing requests.
- **Free-tier API quota.** Full evaluation runs can hit Gemini's daily request limit.
- **One model, thin repeats.** Everything was run on `gemini-3.5-flash-lite`, mostly with a single repeat.

## Project structure

```text
src/
  stock_screener/
    __init__.py
    flow.py              LangGraph agent, system prompt, tool-call guard
    tool.py              Stock-screening tools and number formatting
    grounding.py         Number-grounding checker (understands Indian formats)
    india_data.py        India snapshot loader and rupee formatting
app.py                  Streamlit chat interface
data/
  nifty500_2026-10-20.csv   Nifty 500 constituents
  india_snapshot.json       Committed snapshot used by india_screener
scripts/
  refresh_india.py      Rebuilds the snapshot from the constituents CSV
evals/
  eval.py               Evaluation harness
  eval_cases.py         Development evaluation cases
  eval_holdout.py       US held-out evaluation cases
  heldout_india.py      India held-out v1 (frozen, tag heldout-v1)
  regrade.py            Re-grade saved results offline
  check_*.py            Evaluation checks
  results/              Saved evaluation result files
tests/
  test_*.py             Offline regression tests (9 files)
.github/workflows/
  tests.yml             Runs the offline tests on every push and pull request
pyproject.toml          Project dependencies and configuration
uv.lock                 Locked dependency versions
```

## Disclaimer

For education and information only. Nothing here is financial advice.