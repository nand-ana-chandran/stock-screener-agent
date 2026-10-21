"""Held-out India evaluation cases. Do not tune the screener against these cases."""

import json
import re
from datetime import date

from stock_screener.india_data import get_snapshot_date, load_snapshot


def snapshot_date_pattern():
    snapshot_date = date.fromisoformat(get_snapshot_date(load_snapshot()))
    formats = (
        snapshot_date.isoformat(),
        snapshot_date.strftime("%d %B %Y"),
        snapshot_date.strftime("%B %d, %Y"),
        snapshot_date.strftime("%d-%m-%Y"),
    )
    return r"(?:%s)" % "|".join(re.escape(value) for value in formats)


def industry_contains(fragment):
    return lambda value: (
        isinstance(value, str) and fragment.casefold() in value.casefold()
    )


def industry_is_it(value):
    return isinstance(value, str) and (
        re.search(r"\bit\b", value, re.IGNORECASE) is not None
        or "information technology" in value.casefold()
        or "software" in value.casefold()
    )


def sector_labels_check(answer, _tool_outputs):
    sectors = sorted({
        record["sector"]
        for record in load_snapshot()["tickers"].values()
        if isinstance(record.get("sector"), str)
    })
    missing = [
        sector for sector in sectors
        if re.search(rf"(?<!\w){re.escape(sector)}(?!\w)", answer, re.IGNORECASE) is None
    ]
    issues = []
    if missing:
        issues.append(f"answer omitted snapshot sector labels: {missing}")

    valid = {sector.casefold() for sector in sectors}
    bullet_lines = [
        re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().rstrip(".,;")
        for line in answer.splitlines()
        if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line)
    ]
    unrecognized = []
    for line in bullet_lines:
        if line.casefold().startswith("sectors:"):
            line = line.split(":", 1)[1].strip()
        for item in re.split(r"\s*[,;]\s*|\s+and\s+", line, flags=re.IGNORECASE):
            item = item.strip().rstrip(".,;")
            if item and item.casefold() not in valid:
                unrecognized.append(item)
    if unrecognized:
        issues.append(f"answer listed non-snapshot sector labels: {unrecognized}")
    return issues


def yahoo_blocked_answer_check(answer, tool_outputs):
    for output in tool_outputs:
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError):
            continue
        if parsed.get("error") == "yahoo_blocked":
            expected = (
                "Yahoo Finance is temporarily refusing requests from this server "
                "(rate limiting). No data was retrieved."
            )
            issues = []
            if expected not in answer:
                issues.append("answer omitted the required Yahoo refusal message")
            if re.search(r"authorization|permissions", answer, re.IGNORECASE):
                issues.append("answer misdescribed the Yahoo block as an authorization error")
            return issues
    return []


UNSUPPORTED_FILTER_ANSWER = r"\b(?:cannot|can't|not supported|unable)\b"
DEBT_DATA_OR_UNSUPPORTED = (
    r"\b(?:cannot|can't|not supported|unable)\b|"
    r"\b(?:\d+|several|some)\s+(?:rows?|companies?)\b.{0,60}"
    r"\b(?:missing|lack(?:ed|ing)?|without|no)\b.{0,30}\bdebt\b|"
    r"\b(?:missing|unavailable)\s+debt\s+data\b"
)
NOT_COMPLETE_LIST = (
    r"(?i)\b(?:all|complete|every)\b.{0,40}\b(?:companies|stocks|list)\b|"
    r"\b(?:companies|stocks|list)\b.{0,40}\b(?:all|complete|every)\b"
)
INDIA_DATE = [snapshot_date_pattern]


CASES = [
    {
        "id": "heldout_01",
        "turns": [{
            "prompt": "Which NSE companies are debt-free?",
            "calls": [{"tool": "india_screener", "args": {}}],
        }],
        "tool_call_limits": {"india_screener": {"max": 1}},
        "answer_must_include": INDIA_DATE + [DEBT_DATA_OR_UNSUPPORTED],
        "answer_must_not_match": [NOT_COMPLETE_LIST, r"(?i)\bdebt[- ]free\b.{0,30}\b(?:all|every)\b"],
    },
    {
        "id": "heldout_02",
        "turns": [{
            "prompt": "Show NSE companies with a negative profit margin, worst first.",
            "calls": [{"tool": "india_screener", "args": {}}],
        }],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_03",
        "turns": [
            {
                "prompt": "Top 5 NSE banks by market cap.",
                "calls": [{"tool": "india_screener", "args": {}}],
            },
            {
                "prompt": "Now only the ones with a P/E under 10.",
                "calls": [{
                    "tool": "india_screener",
                    "args": {
                        "industry": industry_contains("bank"),
                        "max_trailing_pe": 10,
                    },
                }],
            },
        ],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_04",
        "turns": [
            {
                "prompt": "Show the 10 largest NSE companies by market cap.",
                "calls": [{"tool": "india_screener", "args": {}}],
            },
            {
                "prompt": "Show the next 10.",
                "calls": [{"tool": "india_screener", "args": {"offset": 10}}],
            },
        ],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_05",
        "turns": [{
            "prompt": "NSE stocks with a market cap above 5 lakh crore rupees.",
            "calls": [{
                "tool": "india_screener",
                "args": {"min_market_cap_crore": 500000},
            }],
        }],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_06",
        "turns": [{
            "prompt": "Which NSE stocks trade below 100 rupees?",
            "calls": [{"tool": "india_screener", "args": {}}],
        }],
        "tool_call_limits": {"india_screener": {"max": 1}},
        "answer_must_include": INDIA_DATE + [UNSUPPORTED_FILTER_ANSWER],
        "answer_must_not_match": [NOT_COMPLETE_LIST],
    },
    {
        "id": "heldout_07",
        "turns": [{
            "prompt": "Which Nifty stocks are closest to their 52-week low?",
            "calls": [{"tool": "india_screener", "args": {}}],
        }],
        "tool_call_limits": {"india_screener": {"max": 1}},
        "answer_must_include": INDIA_DATE + [UNSUPPORTED_FILTER_ANSWER],
        "answer_must_not_match": [NOT_COMPLETE_LIST],
    },
    {
        "id": "heldout_08",
        "turns": [{
            "prompt": "Pharma companies with P/E under 30 and revenue growth over 15%, "
                      "highest profit margin first, top 5.",
            "calls": [{
                "tool": "india_screener",
                "args": {
                    "industry": industry_contains("pharma"),
                    "max_trailing_pe": 30,
                    "min_revenue_growth": 15,
                    "sort_by": "profit_margin",
                    "sort_order": "desc",
                    "limit": 5,
                },
            }],
        }],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_09",
        "turns": [{
            "prompt": "Show me NSE bnak stocks with the lowest PE ratio.",
            "calls": [{
                "tool": "india_screener",
                "args": {
                    "industry": industry_contains("bank"),
                    "sort_by": "trailing_pe",
                    "sort_order": "asc",
                },
            }],
        }],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_10",
        "turns": [{
            "prompt": "India ke sabse bade 5 IT companies market cap ke hisaab se dikhao.",
            "calls": [{
                "tool": "india_screener",
                "args": {
                    "industry": industry_is_it,
                    "sort_by": "market_cap",
                    "sort_order": "desc",
                    "limit": 5,
                },
            }],
        }],
        "answer_must_include": INDIA_DATE,
    },
    {
        "id": "heldout_11",
        "turns": [{
            "prompt": "What is BAGMANE's market cap?",
            "calls": [{
                "tool": "india_screener",
                "args": {"symbol": "BAGMANE"},
            }],
        }],
        "answer_must_include": INDIA_DATE + [
            r"(?i)(?:market cap.{0,50}(?:unavailable|not available|missing)|"
            r"(?:unavailable|not available|missing).{0,50}market cap)"
        ],
        "answer_must_not_match": [
            r"(?i)market cap.{0,50}(?:₹|\bINR\b|\$|\bUSD\b)?\s*\d[\d,.]*"
        ],
    },
    {
        "id": "heldout_12",
        "turns": [{
            "prompt": "List the NSE IT companies that report in USD.",
            "calls": [{
                "tool": "india_screener",
                "args": {"financial_currency": "USD"},
            }],
        }],
        "answer_must_include": INDIA_DATE + [r"\bUSD\b"],
    },
    {
        "id": "heldout_13",
        "turns": [{
            "prompt": "How does Infosys compare with TCS on P/E and profit margin?",
            "calls": [
                {"tool": "india_screener", "args": {"symbol": "INFY"}},
                {"tool": "india_screener", "args": {"symbol": "TCS"}},
            ],
        }],
        "answer_must_include": INDIA_DATE + [
            r"(?i)\b(?:infosys|infy)\b",
            r"\bTCS\b",
            r"(?i)\bP/E\b",
            r"(?i)profit margin",
        ],
    },
    {
        "id": "heldout_14",
        "turns": [{
            "prompt": "Which US semiconductor stocks gained the most today?",
            "calls": [],
        }],
        "tool_call_limits": {
            "_any_of": ["simple_screener", "custom_screener", "company_snapshot"],
            "india_screener": {"max": 0},
        },
        "answer_checks": [yahoo_blocked_answer_check],
    },
    {
        "id": "heldout_15",
        "turns": [{
            "prompt": "What sectors can I filter NSE stocks by?",
            "calls": [{"tool": "india_screener", "args": {}}],
        }],
        "answer_must_include": INDIA_DATE,
        "answer_checks": [sector_labels_check],
    },
]
