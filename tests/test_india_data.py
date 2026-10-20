import importlib.util
import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr
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
    def test_write_snapshot_retries_permission_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            original_replace = Path.replace
            attempts = 0

            def replace_with_transient_error(source, target):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("file is temporarily in use")
                return original_replace(source, target)

            with (
                patch.object(Path, "replace", replace_with_transient_error),
                patch.object(refresh_india.time, "sleep") as sleep,
            ):
                refresh_india._write_snapshot(path, {"tickers": {}})

            self.assertEqual(attempts, 2)
            sleep.assert_called_once_with(0.1)
            self.assertTrue(path.is_file())
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"tickers": {}})

    def test_write_snapshot_leaves_temp_file_after_five_permission_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            stderr = io.StringIO()
            with (
                patch.object(Path, "replace", side_effect=PermissionError("in use")),
                patch.object(refresh_india.time, "sleep"),
                redirect_stderr(stderr),
            ):
                with self.assertRaises(PermissionError):
                    refresh_india._write_snapshot(path, {"tickers": {}})

            temporary_path = path.with_suffix(".json.tmp")
            self.assertTrue(temporary_path.is_file())
            self.assertIn(str(temporary_path), stderr.getvalue())
            self.assertEqual(stderr.getvalue().count("Could not replace snapshot"), 1)

    def test_dummy_symbols_are_skipped_and_404_logging_is_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "nifty500_2026-10-20.csv"
            csv_path.write_text(
                "Symbol\nDUMMY1\nLIVE\n",
                encoding="utf-8",
            )
            output_path = root / "india_snapshot.json"

            class FakeTicker:
                def __init__(self, symbol):
                    self.symbol = symbol

                @property
                def info(self):
                    logging.getLogger("yfinance").error("HTTP Error 404: Not Found")
                    return {}

            stderr = io.StringIO()
            with (
                patch.object(refresh_india.yf, "Ticker", side_effect=FakeTicker) as ticker,
                patch.object(refresh_india.time, "sleep"),
                redirect_stderr(stderr),
            ):
                snapshot = refresh_india.refresh(csv_path, output_path)

            self.assertEqual(ticker.call_count, 1)
            self.assertEqual(snapshot["tickers"]["DUMMY1.NS"]["status"], "skipped")
            self.assertEqual(snapshot["tickers"]["LIVE.NS"]["status"], "failed")
            self.assertNotIn("HTTP Error 404", stderr.getvalue())

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
                "SKIP.NS": {
                    "status": "skipped",
                    "reason": "Dummy symbol skipped",
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
        self.assertNotIn("SKIP.NS", snapshot["tickers"])
        self.assertEqual(snapshot["skipped_ticker_count"], 1)
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
            "₹1.23 lakh crore",
        )
        self.assertEqual(
            format_money(1_000_000_000_000, "INR"),
            "₹1 lakh crore",
        )
        self.assertEqual(
            format_money(16_266_032_054_272, "INR"),
            "₹16.27 lakh crore",
        )
        self.assertEqual(
            format_money(999_000_000_000, "INR"),
            "₹99,900.00 crore",
        )
        self.assertEqual(format_money(10_000_000, "INR"), "₹1.00 crore")
        self.assertEqual(format_money(1_000_000_000, "INR"), "₹100.00 crore")
        self.assertEqual(format_money(100_000_000_000, "INR"), "₹10,000.00 crore")
        self.assertEqual(format_money(1_000_000_000_000, "INR"), "₹1 lakh crore")
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
