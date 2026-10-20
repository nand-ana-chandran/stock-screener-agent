import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from stock_screener.tool import company_snapshot


class TestCompanySnapshot(unittest.TestCase):
    def test_business_summary_ends_at_last_complete_sentence_under_limit(self):
        first_sentence = "A complete business summary sentence."
        summary = first_sentence + " The next sentence is incomplete " + ("x" * 400)
        info = {
            "longName": "Example Corporation",
            "longBusinessSummary": summary,
        }
        with (
            patch(
                "stock_screener.tool.yf.Ticker",
                return_value=SimpleNamespace(info=info),
            ),
            patch(
                "stock_screener.tool.yf.Search",
                return_value=SimpleNamespace(news=[]),
            ),
        ):
            result = company_snapshot.invoke({"symbol": "EXAMPLE"})

        snapshot = json.loads(result.split("\n", 1)[1])
        self.assertEqual(snapshot["business_summary"], first_sentence)
        self.assertLessEqual(len(snapshot["business_summary"]), 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
