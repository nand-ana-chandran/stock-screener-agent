import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

EVAL_PATH = Path(__file__).resolve().parents[1] / "evals" / "eval.py"
SPEC = importlib.util.spec_from_file_location("stock_screener_eval", EVAL_PATH)
eval_module = importlib.util.module_from_spec(SPEC)
with patch.dict(os.environ, {"GOOGLE_API_KEY": "offline-test-placeholder"}):
    SPEC.loader.exec_module(eval_module)

select_cases = eval_module.select_cases

CASES = [
    {"id": "india_nifty"},
    {"id": "india_sector"},
    {"id": "snapshot_nvda"},
]


class TestEvalCaseSelection(unittest.TestCase):
    def test_prefix_selects_only_matching_cases(self):
        selected = select_cases(CASES, prefix="india_")
        self.assertEqual(
            [case["id"] for case in selected],
            ["india_nifty", "india_sector"],
        )

    def test_case_ids_remain_supported(self):
        selected = select_cases(CASES, only_ids=["snapshot_nvda"])
        self.assertEqual([case["id"] for case in selected], ["snapshot_nvda"])

    def test_unknown_prefix_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "No eval cases found"):
            select_cases(CASES, prefix="missing_")

    def test_unknown_case_id_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Unknown case id"):
            select_cases(CASES, only_ids=["not_a_case"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
