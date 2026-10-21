import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime

import importlib
from stock_screener.grounding import check_grounding

CASES_MODULE = os.getenv("CASES_MODULE", "eval_cases")
# Make eval_cases / eval_holdout importable by name from within this directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CASES = importlib.import_module(CASES_MODULE).CASES
from stock_screener.flow import graph, GUARD_STATS, TOOL_GUARD

MODEL = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")
GUARD_LABEL = "on" if TOOL_GUARD else "off"
SLEEP_SECONDS = 6            # pause between turns (raise it if you still hit API errors)
RETRY_WAITS = [20, 40, 80]   # seconds to wait before each retry of a failed case
VALID_TOOLS = {"simple_screener", "custom_screener", "company_snapshot", "india_screener"}

# Tool defaults, so an omitted argument is judged as its default value
DEFAULTS = {
    "custom_screener": {
        "min_market_cap_millions": 0, "max_market_cap_millions": 0, "sector": "",
        "sort_by": "market_cap", "ascending": False, "min_volume": None, "offset": 0,
    },
    "simple_screener": {"offset": 0},
}


def is_transient(e):
    """429 rate limits and 503 'high demand' are Google-side problems, not agent failures."""
    text = f"{type(e).__name__} {e}".lower()
    return any(s in text for s in ("429", "503", "resource_exhausted", "unavailable",
                                   "high demand", "ratelimit", "overloaded"))


def run_turn(prompt, thread_id):
    result = graph.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    msgs = result["messages"]
    last_human = max(i for i, m in enumerate(msgs) if m.type == "human")
    turn_msgs = msgs[last_human + 1:]
    last_message = turn_msgs[-1] if turn_msgs else None
    return {
        "actual_calls": [
            {"name": c["name"], "args": c["args"]}
            for m in turn_msgs if m.type == "ai"
            for c in (m.tool_calls or [])
        ],
        "final_text": last_message.text if last_message else "",
        "tool_outputs": [m.content for m in turn_msgs if m.type == "tool"],
    }


def arg_ok(expected, actual):
    if callable(expected):
        try:
            return bool(expected(actual))
        except Exception:
            return False
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return expected == actual


def check_turn(expected_calls, actual_calls, tool_call_limits=None):
    """Returns (tools_ok, args_ok, reasons). Tools are compared as a multiset (order ignored).
    Strict on purpose: an invalid tool name counts as a failure even if the agent recovered."""
    reasons = []
    invalid = sorted({c["name"] for c in actual_calls if c["name"] not in VALID_TOOLS})
    if invalid:
        reasons.append(f"INVALID TOOL NAME emitted: {invalid}")

    tool_call_limits = tool_call_limits or {}
    exp_names = Counter(c["tool"] for c in expected_calls)
    act_names = Counter(c["name"] for c in actual_calls)
    constrained_names = set(tool_call_limits)
    exact_expected = Counter({
        name: count for name, count in exp_names.items()
        if name not in constrained_names
    })
    exact_actual = Counter({
        name: count for name, count in act_names.items()
        if name not in constrained_names
    })
    if exact_expected != exact_actual:
        reasons.append(
            f"tools expected {dict(exact_expected)}, got {dict(exact_actual)}"
        )
    for name, bounds in tool_call_limits.items():
        count = act_names[name]
        minimum = bounds.get("min", 0)
        maximum = bounds.get("max")
        if count < minimum or (maximum is not None and count > maximum):
            reasons.append(
                f"{name}: expected between {minimum} and {maximum} calls, got {count}"
            )
    if reasons:
        return False, False, reasons

    used = set()
    for exp in expected_calls:
        matched = False
        for i, act in enumerate(actual_calls):
            if i in used or act["name"] != exp["tool"]:
                continue
            merged = {**DEFAULTS.get(act["name"], {}), **act["args"]}
            args_match = all(
                arg_ok(value, merged.get(key))
                for key, value in exp.get("args", {}).items()
            )
            alternatives = exp.get("any_args", [])
            if alternatives:
                args_match = args_match and any(
                    all(arg_ok(value, merged.get(key)) for key, value in option.items())
                    for option in alternatives
                )
            if args_match:
                used.add(i)
                matched = True
                break
        if not matched:
            candidates = [a["args"] for a in actual_calls if a["name"] == exp["tool"]]
            constraint = exp.get("failure_message")
            if constraint is None:
                constraint = ", ".join(
                    f"{key}={value if not callable(value) else 'required constraint'}"
                    for key, value in exp.get("args", {}).items()
                )
            reasons.append(
                f"{exp['tool']}: unmet constraint ({constraint}); actual args: {candidates}"
            )
    return True, not reasons, reasons


def check_answer(case, answer, tool_outputs=None):
    reasons = []
    for pattern in case.get("answer_must_include", []):
        if callable(pattern):
            pattern = pattern()
        if re.search(pattern, answer, re.IGNORECASE | re.MULTILINE) is None:
            reasons.append(f"answer did not match required pattern: {pattern}")
    for pattern in case.get("answer_must_not_match", []):
        if callable(pattern):
            pattern = pattern()
        if re.search(pattern, answer, re.IGNORECASE | re.MULTILINE):
            reasons.append(f"answer matched forbidden pattern: {pattern}")
    for check in case.get("answer_checks", []):
        reasons.extend(check(answer, tool_outputs or []))
    return reasons


def run_case(case, rep):
    """Runs every turn of a case on a fresh thread. If a transient API error hits,
    waits and restarts the WHOLE case, so multi-turn memory stays consistent."""
    for attempt in range(len(RETRY_WAITS) + 1):
        thread_id = str(uuid.uuid4())
        records, transient = [], False
        for t_index, turn in enumerate(case["turns"]):
            rec = {"case": case["id"], "turn": t_index, "repeat": rep,
                   "prompt": turn["prompt"], "attempt": attempt + 1, "guard": GUARD_LABEL}
            try:
                before = dict(GUARD_STATS)
                turn_result = run_turn(turn["prompt"], thread_id)
                actual = turn_result["actual_calls"]
                guard_delta = {k: GUARD_STATS[k] - before[k] for k in GUARD_STATS}
                tools_ok, args_ok, reasons = check_turn(
                    turn["calls"], actual, case.get("tool_call_limits")
                )
                answer_reasons = check_answer(
                    case,
                    turn_result["final_text"],
                    turn_result["tool_outputs"],
                )
                reasons.extend(answer_reasons)
                tool_call_args = [call["args"] for call in actual]
                rec.update(status="pass" if tools_ok and args_ok and not reasons else "fail",
                           actual_calls=actual, tools_ok=tools_ok, args_ok=args_ok,
                           reasons=reasons, guard_activity=guard_delta,
                           final_text=turn_result["final_text"],
                           tool_outputs=turn_result["tool_outputs"],
                           tool_call_args=tool_call_args)
                rec["grounding"] = (
                    check_grounding(rec["final_text"], rec["tool_outputs"], rec["prompt"],
                                    rec["tool_call_args"])
                    if rec["tool_outputs"] else None
                )
            except Exception as e:
                rec.update(status="error", actual_calls=[], tools_ok=False, args_ok=False,
                           reasons=[f"{type(e).__name__}: {str(e)[:500]}"], guard_activity={},
                           final_text="", tool_outputs=[], tool_call_args=[], grounding=None)
                transient = is_transient(e)
            records.append(rec)
            if rec["status"] == "error":
                break  # later turns depend on this one's memory
            time.sleep(SLEEP_SECONDS)
        if transient and attempt < len(RETRY_WAITS):
            wait = RETRY_WAITS[attempt]
            print(f"   API error on '{case['id']}'; waiting {wait}s, then retrying the whole case...")
            time.sleep(wait)
            continue
        return records
    return records


def save(records, path):
    with open(path, "w") as f:
        json.dump(records, f, indent=2, default=str)


def summarize(records, repeats):
    valid = [r for r in records if r["status"] != "error"]
    errors = [r for r in records if r["status"] == "error"]
    invalid_name_turns = [r for r in valid if any("INVALID TOOL NAME" in x for x in r["reasons"])]
    repaired_turns = [r for r in valid if r["guard_activity"].get("repaired", 0) > 0]
    duplicate_turns = [r for r in valid if r["guard_activity"].get("dropped_duplicates", 0) > 0]
    grounded = [r for r in valid if r.get("grounding") is not None]
    n = len(valid)

    print("\n" + "=" * 60)
    print(f"Model: {MODEL} | tool guard: {GUARD_LABEL}")
    print(f"Turns attempted:          {len(records)}")
    print(f"Turns not evaluated:      {len(errors)} (API errors, excluded below)")
    if n:
        tools_correct = sum(r["tools_ok"] for r in valid)
        fully_correct = sum(r["status"] == "pass" for r in valid)
        print(f"Tool selection accuracy:  {tools_correct}/{n} = {100 * tools_correct / n:.1f}%")
        print(f"Strict pass rate:         {fully_correct}/{n} = {100 * fully_correct / n:.1f}%")
        print(f"Invalid tool name turns (after any guard): {len(invalid_name_turns)}/{n}")
        print(f"Turns where guard repaired a tool name:    {len(repaired_turns)}/{n}")
        print(f"Turns where guard dropped a duplicate:     {len(duplicate_turns)}/{n}")
        for rep in range(repeats):
            rv = [r for r in valid if r["repeat"] == rep]
            if rv:
                ok = sum(r["status"] == "pass" for r in rv)
                print(f"  run {rep + 1}: {ok}/{len(rv)} strict passes")
    fully_grounded = sum(not r["grounding"]["ungrounded"] for r in grounded)
    print(f"Grounding: {fully_grounded}/{len(grounded)} answers fully grounded")
    for record in grounded:
        for item in record["grounding"]["ungrounded"]:
            print(f"  {record['case']} (repeat {record['repeat'] + 1}): "
                  f"{item['value']} — {item['context']}")
    if errors:
        print("WARNING: the numbers above cover only completed turns. Report the error count with them.")


def select_cases(case_list, only_ids=None, prefix=None):
    known = {case["id"] for case in case_list}
    only = set(only_ids or [])
    unknown = only - known
    if unknown:
        raise ValueError(f"Unknown case id(s): {sorted(unknown)}")
    if prefix and only:
        raise ValueError("Use either case ids or --prefix, not both.")
    selected = [
        case for case in case_list
        if (not only or case["id"] in only)
        and (not prefix or case["id"].startswith(prefix))
    ]
    if prefix and not selected:
        raise ValueError(f"No eval cases found with prefix {prefix!r}.")
    return selected


def main():
    # Usage: python evals/eval.py [repeats] [case_id ...] [--prefix PREFIX]
    #   python evals/eval.py 3 --prefix india_ -> all India cases, 3 repeats
    import argparse

    parser = argparse.ArgumentParser(description="Run stock screener evaluation cases.")
    parser.add_argument("repeats", nargs="?", type=int, default=1)
    parser.add_argument("case_ids", nargs="*")
    parser.add_argument("--prefix", help="Run only cases whose id starts with this prefix.")
    args = parser.parse_args()

    try:
        cases = select_cases(CASES, args.case_ids, args.prefix)
    except ValueError as error:
        parser.error(str(error))
    subset = bool(args.case_ids or args.prefix)
    tag = "_subset" if subset else ""
    out = (f"eval_results_{MODEL}_{CASES_MODULE}_guard{GUARD_LABEL}{tag}_"
           f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    print(f"Model: {MODEL} | tool guard: {GUARD_LABEL} | cases: {len(cases)} | repeats: {args.repeats}\n")

    records, error_cases_in_a_row, stop = [], 0, False
    try:
        for rep in range(args.repeats):
            for case in cases:
                case_records = run_case(case, rep)
                for rec in case_records:
                    records.append(rec)
                    print(f"[{rec['status'].upper()}] {rec['case']} (turn {rec['turn'] + 1})")
                    for r in rec["reasons"]:
                        print(f"        {r}")
                save(records, out)  # saved after every case, so nothing is lost
                error_cases_in_a_row = error_cases_in_a_row + 1 if case_records[-1]["status"] == "error" else 0
                if error_cases_in_a_row >= 3:
                    print("\nSTOPPING: 3 cases in a row failed with API errors (probably a daily quota). "
                          "Partial results are saved.")
                    stop = True
                    break
            if stop:
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Partial results are saved.")

    summarize(records, args.repeats)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
