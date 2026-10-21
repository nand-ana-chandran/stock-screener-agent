import json
import os
import tempfile
from unittest.mock import patch

from stock_screener.tool import custom_screener


def run_in_temp_dir(args, quotes):
    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("stock_screener.tool.yf.screen", return_value={"quotes": quotes}) as screen:
                result = custom_screener.invoke(args)
        finally:
            os.chdir(previous_directory)
    return result, screen


def parse_rows(result):
    prefix = "Custom screener results (sorted):\n"
    assert result.startswith(prefix), result
    rows, _ = json.JSONDecoder().raw_decode(result[len(prefix):])
    return rows


def test_gainers_only_excludes_negative_and_sorts_descending():
    quotes = [
        {"symbol": "NEG", "regularMarketChangePercent": -2.0, "regularMarketVolume": 900000, "marketCap": 500000000},
        {"symbol": "SMALL", "regularMarketChangePercent": 1.2, "regularMarketVolume": 800000, "marketCap": 500000000},
        {"symbol": "TOP", "regularMarketChangePercent": 8.5, "regularMarketVolume": 700000, "marketCap": 500000000},
        {"symbol": "ZERO", "regularMarketChangePercent": 0.0, "regularMarketVolume": 600000, "marketCap": 500000000},
        {"symbol": "WLDSW", "shortName": "WEARABLE DEVICES LTD C/WTS 29/0",
         "regularMarketChangePercent": 7.56, "regularMarketVolume": 1000000,
         "marketCap": 500000000},
    ]
    result, screen = run_in_temp_dir(
        {"sort_by": "percent_change", "ascending": False, "min_percent_change": 0},
        quotes,
    )
    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["TOP", "SMALL"]
    assert screen.call_args.kwargs["sortField"] == "percentchange"
    assert screen.call_args.kwargs["sortAsc"] is False


def test_yahoo_blocked_errors_are_returned_as_structured_errors():
    for error in (
        RuntimeError("HTTP Error 401: Unauthorized"),
        RuntimeError("HTTP Error 429: Too Many Requests"),
    ):
        with patch("stock_screener.tool.yf.screen", side_effect=error):
            result = custom_screener.invoke({})

        assert json.loads(result) == {
            "error": "yahoo_blocked",
            "message": (
                "Yahoo Finance is temporarily refusing requests from this server "
                "(rate limiting). No data was retrieved."
            ),
        }


def test_gainers_default_volume_threshold_filters_returned_quotes():
    quotes = [
        {
            "symbol": "LOWVOL",
            "regularMarketChangePercent": 50,
            "regularMarketVolume": 364005,
            "marketCap": 500000000,
        },
        {
            "symbol": "ELIGIBLE",
            "regularMarketChangePercent": 10,
            "regularMarketVolume": 500001,
            "marketCap": 500000000,
        },
    ]
    result, screen = run_in_temp_dir(
        {
            "sort_by": "percent_change",
            "ascending": False,
            "min_percent_change": 0,
        },
        quotes,
    )

    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["ELIGIBLE"]
    assert {
        "operator": "GT",
        "operands": ["dayvolume", 500000],
    } in list(query_nodes(screen.call_args.args[0].to_dict()))


def test_gainers_filter_volume_before_selecting_top_ten_across_pages():
    quotes = [
        {
            "symbol": f"LOW{index}",
            "regularMarketChangePercent": 100 - index / 100,
            "regularMarketVolume": 400000,
            "marketCap": 500000000,
        }
        for index in range(250)
    ]
    quotes.extend([
        {
            "symbol": f"ELIGIBLE{index}",
            "regularMarketChangePercent": 20 - index,
            "regularMarketVolume": 500001 + index,
            "marketCap": 500000000,
        }
        for index in range(12)
    ])

    def screen_page(query, **kwargs):
        start = kwargs["offset"]
        return {
            "total": len(quotes),
            "quotes": quotes[start:start + kwargs["size"]],
        }

    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("stock_screener.tool.yf.screen", side_effect=screen_page) as screen:
                result = custom_screener.invoke({
                    "sort_by": "percent_change",
                    "ascending": False,
                    "min_percent_change": 0,
                    "limit": 10,
                })
        finally:
            os.chdir(previous_directory)

    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == [
        f"ELIGIBLE{index}" for index in range(10)
    ]
    assert len(rows) == 10
    assert all(row["regularMarketVolume"] > 500000 for row in rows)
    assert [call.kwargs["offset"] for call in screen.call_args_list] == [0, 250]


def test_explicit_volume_threshold_override_and_no_minimum_are_respected():
    quotes = [
        {
            "symbol": "BELOW_DEFAULT",
            "regularMarketChangePercent": 12,
            "regularMarketVolume": 700000,
            "marketCap": 500000000,
        },
        {
            "symbol": "ABOVE_OVERRIDE",
            "regularMarketChangePercent": 5,
            "regularMarketVolume": 1200000,
            "marketCap": 500000000,
        },
    ]
    result, screen = run_in_temp_dir(
        {
            "sort_by": "percent_change",
            "ascending": False,
            "min_percent_change": 0,
            "min_volume": 1000000,
        },
        quotes,
    )
    assert [row["symbol"] for row in parse_rows(result)] == ["ABOVE_OVERRIDE"]
    assert {
        "operator": "GT",
        "operands": ["dayvolume", 1000000],
    } in list(query_nodes(screen.call_args.args[0].to_dict()))

    result, screen = run_in_temp_dir(
        {
            "sort_by": "percent_change",
            "ascending": False,
            "min_percent_change": 0,
            "min_volume": 0,
        },
        quotes,
    )
    assert [row["symbol"] for row in parse_rows(result)] == [
        "BELOW_DEFAULT", "ABOVE_OVERRIDE"
    ]
    assert all(
        node["operands"][0] != "dayvolume"
        for node in query_nodes(screen.call_args.args[0].to_dict())
        if node.get("operands")
    )


def query_nodes(query):
    if isinstance(query, dict):
        yield query
        for child in query.get("operands", []):
            yield from query_nodes(child)


def test_no_volume_threshold_and_technology_sector_filter():
    _, screen = run_in_temp_dir(
        {
            "sector": "Technology",
            "sort_by": "percent_change",
            "ascending": False,
            "min_volume": 0,
            "min_percent_change": 0,
        },
        [],
    )
    query = screen.call_args.args[0].to_dict()
    nodes = list(query_nodes(query))
    operands = [node.get("operands", []) for node in nodes]
    assert ["sector", "Technology"] in operands
    assert not any(
        node.get("operands", [None])[0] == "dayvolume"
        for node in nodes
    )


def test_small_cap_bounds_exclude_quotes_over_requested_maximum():
    quotes = [
        {
            "symbol": "NVAX",
            "marketCap": 2_103_058_048,
            "regularMarketVolume": 8_000_000,
        },
        {
            "symbol": "INRANGE",
            "marketCap": 1_500_000_000,
            "regularMarketVolume": 2_000_000,
        },
        {
            "symbol": "UNDER",
            "marketCap": 250_000_000,
            "regularMarketVolume": 20_000_000,
        },
    ]
    result, screen = run_in_temp_dir(
        {
            "min_market_cap_millions": 300,
            "max_market_cap_millions": 2000,
            "sector": "Healthcare",
            "sort_by": "volume",
            "ascending": False,
            "min_volume": 1_000_000,
        },
        quotes,
    )
    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["INRANGE"]
    assert rows[0]["marketCap"] == "$1.50B"

    nodes = list(query_nodes(screen.call_args.args[0].to_dict()))
    assert {
        "operator": "GT",
        "operands": ["intradaymarketcap", 300_000_000],
    } in nodes
    assert {
        "operator": "LT",
        "operands": ["intradaymarketcap", 2_000_000_000],
    } in nodes
    assert {
        "operator": "GT",
        "operands": ["dayvolume", 1_000_000],
    } in nodes


def test_top_ten_fetches_all_pages_then_sorts_and_limits_results():
    quotes = [
        {
            "symbol": f"STK{index}",
            "regularMarketChangePercent": (index + 1) / 10,
            "regularMarketVolume": 1,
            "marketCap": 500000000,
        }
        for index in range(250)
    ]
    quotes.extend([
        {
            "symbol": f"LATE{index}",
            "regularMarketChangePercent": 30 + index,
            "regularMarketVolume": 1,
            "marketCap": 500000000,
        }
        for index in range(5)
    ])
    quotes.append({
        "symbol": "WLDSW",
        "shortName": "WEARABLE DEVICES LTD C/WTS 29/0",
        "regularMarketChangePercent": 200,
        "regularMarketVolume": 1,
        "marketCap": 500000000,
    })

    def screen_page(query, **kwargs):
        start = kwargs["offset"]
        return {
            "total": len(quotes),
            "quotes": quotes[start:start + kwargs["size"]],
        }

    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("stock_screener.tool.yf.screen", side_effect=screen_page) as screen:
                result = custom_screener.invoke({
                    "sector": "Technology",
                    "sort_by": "percent_change",
                    "ascending": False,
                    "min_volume": 0,
                    "min_percent_change": 0,
                    "limit": 10,
                })
        finally:
            os.chdir(previous_directory)

    rows = parse_rows(result)
    assert len(rows) == 10
    assert [row["symbol"] for row in rows] == [f"LATE{i}" for i in range(4, -1, -1)] + [
        f"STK{i}" for i in range(249, 244, -1)
    ]
    assert "WLDSW" not in [row["symbol"] for row in rows]
    assert screen.call_count == 2
    assert [call.kwargs["size"] for call in screen.call_args_list] == [250, 250]
    assert [call.kwargs["offset"] for call in screen.call_args_list] == [0, 250]


def test_insufficient_results_are_reported_without_claiming_market_coverage():
    quotes = [
        {
            "symbol": f"NEG{index}",
            "regularMarketChangePercent": -2.0,
            "regularMarketVolume": 600000,
            "marketCap": 500000000,
        }
        for index in range(250)
    ]
    quotes.extend([
        {"symbol": "TOP", "regularMarketChangePercent": 8.5, "regularMarketVolume": 600000, "marketCap": 500000000},
        {"symbol": "SMALL", "regularMarketChangePercent": 1.2, "regularMarketVolume": 600000, "marketCap": 500000000},
    ])

    def screen_page(query, **kwargs):
        start = kwargs["offset"]
        return {
            "total": len(quotes),
            "quotes": quotes[start:start + kwargs["size"]],
        }

    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("stock_screener.tool.yf.screen", side_effect=screen_page) as screen:
                result = custom_screener.invoke({
                    "sort_by": "percent_change",
                    "ascending": False,
                    "min_percent_change": 0,
                    "limit": 10,
                })
        finally:
            os.chdir(previous_directory)

    assert [row["symbol"] for row in parse_rows(result)] == ["TOP", "SMALL"]
    assert "Only 2 matching stocks" in result
    assert "252 unique Yahoo Finance screener candidates retrieved" in result
    assert "may not include every market listing" in result
    assert [call.kwargs["offset"] for call in screen.call_args_list] == [0, 250]


def test_incomplete_yahoo_candidate_set_is_reported():
    pages = [
        {
            "total": 300,
            "quotes": [
                {"symbol": "TOP", "regularMarketChangePercent": 8.5, "regularMarketVolume": 600000, "marketCap": 500000000},
                {"symbol": "SMALL", "regularMarketChangePercent": 1.2, "regularMarketVolume": 600000, "marketCap": 500000000},
            ],
        },
        {"total": 300, "quotes": []},
    ]
    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("stock_screener.tool.yf.screen", side_effect=pages) as screen:
                result = custom_screener.invoke({
                    "sort_by": "percent_change",
                    "ascending": False,
                    "min_percent_change": 0,
                    "limit": 10,
                })
        finally:
            os.chdir(previous_directory)

    assert [row["symbol"] for row in parse_rows(result)] == ["TOP", "SMALL"]
    assert "candidate set may be incomplete" in result
    assert "Yahoo reported 300 candidates" in result
    assert [call.kwargs["offset"] for call in screen.call_args_list] == [0, 2]


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
    test_yahoo_blocked_errors_are_returned_as_structured_errors()
    test_gainers_default_volume_threshold_filters_returned_quotes()
    test_gainers_filter_volume_before_selecting_top_ten_across_pages()
    test_explicit_volume_threshold_override_and_no_minimum_are_respected()
    test_no_volume_threshold_and_technology_sector_filter()
    test_small_cap_bounds_exclude_quotes_over_requested_maximum()
    test_top_ten_fetches_all_pages_then_sorts_and_limits_results()
    test_insufficient_results_are_reported_without_claiming_market_coverage()
    test_incomplete_yahoo_candidate_set_is_reported()
    test_volume_sort_is_descending_even_if_upstream_rows_are_unsorted()
    print("All custom screener tests passed.")
