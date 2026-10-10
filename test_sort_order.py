import json
import os
import tempfile
from unittest.mock import patch

from tool import simple_screener


def run_screener(args, side_effect):
    previous_directory = os.getcwd()
    with tempfile.TemporaryDirectory() as temp_directory:
        try:
            os.chdir(temp_directory)
            with patch("tool.yf.screen", side_effect=side_effect) as screen:
                result = simple_screener.invoke(args)
        finally:
            os.chdir(previous_directory)
    return result, screen


def parse_rows(result):
    prefix = "Stock screener results (most_actives):\n"
    assert result.startswith(prefix), result
    rows, _ = json.JSONDecoder().raw_decode(result[len(prefix):])
    return rows


def test_most_actives_rank_by_volume_and_respect_top_ten_limit():
    first_page = [
        {"symbol": f"STK{i}", "shortName": f"Stock {i}", "regularMarketVolume": 110 - i}
        for i in range(10)
    ]
    first_page.append({"symbol": "NO_VOLUME", "shortName": "No Volume"})
    second_page = [
        {"symbol": "LATE_HIGH", "shortName": "Late High", "regularMarketVolume": 1000},
        {"symbol": "LATE_LOW", "shortName": "Late Low", "regularMarketVolume": 50},
    ]

    def screen_page(query, **kwargs):
        page_quotes = first_page if kwargs["offset"] == 0 else second_page
        return {"total": 13, "quotes": page_quotes}

    result, screen = run_screener(
        {"screen_type": "most_actives", "limit": 10},
        screen_page,
    )
    rows = parse_rows(result)

    assert len(rows) == 10
    assert rows[0]["symbol"] == "LATE_HIGH"
    assert [row["regularMarketVolume"] for row in rows] == [
        1000, 110, 109, 108, 107, 106, 105, 104, 103, 102
    ]
    assert [row["rank"] for row in rows] == list(range(1, 11))
    assert screen.call_count == 2
    assert [call.kwargs["sortField"] for call in screen.call_args_list] == [
        "dayvolume", "dayvolume"
    ]
    assert [call.kwargs["sortAsc"] for call in screen.call_args_list] == [False, False]
    assert [call.kwargs["offset"] for call in screen.call_args_list] == [0, 11]
    assert [call.kwargs["size"] for call in screen.call_args_list] == [250, 250]


def test_most_actives_preserve_missing_volume_as_unranked_null():
    result, _ = run_screener(
        {"screen_type": "most_actives", "limit": 5},
        lambda query, **kwargs: {
            "total": 2,
            "quotes": [
                {"symbol": "HAS_VOLUME", "regularMarketVolume": 100},
                {"symbol": "NO_VOLUME"},
            ],
        },
    )
    rows = parse_rows(result)
    assert [row["symbol"] for row in rows] == ["HAS_VOLUME", "NO_VOLUME"]
    assert rows[0]["rank"] == 1
    assert rows[0]["regularMarketVolume"] == 100
    assert rows[1]["rank"] is None
    assert rows[1]["regularMarketVolume"] is None
    assert "1 result(s) have no reported daily volume" in result
    assert "Only 2 stocks were available" in result


def test_most_actives_default_page_size_is_preserved():
    _, screen = run_screener(
        {"screen_type": "most_actives"},
        lambda query, **kwargs: {"quotes": []},
    )
    assert screen.call_args.kwargs == {
        "sortField": "dayvolume",
        "sortAsc": False,
        "size": 250,
        "offset": 0,
    }


if __name__ == "__main__":
    test_most_actives_rank_by_volume_and_respect_top_ten_limit()
    test_most_actives_preserve_missing_volume_as_unranked_null()
    test_most_actives_default_page_size_is_preserved()
    print("All screener sort-order tests passed.")
