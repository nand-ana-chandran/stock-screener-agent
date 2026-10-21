import importlib
import importlib.util
import json
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
load_cases_module = eval_module.load_cases_module
parse_args = eval_module.parse_args
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

    def test_india_sector_pe_still_requires_technology_sector(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_sector_pe"
        )
        expected = case["turns"][0]["calls"]
        good_args = {
            "sector": "Technology",
            "min_trailing_pe": 10,
            "max_trailing_pe": 25,
            "sort_by": "trailing_pe",
            "sort_order": "asc",
        }
        self.assertTrue(check_turn(
            expected,
            [{"name": "india_screener", "args": good_args}],
        )[1])
        self.assertFalse(check_turn(
            expected,
            [{
                "name": "india_screener",
                "args": {
                    **good_args,
                    "sector": None,
                    "industry": "IT",
                },
            }],
        )[1])

    def test_case_ids_remain_supported(self):
        selected = select_cases(CASES, only_ids=["snapshot_nvda"])
        self.assertEqual([case["id"] for case in selected], ["snapshot_nvda"])

    def test_heldout_india_cases_load_with_unique_ids(self):
        module = load_cases_module("evals/heldout_india.py")
        ids = [case["id"] for case in module.CASES]

        self.assertEqual(len(ids), 15)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [f"heldout_{index:02d}" for index in range(1, 16)])
        self.assertEqual(len(module.CASES[2]["turns"]), 2)
        self.assertEqual(len(module.CASES[3]["turns"]), 2)

    def test_cases_option_loads_and_selects_heldout_module(self):
        args = parse_args([
            "--cases",
            "evals/heldout_india.py",
            "1",
            "heldout_08",
        ])
        module = load_cases_module(args.cases)
        selected = select_cases(module.CASES, only_ids=args.case_ids)

        self.assertEqual(args.repeats, 1)
        self.assertEqual([case["id"] for case in selected], ["heldout_08"])

    def test_cases_option_preserves_prefix_selection(self):
        args = parse_args([
            "--cases",
            "evals/heldout_india.py",
            "--prefix",
            "heldout_",
        ])
        module = load_cases_module(args.cases)
        selected = select_cases(module.CASES, prefix=args.prefix)

        self.assertEqual(len(selected), 15)

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

    def test_any_of_tool_limit_accepts_us_tool_but_rejects_india_tool(self):
        limits = {
            "_any_of": ["simple_screener", "custom_screener", "company_snapshot"],
            "india_screener": {"max": 0},
        }
        self.assertEqual(
            check_turn(
                [],
                [{"name": "custom_screener", "args": {}}],
                limits,
            )[:2],
            (True, True),
        )
        self.assertFalse(
            check_turn(
                [],
                [{"name": "india_screener", "args": {}}],
                limits,
            )[0]
        )

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
                    "market_cap": 4_579_723_313_152,
                    "quote_currency": "INR",
                    "total_cash": 1_234_000_000,
                    "financial_currency": "USD",
                },
            },
        }
        infy = snapshot["tickers"]["INFY.NS"]
        market_cap = format_money(infy["market_cap"], infy["quote_currency"])
        cash = format_money(infy["total_cash"], infy["financial_currency"])
        self.assertEqual(market_cap, "₹4.58 lakh crore")
        date_forms = (
            "2026-10-20",
            "20 October 2026",
            "October 20, 2026",
            "20-10-2026",
        )
        eval_cases = importlib.import_module("eval_cases")
        with patch.object(eval_cases, "load_snapshot", return_value=snapshot):
            for snapshot_date in date_forms:
                answer = (
                    f"On {snapshot_date}, market cap is INR {market_cap} and cash is "
                    f"USD {cash}; these cannot be compared without an exchange rate."
                )
                with self.subTest(snapshot_date=snapshot_date):
                    self.assertEqual(check_answer(case, answer), [])

            bullet_answer = (
                "- Snapshot date: 20 October 2026\n"
                f"- Market cap (INR): {market_cap}\n"
                f"- Cash (USD): {cash}\n"
                "- They are not directly comparable without a common currency."
            )
            self.assertEqual(check_answer(case, bullet_answer), [])

            wrong_currency_answer = (
                "On October 20, 2026, market cap is USD "
                f"{market_cap}; cash is USD {cash}. They are in different currencies."
            )
            self.assertTrue(check_answer(case, wrong_currency_answer))
            forbidden_result = check_answer(
                case,
                f"On 2026-10-20, Market cap: USD {market_cap}, cash is USD {cash}. "
                "They cannot be compared without an exchange rate.",
            )
            self.assertTrue(any(
                "market cap\\W{0,6}(?:is\\s+)?USD" in reason
                for reason in forbidden_result
            ))
            self.assertTrue(check_answer(case, (
                f"On 2026-10-20, market cap is INR {market_cap} and cash is "
                f"USD {cash}; they are in different currencies. "
                "Cash is larger than market cap."
            )))
            self.assertTrue(check_answer(case, (
                f"On 2026-10-20, market cap is INR {market_cap} and cash is "
                f"USD {cash}; they are in different currencies. "
                "The cash-to-market-cap ratio is 2."
            )))

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
        one_currency_output = [json.dumps({"currency_groups": {"INR": 4}})]
        for answer in valid_answers:
            with self.subTest(answer=answer):
                self.assertEqual(
                    check_answer(case, answer, one_currency_output),
                    [],
                )
        bare_figure = check_answer(
            case,
            "- TCS — 12,000 crore\nUSD-reporting companies are ranked separately.",
        )
        self.assertTrue(any("no currency label" in issue for issue in bare_figure))
        silent_omission = check_answer(
            case,
            "- TCS — ₹12,000 crore",
            one_currency_output,
        )
        self.assertEqual(silent_omission, [])
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
        for wording in (
            "USD reporters are not ranked here.",
            "USD-reporting companies are excluded.",
            "Companies reporting in USD are not included.",
            "USD reporters are ranked separately.",
            "USD amounts are not compared.",
            "USD companies are excluded.",
            "USD reporting companies are not included.",
        ):
            with self.subTest(wording=wording):
                self.assertEqual(
                    check_answer(
                        case,
                        f"- TCS — ₹12,000 crore\n{wording}",
                        one_currency_output,
                    ),
                    [],
                )

        ten_inr_rows = "\n".join(
            f"{index}. Company {index} — ₹{index * 100} crore"
            for index in range(1, 11)
        )
        run_two_shape = (
            f"{ten_inr_rows}\n\n"
            "Note: USD-reporting companies are not ranked in the INR list; "
            "use financial_currency=USD to view them."
        )
        self.assertEqual(check_answer(case, run_two_shape, one_currency_output), [])

    def test_it_cash_two_currency_requirements_follow_tool_output_groups(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_it_companies_by_cash"
        )
        two_groups = [json.dumps({"currency_groups": {"INR": 8, "USD": 2}})]
        one_group_answer = "- TCS — ₹12,000 crore"
        self.assertEqual(check_answer(case, one_group_answer, [
            json.dumps({"currency_groups": {"USD": 2}})
        ]), [])

        two_group_answer = (
            "1. TCS — ₹12,000 crore\n"
            "USD reporters are ranked separately (2 companies)."
        )
        self.assertEqual(check_answer(case, two_group_answer, two_groups), [])

        no_statement = (
            "1. TCS — ₹12,000 crore"
        )
        missing_statement = check_answer(case, no_statement, two_groups)
        self.assertTrue(any("not ranked" in reason for reason in missing_statement))
        self.assertTrue(any("USD group or its count" in reason for reason in missing_statement))

        unnamed_group = (
            "1. TCS — ₹12,000 crore\n"
            "USD-reporting companies are ranked separately."
        )
        missing_count = check_answer(case, unnamed_group, two_groups)
        self.assertEqual(missing_count, [])

        mixed_currency_ranking = (
            "1. TCS — ₹12,000 crore\n"
            "2. Infosys — USD 3 billion\n"
            "USD reporters are ranked separately (2 companies)."
        )
        mixed_result = check_answer(case, mixed_currency_ranking, two_groups)
        self.assertTrue(any("mixes USD and INR" in reason for reason in mixed_result))

    def test_new_india_development_case_answer_checks(self):
        debt_case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_debt_free"
        )
        debt_output = [json.dumps({
            "total_matches": 2,
            "debt_free_count": 2,
            "excluded_by_field": {"total_debt": 1},
            "rows": [],
        })]
        self.assertEqual(
            check_answer(
                debt_case,
                "There are 2 debt-free companies. 1 row lacked debt data.",
                debt_output,
            ),
            [],
        )
        self.assertTrue(check_answer(
            debt_case,
            "There are 2 debt-free companies.",
            debt_output,
        ))

        price_case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_price_under_100"
        )
        self.assertEqual(
            check_answer(
                price_case,
                "There are 3 matching companies in total.",
                [json.dumps({"total_matches": 3, "rows": []})],
            ),
            [],
        )
        self.assertTrue(check_answer(
            price_case,
            "Here are some companies.",
            [json.dumps({"total_matches": 3, "rows": []})],
        ))

        order_case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_52w_low"
        )
        ordered_output = [json.dumps({
            "rows": [{"symbol": "NEAR.NS"}, {"symbol": "FAR.NS"}],
        })]
        self.assertEqual(
            check_answer(
                order_case,
                "Closest to the 52-week low: NEAR.NS, then FAR.NS.",
                ordered_output,
            ),
            [],
        )
        self.assertTrue(check_answer(
            order_case,
            "Closest to the 52-week low: FAR.NS, then NEAR.NS.",
            ordered_output,
        ))

    def test_saved_it_cash_answers_are_checked_against_currency_groups(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_it_companies_by_cash"
        )
        tool_output = [json.dumps({
            "currency_groups": {"INR": 26, "USD": 2},
        })]
        saved_answer_1 = (
            "### Other Currency Groups (Excluded from the above ranking)\n"
            "Rows reporting in other currencies are not ranked in this ranking:\n"
            "- **USD (2 companies):** HCLTECH.NS, INFY.NS.\n"
            '*(Use `financial_currency="USD"` to view the USD group.)*'
        )
        saved_answer_2 = (
            "The ranking below includes companies reporting in **INR** "
            "(26 companies total). Companies reporting in other currencies "
            "(such as the 2 companies in USD: HCLTECH.NS and INFY.NS) are "
            "excluded from this ranking and are not compared across currency groups."
        )
        saved_answer_3 = (
            "Rows reporting in INR (26 companies) are ranked within that currency "
            "only. Rows reporting in other currencies are excluded from this ranking: "
            "USD (2): HCLTECH.NS, INFY.NS."
        )
        for answer in (saved_answer_1, saved_answer_2, saved_answer_3):
            with self.subTest(answer=answer):
                self.assertEqual(check_answer(case, answer, tool_output), [])

        no_usd_statement = (
            "The INR rows are ranked by cash. The USD group has 2 companies."
        )
        missing_status = check_answer(case, no_usd_statement, tool_output)
        self.assertTrue(any("non-ranked currency groups" in issue for issue in missing_status))

        no_usd_reference = "Rows reporting in other currencies are excluded from this ranking."
        missing_group = check_answer(case, no_usd_reference, tool_output)
        self.assertTrue(any("USD group or its count" in issue for issue in missing_group))

    def test_it_cash_eval_allows_two_screener_calls(self):
        case = next(
            case for case in eval_module.CASES
            if case["id"] == "india_it_companies_by_cash"
        )
        limits = case["tool_call_limits"]
        actual = [
            {
                "name": "india_screener",
                "args": {
                    "sector": "Technology",
                    "sort_by": "total_cash",
                    "sort_order": "desc",
                },
            },
            {"name": "india_screener", "args": {"financial_currency": "INR"}},
        ]
        self.assertTrue(check_turn(case["turns"][0]["calls"], actual, limits)[0])
        for industry in (
            "IT",
            "Information Technology Services",
            "Software - Application",
        ):
            with self.subTest(industry=industry):
                self.assertTrue(check_turn(
                    case["turns"][0]["calls"],
                    [{
                        "name": "india_screener",
                        "args": {
                            "industry": industry,
                            "sort_by": "total_cash",
                            "sort_order": "desc",
                        },
                    }],
                    limits,
                )[1])
        actual.append({"name": "india_screener", "args": {"financial_currency": "USD"}})
        self.assertFalse(check_turn(case["turns"][0]["calls"], actual, limits)[0])
        only_sector_call = [{
            "name": "india_screener",
            "args": {
                "sector": "Technology",
                "sort_by": "total_cash",
                "sort_order": "desc",
            },
        }]
        self.assertTrue(check_turn(
            case["turns"][0]["calls"],
            only_sector_call,
            limits,
        )[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
