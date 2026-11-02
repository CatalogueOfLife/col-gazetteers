"""Tests for the Wikidata SPARQL helper against a fake server, no downloads.

Run with `python scripts/test_wikidata.py` (no test framework required, like
test_id_patterns.py). Exit code 0 on success, an assertion error otherwise.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import wikidata as wd  # noqa: E402

CSV = b"code,name\nGB-ENG,England\n"


class Response:
    def __init__(self, status_code: int, content: bytes = b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise wd.requests.HTTPError(str(self.status_code), response=self)


class Server:
    """Answers with the scripted responses in turn, raising any exception."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = 0

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests += 1
        answer = self.script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def run(*script) -> tuple[Server, Clock, Path, Exception | None]:
    """Run one query against `script`; return the server, clock, cache path and
    the exception raised, if any."""
    wd._session = server = Server(*script)
    wd.time = clock = Clock()
    cache = Path(tempfile.mkdtemp()) / "q.csv"
    try:
        wd.sparql_csv("SELECT 1", cache, role="r", name="n")
    except Exception as e:  # noqa: BLE001
        return server, clock, cache, e
    return server, clock, cache, None


def test_rate_limit_is_retried() -> None:
    server, clock, cache, err = run(Response(429), Response(200, CSV))
    assert err is None, err
    assert server.requests == 2
    assert cache.read_bytes() == CSV
    assert clock.waits == [wd.RETRY_DELAY]


def test_retry_after_is_honoured() -> None:
    server, clock, cache, err = run(Response(429, headers={"Retry-After": "7"}), Response(200, CSV))
    assert err is None, err
    assert clock.waits == [7]


def test_retry_after_is_capped() -> None:
    server, clock, cache, err = run(Response(429, headers={"Retry-After": "86400"}), Response(200, CSV))
    assert err is None, err
    assert clock.waits == [wd.MAX_RETRY_AFTER]


def test_server_errors_and_dropped_connections_are_retried() -> None:
    server, clock, cache, err = run(
        Response(503), wd.requests.ConnectionError("reset"), Response(200, CSV)
    )
    assert err is None, err
    assert server.requests == 3
    assert clock.waits == [wd.RETRY_DELAY, 2 * wd.RETRY_DELAY]


def test_rate_limit_gives_up() -> None:
    server, clock, cache, err = run(*[Response(429)] * wd.ATTEMPTS)
    assert isinstance(err, wd.requests.HTTPError), err
    assert err.response.status_code == 429
    assert server.requests == wd.ATTEMPTS
    assert not cache.exists(), "no cache file from a failed query"


def test_bad_query_is_not_retried() -> None:
    server, clock, cache, err = run(Response(400))
    assert isinstance(err, wd.requests.HTTPError), err
    assert server.requests == 1
    assert clock.waits == []


def test_cache_hit_makes_no_request() -> None:
    wd._session = server = Server()
    cache = Path(tempfile.mkdtemp()) / "q.csv"
    cache.write_bytes(CSV)
    path, record = wd.sparql_csv("SELECT 1", cache, role="r", name="n")
    assert server.requests == 0
    assert path == cache and record.size_bytes == len(CSV)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print(f"{len(tests)} tests passed")
