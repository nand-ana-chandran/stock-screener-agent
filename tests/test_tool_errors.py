import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from stock_screener.tool import company_snapshot, custom_screener, simple_screener


class HttpFailure(Exception):
    def __init__(self, status, message="request failed"):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status)


class TestYahooErrorHandling(unittest.TestCase):
    def test_blocked_screener_is_retried_and_logged_without_url_or_key(self):
        failure = HttpFailure(429, "https://example.invalid/?key=secret")
        stderr = io.StringIO()
        with (
            patch("stock_screener.tool.yf.screen", side_effect=failure) as screen,
            patch("stock_screener.tool.time.sleep") as sleep,
            redirect_stderr(stderr),
        ):
            result = simple_screener.invoke({"screen_type": "day_gainers"})

        self.assertEqual(
            result,
            "Yahoo is rate-limiting this host. Please try again later.",
        )
        self.assertEqual(screen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertIn("exception_type=HttpFailure", stderr.getvalue())
        self.assertIn("http_status=429", stderr.getvalue())
        self.assertNotIn("example.invalid", stderr.getvalue())
        self.assertNotIn("secret", stderr.getvalue())
        self.assertNotIn("check the ticker", result.lower())

    def test_network_timeout_is_retried_with_a_distinct_message(self):
        stderr = io.StringIO()
        with (
            patch(
                "stock_screener.tool.yf.screen",
                side_effect=TimeoutError("connection timed out"),
            ) as screen,
            patch("stock_screener.tool.time.sleep") as sleep,
            redirect_stderr(stderr),
        ):
            result = custom_screener.invoke({})

        self.assertEqual(
            result,
            "Could not connect to Yahoo Finance due to a network error or timeout. "
            "Please try again later.",
        )
        self.assertEqual(screen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertIn("exception_type=TimeoutError", stderr.getvalue())
        self.assertIn("http_status=unknown", stderr.getvalue())
        self.assertNotIn("check the ticker", result.lower())

    def test_normal_empty_company_data_reports_unknown_symbol(self):
        with (
            patch(
                "stock_screener.tool.yf.Ticker",
                return_value=SimpleNamespace(info={}),
            ) as ticker,
            patch("stock_screener.tool.time.sleep") as sleep,
        ):
            result = company_snapshot.invoke({"symbol": "NOPE"})

        self.assertEqual(
            result,
            "No data found for 'NOPE'. Check the ticker symbol.",
        )
        ticker.assert_called_once_with("NOPE")
        sleep.assert_not_called()

    def test_invalid_crumb_in_company_lookup_reports_rate_limit(self):
        class InvalidCrumbTicker:
            @property
            def info(self):
                raise RuntimeError("Invalid Crumb")

        stderr = io.StringIO()
        with (
            patch(
                "stock_screener.tool.yf.Ticker",
                return_value=InvalidCrumbTicker(),
            ) as ticker,
            patch("stock_screener.tool.time.sleep") as sleep,
            redirect_stderr(stderr),
        ):
            result = company_snapshot.invoke({"symbol": "NOPE"})

        self.assertEqual(
            result,
            "Yahoo is rate-limiting this host. Please try again later.",
        )
        self.assertEqual(ticker.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertIn("exception_type=RuntimeError", stderr.getvalue())
        self.assertIn("http_status=unknown", stderr.getvalue())

    def test_all_blocking_http_statuses_are_classified(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                failure = HttpFailure(status)
                stderr = io.StringIO()
                with (
                    patch("stock_screener.tool.yf.screen", side_effect=failure),
                    patch("stock_screener.tool.time.sleep"),
                    redirect_stderr(stderr),
                ):
                    result = simple_screener.invoke(
                        {"screen_type": "day_gainers"}
                    )
                self.assertIn("rate-limiting this host", result)
                self.assertIn(f"http_status={status}", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
