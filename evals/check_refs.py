import json, re, sys

r = json.load(open(sys.argv[1]))
for x in r:
    if x["case"] in ("ho_memory_second_one", "ho_memory_most_traded_then_first"):
        print(x["case"], "turn", x["turn"] + 1, x["actual_calls"])
        for o in x.get("tool_outputs", []):
            print("  symbols in tool output:", re.findall(r'"symbol": "([^"]+)"', o))
