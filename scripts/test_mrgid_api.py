"""Tests for the MarineRegions helpers against a fake server, no downloads.

Run with `python scripts/test_mrgid_api.py` (no test framework required, like
test_id_patterns.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import mrgid_api as api  # noqa: E402
from mrgid import build as mrgid_build  # noqa: E402

api.RETRY_BACKOFF = 0
api.RATE_LIMITED_WAIT = 0
api.REQUEST_INTERVAL = 0


class Response:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise api.requests.HTTPError(str(self.status_code))


class Server:
    """Answers by the id in the URL, or with the scripted responses in turn."""

    def __init__(self, records=None, script=None):
        self.records = records or {}
        self.script = list(script or [])
        self.requests = 0

    def get(self, url, timeout=None):
        self.requests += 1
        if self.script:
            return self.script.pop(0)
        mrgid = int(url.rstrip("/").rsplit("/", 1)[1])
        record = self.records.get(mrgid)
        return Response(200, record) if record else Response(404, {})


def record(mrgid: int, status: str = "standard") -> dict:
    return {"MRGID": mrgid, "preferredGazetteerName": f"n{mrgid}", "status": status}


def test_rate_limit_is_retried() -> None:
    api._session = Server(script=[Response(429), Response(429), Response(200, record(1))])
    assert api._get_with_retry("https://x/1/").status_code == 200
    assert api._session.requests == 3


def test_rate_limit_gives_up() -> None:
    api._session = Server(script=[Response(429)] * api.RETRIES)
    try:
        api._get_with_retry("https://x/1/")
    except api.requests.HTTPError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("should give up after all retries")


def test_pace_spreads_requests_over_threads() -> None:
    api.REQUEST_INTERVAL, interval = 0.02, api.REQUEST_INTERVAL
    api._next_request = 0.0
    times, lock = [], threading.Lock()

    def request(_) -> None:
        api._pace()
        with lock:
            times.append(time.monotonic())

    try:
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(request, range(10)))
    finally:
        api.REQUEST_INTERVAL = interval
    assert max(times) - min(times) >= 0.15, "10 requests spread over at least 9 intervals"


def test_unused_and_deleted_ids_are_no_records() -> None:
    api._session = Server({1: record(1), 2: record(2, "deleted")})
    with tempfile.TemporaryDirectory() as tmp:
        assert api.fetch_record(1, Path(tmp)).name == "n1"
        assert api.fetch_record(2, Path(tmp)) is None
        assert api.fetch_record(3, Path(tmp)) is None
        requests = api._session.requests
        assert api.fetch_record(3, Path(tmp)) is None, "cached"
        assert api._session.requests == requests


def test_walk_crosses_gaps_and_ends_after_an_empty_block() -> None:
    api.ID_BLOCK = 10
    # the second block has only a deleted record and must end the walk
    api._session = Server({1: record(1), 15: record(15), 21: record(21, "deleted"), 45: record(45)})
    with tempfile.TemporaryDirectory() as tmp:
        records = api.fetch_all_records(Path(tmp), workers=4)
    assert sorted(records) == [1, 15]
    assert api._session.requests == 30


def test_failed_lookups_raise() -> None:
    api._session = Server(script=[Response(429)] * api.RETRIES)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            api.fetch_all_records(Path(tmp), workers=1)
        except api.requests.HTTPError:
            pass
        else:
            raise AssertionError("a failed lookup would drop the record")


def test_a_feature_serves_all_records_pointing_at_it() -> None:
    attrs = {
        "mrgid_1": {"14785": [14785]},
        "mrgid_2": {"17651": [17651], "99": [99]},
        "mrgid_3": {"17652": [17652, 14785]},
        "other": {"x": [1]},
    }
    props = {"mrgid_1": 14785, "mrgid_2": 17651.0, "mrgid_3": 17652, "other": None}
    assert mrgid_build._mrgids_of_feature(props, attrs) == [14785, 17651, 17652]
    assert mrgid_build._mrgids_of_feature({"mrgid_1": 1}, attrs) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print(f"{len(tests)} tests passed")
