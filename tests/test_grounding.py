from stock_screener.grounding import check_grounding


result = check_grounding(
    "NVDA has a market cap of $4.52T and a P/E of 37.89",
    ["Market cap: $4.52T; P/E: 37.89"],
    "What is NVDA's market cap and P/E?",
)
assert result["ungrounded"] == []

result = check_grounding("profit margin 24.9%", ["24.90%"], "")
assert result["ungrounded"] == []

result = check_grounding("The price is $312.55", ["Price: $310.00"], "")
assert [item["text"] for item in result["ungrounded"]] == ["$312.55"]

result = check_grounding("1. 2. 3.", [], "")
assert result["ungrounded"] == []

result = check_grounding("The requested value is 42", [], "Please report 42.")
assert result["ungrounded"] == []

result = check_grounding("Revenue was 1.54 billion", ["Revenue: $1.54B"], "")
assert result["ungrounded"] == []

result = check_grounding("52-Week High: 64.76", ["High: 64.76"], "")
assert result["ungrounded"] == []

result = check_grounding(
    "market cap between $300M and $2,000M, volume of 500,000",
    [],
    "",
    [{
        "min_market_cap_millions": 300,
        "max_market_cap_millions": 2000,
        "min_volume": 500000,
    }],
)
assert result["ungrounded"] == []

result = check_grounding("The price is $312.55", ["Price: $310.00"], "")
assert result["ungrounded"] == [{
    "value": 312.55,
    "text": "$312.55",
    "context": "The price is $312.55",
}]

print("All grounding tests passed.")
