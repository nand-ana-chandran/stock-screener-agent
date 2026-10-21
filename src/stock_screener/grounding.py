import json
import re


NUMBER_PATTERN = re.compile(
    r"(?<![\w.])(?:₹|\$)?"
    r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)"
    r"(?:\.\d+)?"
    r"(?:[eE][+-]?\d+)?"
    r"\s*(?:%|(?i:lakh\s+crore|crore|K|M|B|T|million|billion|trillion))?"
    r"(?!\w)",
    re.IGNORECASE,
)
SUFFIX_MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
    "t": 1_000_000_000_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
    "crore": 10_000_000,
    "lakh crore": 1_000_000_000_000,
}
TIME_HYPHEN_PATTERN = re.compile(r"-(?:week|day|month|year)s?\b", re.IGNORECASE)
RANK_RANGE_PATTERN = re.compile(
    r"\b(?:ranks?|positions?)\s+(\d+)\s*(?:to|[-–])\s*(\d+)\b",
    re.IGNORECASE,
)


def _extract_numbers(text, ignored_spans=()):
    rank_range_spans = {
        span
        for range_match in RANK_RANGE_PATTERN.finditer(text)
        for span in (range_match.span(1), range_match.span(2))
    }
    for match in NUMBER_PATTERN.finditer(text):
        token = match.group().strip()
        value_text = token.replace("$", "").replace("₹", "").replace(",", "")
        suffix_match = re.search(
            r"\s*(lakh\s+crore|crore|million|billion|trillion|[KMBT])$",
            value_text,
            re.IGNORECASE,
        )
        if suffix_match:
            suffix = re.sub(r"\s+", " ", suffix_match.group(1).lower())
            value_text = value_text[:suffix_match.start()].strip()
            multiplier = SUFFIX_MULTIPLIERS[suffix]
        else:
            multiplier = 1
        value_text = value_text.rstrip("%").strip()
        value = float(value_text) * multiplier
        has_marker = "$" in token or "₹" in token or "%" in token
        has_decimal = "." in value_text
        bare_small_integer = (
            not has_marker
            and not has_decimal
            and not suffix_match
            and value.is_integer()
            and 0 <= value <= 10
        )
        year = value.is_integer() and 1990 <= value <= 2100
        time_hyphen_unit = TIME_HYPHEN_PATTERN.match(text[match.end():]) is not None
        part_of_company_name = any(
            match.start() < end and start < match.end()
            for start, end in ignored_spans
        )
        rank_range_number = (match.start(), match.end()) in rank_range_spans
        line_start = text.rfind("\n", 0, match.start()) + 1
        ordered_list_marker = (
            not text[line_start:match.start()].strip()
            and re.match(r"[.)]\s+", text[match.end():]) is not None
        )
        yield {
            "token": token,
            "value": value,
            "start": match.start(),
            "end": match.end(),
            "ignored": (
                bare_small_integer
                or year
                or time_hyphen_unit
                or ordered_list_marker
                or part_of_company_name
                or rank_range_number
            ),
        }


def _numeric_company_names(value, key=None):
    names = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            names.extend(_numeric_company_names(child, child_key))
    elif isinstance(value, list):
        for child in value:
            names.extend(_numeric_company_names(child, key))
    elif (
        isinstance(value, str)
        and key
        and key.casefold() in {"name", "company_name", "longname", "shortname"}
        and re.search(r"\d", value)
    ):
        names.append(value)
    return names


def _company_name_number_spans(text, tool_outputs):
    names = set()
    for output in tool_outputs:
        try:
            parsed = json.loads(_as_text(output))
        except (TypeError, ValueError):
            continue
        names.update(_numeric_company_names(parsed))

    spans = []
    for name in names:
        words = name.split()
        if not words:
            continue
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)",
            re.IGNORECASE,
        )
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _as_text(value):
    if isinstance(value, str):
        return value
    return str(value)


def _is_grounded(value, allowed_values):
    return any(
        abs(value - allowed) <= 0.011
        or abs(value - allowed) <= abs(allowed) * 0.005
        for allowed in allowed_values
    )


def check_grounding(final_text, tool_outputs, prompt, tool_call_args=None):
    answer_numbers = list(
        _extract_numbers(final_text, _company_name_number_spans(final_text, tool_outputs))
    )
    allowed_values = [
        number["value"]
        for output in tool_outputs
        for number in _extract_numbers(_as_text(output))
    ]
    allowed_values.extend(number["value"] for number in _extract_numbers(prompt))
    for call_args in tool_call_args or []:
        for key, value in call_args.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            allowed_values.append(float(value))
            if key.endswith("_millions"):
                allowed_values.append(float(value) * 1_000_000)

    ungrounded = []
    for number in answer_numbers:
        if number["ignored"] or _is_grounded(number["value"], allowed_values):
            continue
        start = max(0, number["start"] - 20)
        end = min(len(final_text), number["end"] + 20)
        ungrounded.append({
            "value": number["value"],
            "text": number["token"],
            "context": final_text[start:end].replace("\n", " ").strip(),
        })

    return {
        "numbers_in_answer": [number["token"] for number in answer_numbers],
        "ungrounded": ungrounded,
    }
