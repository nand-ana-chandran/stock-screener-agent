import json
import os
import tempfile
from unittest.mock import patch

from tool import custom_screener


def run_in_temp_dir(args, quotes):
    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("tool.yf.screen", return_value={"quotes": quotes}) as screen:
                result = custom_screener.invoke(args)
        finally:
            os.chdir(previous_directory)
    return result, screen


def parse_rows(result):
    prefix = "Custom screener results (sorted):\n"
    assert result.startswith(prefix), result
    return json.loads(result[len(prefix):])


def test_gainers_only_excludes_negative_and_sorts_descending():
    quotes = [
        {"symbol": "NEG", "regularMarketChangePercent": -2.0, "regularMarketVolume": 900000, "marketCap": 500000000},
        {"symbol": "SMALL", "regularMarketChangePercent": 1.2, "regularMarketVolume": 800000, "marketCap": 500000000},
        {"symbol": "TOP", "regularMarketChangePercent": 8.5, "regularMarketVolume": 700000, "marketCap": 500000000},
        {"symbol": "ZERO", "regularMarketChangePercent": 0.0, "regularMarketVolume": 600000, "marketCap": 500000000},
    ]
    result, screen = run_in_temp_dir(
        {"sort_by": "percent_change", "ascending": False, "min_percent_change": 0},
        quotes,
    )
    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["TOP", "SMALL"]
    assert screen.call_args.kwargs["sortField"] == "percentchange"
    assert screen.call_args.kwargs["sortAsc"] is False


def test_volume_sort_is_descending_even_if_upstream_rows_are_unsorted():
    quotes = [
        {"symbol": "LOW", "regularMarketVolume": 600000, "marketCap": 500000000},
        {"symbol": "HIGH", "regularMarketVolume": 3000000, "marketCap": 500000000},
        {"symbol": "MID", "regularMarketVolume": 1500000, "marketCap": 500000000},
    ]
    result, _ = run_in_temp_dir(
        {"sort_by": "volume", "ascending": False},
        quotes,
    )
    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["HIGH", "MID", "LOW"]


if __name__ == "__main__":
    test_gainers_only_excludes_negative_and_sorts_descending()
    test_volume_sort_is_descending_even_if_upstream_rows_are_unsorted()
    print("All custom screener tests passed.")
