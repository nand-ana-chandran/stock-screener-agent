import json
import unittest

from stock_screener.india_data import DEFAULT_SNAPSHOT_PATH

SNAPSHOT_PATH = DEFAULT_SNAPSHOT_PATH


class TestIndiaSnapshotRepositoryData(unittest.TestCase):
    def test_repository_snapshot_is_valid_and_has_at_least_400_ok_tickers(self):
        self.assertTrue(
            SNAPSHOT_PATH.is_file(),
            f"Required repository snapshot is missing: {SNAPSHOT_PATH}",
        )

        try:
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.fail(
                f"Repository snapshot must be readable valid JSON "
                f"({SNAPSHOT_PATH}): {exc}"
            )

        self.assertIsInstance(
            snapshot,
            dict,
            f"Repository snapshot must contain a JSON object: {SNAPSHOT_PATH}",
        )
        tickers = snapshot.get("tickers")
        self.assertIsInstance(
            tickers,
            dict,
            f"Repository snapshot must contain a 'tickers' object: {SNAPSHOT_PATH}",
        )
        ok_count = sum(
            isinstance(record, dict) and record.get("status") == "ok"
            for record in tickers.values()
        )
        self.assertGreaterEqual(
            ok_count,
            400,
            f"Repository snapshot must have at least 400 tickers with status "
            f"'ok'; found {ok_count}: {SNAPSHOT_PATH}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
