import importlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

EVAL_PATH = Path(__file__).resolve().parents[1] / "evals" / "eval.py"
SPEC = importlib.util.spec_from_file_location("stock_screener_eval", EVAL_PATH)
eval_module = importlib.util.module_from_spec(SPEC)
with patch.dict(os.environ, {"GOOGLE_API_KEY": "offline-test-placeholder"}):
    SPEC.loader.exec_module(eval_module)

select_cases = eval_module.select_cases
check_answer = eval_module.check_answer
check_turn = eval_module.check_turn

CASES = [
    {"id": "india_nifty"},
    {"id": "india_sector"},
    {"id": "snapshot_nvda"},
]


class TestEvalCaseSelection(unittest.TestCase):
    def test_prefix_selects_only_matching_cases(self):
        selected = select_cases(CASES, prefix="india_")
        self.assertEqual(
            [case["id"] for case in selected],
            ["india_nifty", "india_sector"],
        )

    def test_case_ids_remain_supported(self):
        selected = select_cases(CASES, only_ids=["snapshot_nvda"])
        self.assertEqual([case["id"] for case in selected], ["snapshot_nvda"])

    def test_unknown_prefix_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "No eval cases found"):
            select_cases(CASES, prefix="missing_")

    def test_unknown_case_id_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Unknown case id"):
            select_cases(CASES, only_ids=["not_a_case"])

    def test_call_limits_allow_two_india_calls_and_reject_us_company_tool(self):
        limits = {"india_screener": {"min": 1, "max": 2}, "company_snapshot": {"max": 0}}
        expected = [{"tool": "india_screener", "args": {"symbol": "INFY"}}]
        actual = [
            {"name": "india_screener", "args": {"symbol": "INFY"}},
            {"name": "india_screener", "args": {"company_name": "Infosys"}},
        ]
        self.assertEqual(check_turn(expected, actual, limits)[:2], (True, True))
        actual.append({"name": "company_snapshot", "args": {"symbol": "INFY"}})
        self.assertFalse(check_turn(expected, actual, limits)[0])

    def test_pharma_case_accepts_healthcare_sector_or_drug_industry(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_pharma_growth"
        )
        expected = case["turns"][0]["calls"]
        for args in (
            {"min_revenue_growth": 10, "sector": "Healthcare"},
            {
                "min_revenue_growth": 10,
                "industry": "Pharma Retail",
            },
            {
                "min_revenue_growth": 10,
                "industry": "Drug Manufacturers - Specialty & Generic",
            },
        ):
            self.assertTrue(check_turn(
                expected,
                [{"name": "india_screener", "args": args}],
            )[1])
        for args in (
            {"min_revenue_growth": 9.99, "sector": "Healthcare"},
            {"min_revenue_growth": 10, "industry": "Biotechnology"},
            {"min_revenue_growth": 10, "sector": "Technology"},
        ):
            result = check_turn(
                expected,
                [{"name": "india_screener", "args": args}],
            )
            self.assertFalse(result[1])
            self.assertIn("min_revenue_growth >= 10", result[2][0])
            self.assertIn("industry containing 'pharma' or 'drug'", result[2][0])

    def test_small_cap_banks_require_market_cap_ascending_and_bank_industry(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_small_cap_sector"
        )
        expected = case["turns"][0]["calls"]
        for args in (
            {
                "industry": "Banks",
                "sort_by": "market_cap",
                "sort_order": "asc",
            },
            {
                "industry": "Commercial BANKS",
                "sector": "Financial Services",
                "sort_by": "market_cap",
                "sort_order": "asc",
            },
        ):
            self.assertTrue(check_turn(
                expected,
                [{"name": "india_screener", "args": args}],
            )[1])
        for args in (
            {"industry": "Banks", "sort_by": "market_cap", "sort_order": "desc"},
            {"industry": "Banks", "sort_by": "price", "sort_order": "asc"},
            {
                "industry": "Banks",
                "sector": "Technology",
                "sort_by": "market_cap",
                "sort_order": "asc",
            },
            {
                "sector": "Financial Services",
                "sort_by": "market_cap",
                "sort_order": "asc",
            },
        ):
            self.assertFalse(check_turn(
                expected,
                [{"name": "india_screener", "args": args}],
            )[1])

    def test_infosys_answer_checks_use_snapshot_values(self):
        from stock_screener.india_data import format_money, get_snapshot_date

        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_infosys_cash_vs_market_cap"
        )
        snapshot = {
            "snapshot_date": "2026-10-20",
            "tickers": {
                "INFY.NS": {
                    "market_cap": 16_266_000_000_000,
                    "quote_currency": "INR",
                    "total_cash": 1_234_000_000,
                    "financial_currency": "USD",
                },
            },
        }
        infy = snapshot["tickers"]["INFY.NS"]
        answer = (
            f"Snapshot date {get_snapshot_date(snapshot)}. "
            f"Market cap in {infy['quote_currency']}: "
            f"{format_money(infy['market_cap'], infy['quote_currency'])}. "
            f"Cash in {infy['financial_currency']}: "
            f"{format_money(infy['total_cash'], infy['financial_currency'])}. "
            "They cannot be compared without an exchange rate."
        )
        eval_cases = importlib.import_module("eval_cases")
        with patch.object(eval_cases, "load_snapshot", return_value=snapshot):
            for caveat in (
                "They are not directly comparable.",
                "They cannot be compared without an exchange rate.",
                "You can't compare them without an exchange rate.",
                "They can't compare these before considering the exchange rate.",
                "They should not be compared before an exchange rate is applied.",
                "They are in different currencies.",
                "They need a common currency.",
            ):
                with self.subTest(caveat=caveat):
                    self.assertEqual(
                        check_answer(case, answer.split("They cannot be compared")[0] + caveat),
                        [],
                    )
            self.assertTrue(check_answer(case, answer.replace("INR", "USD")))
            self.assertTrue(check_answer(case, answer + " Cash is larger than market cap."))
            self.assertTrue(check_answer(case, answer + " The cash-to-market-cap ratio is 2."))

    def test_it_cash_answer_checks_currency_on_each_list_row(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_it_companies_by_cash"
        )
        valid_answers = (
            "- TCS — ₹12,000 crore\nUSD-reporting companies are ranked separately.",
            "- TCS — 12,000 crore INR\nUSD-reporting companies are ranked separately.",
            "- Infosys — USD 3 billion\nUSD reporters are not included in this INR ranking.",
            "- Infosys — $3,124 million\nCompanies reporting in USD are ranked separately.",
            "- One 97 Communications — ₹12,000 crore\nUSD-reporting companies are ranked separately.",
        )
        for answer in valid_answers:
            with self.subTest(answer=answer):
                self.assertEqual(check_answer(case, answer), [])
        bare_figure = check_answer(
            case,
            "- TCS — 12,000 crore\nUSD-reporting companies are ranked separately.",
        )
        self.assertTrue(any("no currency label" in issue for issue in bare_figure))
        silent_omission = check_answer(case, "- TCS — ₹12,000 crore")
        self.assertTrue(any("USD-reporting companies" in issue for issue in silent_omission))
        self.assertTrue(check_answer(
            case,
            "- Infosys — USD 3 billion\n- TCS — ₹12,000 crore\n"
            "USD-reporting companies are ranked separately.",
        ))
        self.assertTrue(check_answer(
            case,
            "- TCS — ₹12,000 crore\n- Infosys — $3 billion\n"
            "USD-reporting companies are ranked separately.",
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
