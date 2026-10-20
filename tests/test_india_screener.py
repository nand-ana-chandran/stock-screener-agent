import json
import unittest
from unittest.mock import patch

from stock_screener.tool import india_screener


SNAPSHOT = {
    "snapshot_date": "2026-10-20",
    "tickers": {
        "AAA.NS": {
            "status": "ok",
            "name": "Alpha India",
            "market_cap": 3_000_000_000,
            "price": 125.5,
            "trailing_pe": 15.0,
            "sector": "Technology",
            "industry": "Software",
            "fifty_two_week_low": 90.0,
            "fifty_two_week_high": 150.0,
            "revenue_growth": 0.12,
            "profit_margin": 0.2,
            "total_cash": 1_234_567,
            "total_debt": 2_000_000,
            "quote_currency": "INR",
            "financial_currency": "USD",
        },
        "BBB.NS": {
            "status": "ok",
            "name": "Beta India",
            "market_cap": 5_000_000_000,
            "price": 250.0,
            "trailing_pe": 25.0,
            "sector": "Technology",
            "industry": "Hardware",
            "fifty_two_week_low": 200.0,
            "fifty_two_week_high": 300.0,
            "revenue_growth": 0.3,
            "profit_margin": 0.1,
            "total_cash": 500_000_000,
            "total_debt": 100_000_000,
            "quote_currency": "INR",
            "financial_currency": "INR",
        },
        "NULLS.NS": {
            "status": "partial",
            "name": "Null Data Ltd",
            "market_cap": 4_000_000_000,
            "price": None,
            "trailing_pe": None,
            "sector": "Technology",
            "industry": None,
            "fifty_two_week_low": None,
            "fifty_two_week_high": None,
            "revenue_growth": None,
            "profit_margin": None,
            "total_cash": None,
            "total_debt": None,
            "quote_currency": "INR",
            "financial_currency": "INR",
        },
        "FAIL.NS": {
            "status": "failed",
            "error_type": "TimeoutError",
        },
        "SKIP.NS": {
            "status": "skipped",
            "name": "Placeholder company",
            "market_cap": 9_000_000_000_000,
            "quote_currency": "INR",
        },
        "BAGMANE.NS": {
            "status": "partial",
            "name": "BAGMANE Developers",
            "market_cap": 8_000_000_000,
            "price": 200.0,
            "trailing_pe": 10.0,
            "sector": "Real Estate",
            "industry": "Real Estate Development",
            "quote_currency": None,
            "financial_currency": None,
        },
    },
}


class TestIndiaScreener(unittest.TestCase):
    def _invoke(self, args, snapshot=SNAPSHOT):
        with (
            patch("stock_screener.tool.load_snapshot", return_value=snapshot),
            patch(
                "stock_screener.tool.yf.Ticker",
                side_effect=AssertionError("India screener must not use network"),
            ),
        ):
            result = india_screener.invoke(args)
        return json.loads(result)

    def test_filters_sorting_pagination_and_currency_labels(self):
        result = self._invoke({
            "min_market_cap_crore": 300,
            "max_market_cap_crore": 500,
            "sector": "technology",
            "min_trailing_pe": 10,
            "max_trailing_pe": 20,
            "min_profit_margin": 15,
            "min_revenue_growth": 10,
            "sort_by": "market_cap",
            "sort_order": "desc",
            "limit": 1,
            "offset": 0,
        })

        self.assertEqual(result["snapshot_date"], "2026-10-20")
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["excluded_missing_data"], 3)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["symbol"], "AAA.NS")
        self.assertEqual(row["market_cap"], "₹300.00 crore")
        self.assertEqual(row["price"], "₹125.50")
        self.assertEqual(row["total_cash"], "USD 1,234,567.00")
        self.assertEqual(row["total_debt"], "USD 2,000,000.00")
        self.assertEqual(row["profit_margin"], "20.00%")
        self.assertEqual(row["revenue_growth"], "12.00%")
        self.assertEqual(row["quote_currency"], "INR")
        self.assertEqual(row["financial_currency"], "USD")

    def test_null_filter_values_are_excluded_not_zero(self):
        result = self._invoke({
            "sector": "Technology",
            "industry": "Software",
            "min_profit_margin": 0,
            "sort_by": "trailing_pe",
            "sort_order": "asc",
            "limit": 10,
        })

        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["excluded_missing_data"], 3)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["AAA.NS"])

    def test_sort_and_offset_return_correct_page(self):
        result = self._invoke({
            "sort_by": "market_cap",
            "sort_order": "desc",
            "limit": 1,
            "offset": 1,
        })

        self.assertEqual(result["total_matches"], 3)
        self.assertEqual(result["excluded_missing_data"], 3)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["NULLS.NS"])
        self.assertIsNone(result["rows"][0]["price"])

    def test_sort_field_missing_data_is_counted(self):
        result = self._invoke({
            "sort_by": "price",
            "sort_order": "asc",
        })

        self.assertEqual(result["excluded_missing_data"], 3)
        self.assertEqual(result["total_matches"], 3)
        self.assertEqual(
            [row["symbol"] for row in result["rows"]],
            ["AAA.NS", "BAGMANE.NS", "BBB.NS"],
        )

    def test_skipped_ticker_is_unavailable_and_null_currency_is_not_guessed(self):
        result = self._invoke({
            "sort_by": "price",
            "sort_order": "asc",
        })

        symbols = [row["symbol"] for row in result["rows"]]
        self.assertNotIn("SKIP.NS", symbols)
        self.assertIn("BAGMANE.NS", symbols)
        bagmane = next(row for row in result["rows"] if row["symbol"] == "BAGMANE.NS")
        self.assertEqual(bagmane["price"], "200.00 (currency unknown)")
        self.assertEqual(bagmane["market_cap"], "8,000,000,000.00 (currency unknown)")
        self.assertIsNone(bagmane["quote_currency"])
        self.assertEqual(result["excluded_missing_data"], 3)

        market_cap_filter = self._invoke({
            "min_market_cap_crore": 1,
            "sort_by": "price",
        })
        self.assertNotIn(
            "BAGMANE.NS",
            [row["symbol"] for row in market_cap_filter["rows"]],
        )
        self.assertEqual(market_cap_filter["excluded_missing_data"], 4)

    def test_no_sector_matches_returns_valid_sector_and_industry_values(self):
        result = self._invoke({
            "sector": "Pharmaceuticals",
        })

        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["rows"], [])
        self.assertIn("Technology", result["available_sectors"])
        self.assertIn("Software", result["available_industries"])
        self.assertNotIn("Pharmaceuticals", result["available_sectors"])

    def test_symbol_and_company_name_filters_find_infosys_with_currencies(self):
        infy = {
            "status": "ok",
            "name": "Infosys Limited",
            "market_cap": 4_579_723_313_152,
            "price": 1_130.7,
            "trailing_pe": 15.4,
            "sector": "Technology",
            "industry": "Information Technology Services",
            "total_cash": 3_124_000_000,
            "total_debt": 923_000_000,
            "quote_currency": "INR",
            "financial_currency": "USD",
        }
        snapshot = {
            **SNAPSHOT,
            "tickers": {**SNAPSHOT["tickers"], "INFY.NS": infy},
        }

        for query in ({"symbol": "infy"}, {"symbol": "INFY.Ns"}, {"company_name": "fosys"}):
            with self.subTest(query=query):
                result = self._invoke(query, snapshot)
                self.assertEqual(result["total_matches"], 1)
                row = result["rows"][0]
                self.assertEqual(row["symbol"], "INFY.NS")
                self.assertEqual(row["quote_currency"], "INR")
                self.assertEqual(row["financial_currency"], "USD")
                self.assertTrue(row["market_cap"].startswith("₹"))
                self.assertTrue(row["total_cash"].startswith("USD "))

    def test_unknown_symbol_returns_no_rows(self):
        result = self._invoke({"symbol": "NOTREAL"})
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["rows"], [])

    def test_sector_industry_aliases_and_cash_debt_sorts(self):
        snapshot = {
            **SNAPSHOT,
            "tickers": {
                **SNAPSHOT["tickers"],
                "AAA.NS": {
                    **SNAPSHOT["tickers"]["AAA.NS"],
                    "industry": "Drug Manufacturers - Specialty & Generic",
                },
                "BBB.NS": {
                    **SNAPSHOT["tickers"]["BBB.NS"],
                    "industry": "Banks - Regional",
                },
                "CCC.NS": {
                    **SNAPSHOT["tickers"]["AAA.NS"],
                    "sector": "Healthcare",
                    "industry": "Pharma Retail",
                },
                "DDD.NS": {
                    **SNAPSHOT["tickers"]["BBB.NS"],
                    "sector": "Healthcare",
                    "industry": "Banking Services",
                },
            },
        }
        it_result = self._invoke({"sector": "it"}, snapshot)
        self.assertEqual(it_result["total_matches"], 3)

        pharma_result = self._invoke({"industry": "PhArMa"}, snapshot)
        self.assertEqual([row["symbol"] for row in pharma_result["rows"]], ["AAA.NS"])
        pharma_sector_alias = self._invoke({"sector": "PHARMA"}, snapshot)
        self.assertEqual(
            [row["symbol"] for row in pharma_sector_alias["rows"]],
            ["AAA.NS"],
        )

        banks_result = self._invoke({"industry": "banks"}, snapshot)
        self.assertEqual([row["symbol"] for row in banks_result["rows"]], ["BBB.NS"])

        for sort_by in ("total_cash", "total_debt"):
            result = self._invoke({
                "sort_by": sort_by,
                "sort_order": "desc",
                "sector": "Technology",
                "limit": 1,
            }, snapshot)
            self.assertEqual(result["total_matches"], 2)
            self.assertEqual(len(result["rows"]), 1)
            self.assertIn("within each financial currency", result["sort_note"])
            self.assertTrue(all(row[sort_by] is not None for row in result["rows"]))
            self.assertEqual(result["currency_groups"], {"INR": 1, "USD": 1})
            self.assertIn("INR (1)", result["note"])
            self.assertIn("USD (1)", result["note"])
            self.assertIn("within each currency only", result["note"])

    def test_missing_data_count_excludes_rows_filtered_out_by_other_fields(self):
        snapshot = {
            "snapshot_date": "2026-10-20",
            "tickers": {
                "OTHER.NS": {
                    "status": "partial",
                    "industry": "Software",
                    "revenue_growth": None,
                    "market_cap": 2_000_000_000,
                    "quote_currency": "INR",
                },
                "DRUG.NS": {
                    "status": "ok",
                    "industry": "Drug Manufacturers",
                    "revenue_growth": 0.2,
                    "market_cap": 3_000_000_000,
                    "quote_currency": "INR",
                },
            },
        }
        result = self._invoke({
            "industry": "Drug",
            "min_revenue_growth": 10,
            "sort_by": "market_cap",
        }, snapshot)

        self.assertEqual(result["excluded_missing_data"], 0)
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["DRUG.NS"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
