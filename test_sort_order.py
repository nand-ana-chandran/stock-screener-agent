import os
import tempfile
from unittest.mock import patch

from tool import simple_screener


previous_directory = os.getcwd()
with tempfile.TemporaryDirectory() as temp_directory:
    try:
        os.chdir(temp_directory)
        with patch("tool.yf.screen", return_value={"quotes": []}) as screen:
            simple_screener.invoke({"screen_type": "most_actives"})
    finally:
        os.chdir(previous_directory)

assert screen.call_args.args[0] is not None
assert screen.call_args.kwargs == {
    "sortField": "dayvolume",
    "sortAsc": False,
    "size": 5,
    "offset": 0,
}
print("Most-actives requests descending dayvolume sorting.")
