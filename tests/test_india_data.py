import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_screener.india_data import format_money, get_snapshot_date, load_snapshot


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "refresh_india", ROOT / "scripts" / "refresh_india.py"
)
refresh_india = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh_india)


class TestIndiaData(unittest.TestCase):
    def test_fixture_loads_date_usd_nulls_and_failed_record(self):
        fixture = {
            "snapshot_date": "2026-10-20",
            "fetched_at": "2026-10-20T10:00:00+00:00",
            "tickers": {
                "USDCO.NS": {
                    "status": "ok",
                    "financial_currency": "USD",
                    "total_cash": 1_234_567.0,
                },
                "NULLS.NS": {
                    "status": "partial",
                    "financial_currency": "INR",
                    "total_cash": None,
                    "market_cap": None,
                },
                "FAIL.NS": {
                    "status": "failed",
                    "error_type": "TimeoutError",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "india_snapshot.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")

            snapshot = load_snapshot(path)

        self.assertEqual(get_snapshot_date(snapshot), "2026-10-20")
        self.assertEqual(snapshot["tickers"]["USDCO.NS"]["status"], "ok")
        self.assertEqual(snapshot["tickers"]["NULLS.NS"]["total_cash"], None)
        self.assertEqual(snapshot["tickers"]["FAIL.NS"]["error_type"], "TimeoutError")
        self.assertEqual(format_money(1_234_567.0, "USD"), "USD 1,234,567.00")
        self.assertIsNone(format_money(None, "USD"))

    def test_snapshot_date_falls_back_to_fetch_timestamp(self):
        self.assertEqual(
            get_snapshot_date({"fetched_at": "2026-10-20T10:00:00Z"}),
            "2026-10-20",
        )
        self.assertIsNone(get_snapshot_date({}))

    def test_inr_format_uses_crore_lakh_crore_and_indian_grouping(self):
        self.assertEqual(
            format_money(1_234_567_890_000, "INR"),
            "₹1,23,456.79 crore",
        )
        self.assertEqual(
            format_money(123_456_789_012_340, "INR"),
            "₹12.35 lakh crore",
        )
        self.assertEqual(format_money(100_000_000, "USD"), "USD 100,000,000.00")

    def test_refresh_writes_records_and_resumes_completed_tickers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            csv_path = data_dir / "nifty500_2026-10-20.csv"
            csv_path.write_text(
                "Symbol,Company\nRELIANCE,Example\nINFY.NS,Example\nBROKEN,Example\n",
                encoding="utf-8",
            )
            output_path = data_dir / "india_snapshot.json"

            complete_info = {
                "regularMarketPrice": 100,
                "marketCap": 1_000_000,
                "trailingPE": 20,
                "sector": "Technology",
                "industry": "Software",
                "fiftyTwoWeekLow": 80,
                "fiftyTwoWeekHigh": 120,
                "revenueGrowth": 0.1,
                "profitMargins": 0.2,
                "totalCash": 100,
                "totalDebt": 50,
                "currency": "INR",
                "financialCurrency": "INR",
            }

            def ticker_factory(symbol):
                if symbol == "BROKEN.NS":
                    raise TimeoutError("offline test")
                return type(
                    "FakeTicker",
                    (),
                    {"info": complete_info if symbol == "RELIANCE.NS" else {"marketCap": 2}},
                )()

            with (
                patch.object(refresh_india.yf, "Ticker", side_effect=ticker_factory) as ticker,
                patch.object(refresh_india.time, "sleep") as sleep,
            ):
                snapshot = refresh_india.refresh(csv_path, output_path)
                self.assertEqual(snapshot["tickers"]["RELIANCE.NS"]["status"], "ok")
                self.assertEqual(snapshot["tickers"]["INFY.NS"]["status"], "partial")
                self.assertEqual(snapshot["tickers"]["BROKEN.NS"]["status"], "failed")
                self.assertEqual(
                    snapshot["tickers"]["BROKEN.NS"]["error_type"],
                    "TimeoutError",
                )
                self.assertEqual(ticker.call_count, 6)
                self.assertEqual(
                    [call.args[0] for call in sleep.call_args_list],
                    [0.5, 0.5, 1, 2, 4],
                )

                ticker.reset_mock()
                sleep.reset_mock()
                snapshot = refresh_india.refresh(csv_path, output_path)

            self.assertEqual(ticker.call_count, 4)
            self.assertEqual(snapshot["tickers"]["RELIANCE.NS"]["status"], "ok")
            self.assertEqual(snapshot["tickers"]["INFY.NS"]["status"], "partial")
            self.assertEqual(snapshot["tickers"]["BROKEN.NS"]["status"], "failed")
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["snapshot_date"], "2026-10-20")


if __name__ == "__main__":
    unittest.main(verbosity=2)
