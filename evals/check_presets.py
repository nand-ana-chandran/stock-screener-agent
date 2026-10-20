import json
from stock_screener.tool import simple_screener

for s in ["day_gainers", "day_losers", "most_shorted_stocks",
          "undervalued_large_caps", "top_mutual_funds"]:
    out = simple_screener.invoke({"screen_type": s})
    rows = json.loads(out.split("\n", 1)[1])
    print(s, "->", [r["name"] for r in rows])
