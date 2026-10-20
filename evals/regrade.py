import argparse
import glob
import json
import os

from stock_screener.grounding import check_grounding


def regrade(path):
    with open(path, encoding="utf-8") as result_file:
        records = json.load(result_file)

    graded = []
    for record in records:
        tool_outputs = record.get("tool_outputs") or []
        if not tool_outputs:
            continue
        result = check_grounding(
            record.get("final_text", ""),
            tool_outputs,
            record.get("prompt", ""),
            record.get("tool_call_args", []),
        )
        graded.append((record, result))

    fully_grounded = sum(not result["ungrounded"] for _, result in graded)
    print(f"{os.path.basename(path)}")
    print(f"Grounding: {fully_grounded}/{len(graded)} answers fully grounded")
    for record, result in graded:
        for item in result["ungrounded"]:
            case = record.get("case", "unknown")
            repeat = record.get("repeat")
            repeat_label = f" (repeat {repeat + 1})" if isinstance(repeat, int) else ""
            print(f"  {case}{repeat_label}: {item['value']} — {item['context']}")


def main():
    parser = argparse.ArgumentParser(
        description="Regrade saved evaluation results for numeric grounding offline."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="saved eval_results_*.json files (defaults to matching files in this directory)",
    )
    args = parser.parse_args()
    paths = args.files or sorted(glob.glob("eval_results_*.json"))
    if not paths:
        parser.error("no eval_results_*.json files found")
    for path in paths:
        regrade(path)


if __name__ == "__main__":
    main()
