"""Offline tests for the fetch_all_quotes pagination helper.

All calls to yf.screen are mocked; no network access is required.
"""
import unittest
from unittest.mock import patch, call

from stock_screener.tool import fetch_all_quotes, SCREENER_PAGE_SIZE


def _page(symbols, total=None):
    """Build a minimal yf.screen-style response dict."""
    quotes = [{"symbol": s, "regularMarketVolume": 1_000_000} for s in symbols]
    result = {"quotes": quotes}
    if total is not None:
        result["total"] = total
    return result


SENTINEL_QUERY = object()   # stands in for a real EquityQuery
SORT_FIELD = "intradaymarketcap"
SORT_ASC = False


class TestFetchAllQuotesEmpty(unittest.TestCase):
    """yf.screen returns an empty page on the first call."""

    def test_empty_result(self):
        empty = {"quotes": [], "total": 0}
        with patch("stock_screener.tool.yf.screen", return_value=empty) as mock_screen:
            quotes, reported_total, complete, last_result = fetch_all_quotes(
                SENTINEL_QUERY, SORT_FIELD, SORT_ASC
            )

        self.assertEqual(quotes, [])
        self.assertEqual(reported_total, 0)
        self.assertTrue(complete)
        self.assertIs(last_result, empty)
        mock_screen.assert_called_once_with(
            SENTINEL_QUERY,
            sortField=SORT_FIELD,
            sortAsc=SORT_ASC,
            size=SCREENER_PAGE_SIZE,
            offset=0,
        )


class TestFetchAllQuotesDuplicates(unittest.TestCase):
    """Page 2 repeats a symbol from page 1; the duplicate must be dropped."""

    def test_duplicate_symbols(self):
        # Page 1: (SCREENER_PAGE_SIZE - 1) unique "A" entries + 1 "B" entry.
        # total is not set so the short-page rule applies when page2 arrives.
        page1_quotes = [{"symbol": f"A{i}", "regularMarketVolume": 1_000_000}
                        for i in range(SCREENER_PAGE_SIZE - 1)]
        page1_quotes.append({"symbol": "B", "regularMarketVolume": 1_000_000})
        page1 = {"quotes": page1_quotes}   # no "total" key

        # Page 2: B (duplicate) + C. This page is shorter than SCREENER_PAGE_SIZE,
        # which triggers the short-page break (reported_total is still None).
        page2 = _page(["B", "C"])

        with patch("stock_screener.tool.yf.screen", side_effect=[page1, page2]):
            quotes, reported_total, complete, last_result = fetch_all_quotes(
                SENTINEL_QUERY, SORT_FIELD, SORT_ASC
            )

        symbols = [q["symbol"] for q in quotes]
        # B must appear exactly once
        self.assertEqual(symbols.count("B"), 1)
        # All A0..A(n-2) entries must be present
        for i in range(SCREENER_PAGE_SIZE - 1):
            self.assertEqual(symbols.count(f"A{i}"), 1)
        # C must appear once
        self.assertEqual(symbols.count("C"), 1)
        # Total unique: (SCREENER_PAGE_SIZE - 1) A's + B + C
        self.assertEqual(len(quotes), SCREENER_PAGE_SIZE + 1)
        self.assertIsNone(reported_total)
        self.assertTrue(complete)
        self.assertIs(last_result, page2)


class TestFetchAllQuotesReportedTotalReached(unittest.TestCase):
    """reported_total is reached after the first page; no second call is made."""

    def test_reported_total_reached(self):
        # Yahoo says total=2 and the first page delivers exactly 2 quotes.
        page1 = _page(["X", "Y"], total=2)

        with patch("stock_screener.tool.yf.screen", return_value=page1) as mock_screen:
            quotes, reported_total, complete, last_result = fetch_all_quotes(
                SENTINEL_QUERY, SORT_FIELD, SORT_ASC
            )

        self.assertEqual([q["symbol"] for q in quotes], ["X", "Y"])
        self.assertEqual(reported_total, 2)
        self.assertTrue(complete)
        # Only one API call should have been made
        mock_screen.assert_called_once()


class TestFetchAllQuotesShortPage(unittest.TestCase):
    """First page is shorter than SCREENER_PAGE_SIZE and no total is reported.

    The helper must recognise this as the end of the candidate set and set
    candidate_set_complete=True without making a second call.
    """

    def test_short_page_signals_complete(self):
        # 3 results, no total key → helper infers no more pages
        page1 = _page(["P", "Q", "R"])

        with patch("stock_screener.tool.yf.screen", return_value=page1) as mock_screen:
            quotes, reported_total, complete, last_result = fetch_all_quotes(
                SENTINEL_QUERY, SORT_FIELD, SORT_ASC
            )

        self.assertEqual([q["symbol"] for q in quotes], ["P", "Q", "R"])
        self.assertIsNone(reported_total)
        self.assertTrue(complete)
        mock_screen.assert_called_once()


if __name__ == "__main__":
    results = unittest.main(verbosity=2, exit=False)
    if results.result.wasSuccessful():
        print("All fetch_all_quotes tests passed.")
    else:
        raise SystemExit(1)
