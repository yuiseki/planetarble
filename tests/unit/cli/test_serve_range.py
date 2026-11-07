"""HTTP Range support of the `planetarble serve` tile server.

pmtiles.js reads archives with Range requests, so these behaviours decide
whether the viewer can load a planet at all.
"""

from __future__ import annotations

import functools
import http.client
import http.server
import socket
import threading
from pathlib import Path

import pytest

from planetarble.cli.serve import _RangeRequestHandler

BODY = bytes(range(10))  # 10 bytes: 00 01 .. 09


class _QuietHandler(_RangeRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature from BaseHTTPRequestHandler
        pass


@pytest.fixture
def server(tmp_path: Path):
    (tmp_path / "planet.pmtiles").write_bytes(BODY)
    handler = functools.partial(_QuietHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port: int, method: str = "GET", range_header: str | None = None, path: str = "/planet.pmtiles"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Range": range_header} if range_header else {}
    conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_full_get_advertises_ranges(server: int) -> None:
    resp, body = _request(server)
    assert resp.status == 200
    assert resp.getheader("Accept-Ranges") == "bytes"
    assert body == BODY


@pytest.mark.parametrize(
    ("header", "content_range", "expected"),
    [
        ("bytes=0-3", "bytes 0-3/10", BODY[0:4]),
        ("bytes=5-", "bytes 5-9/10", BODY[5:]),
        ("bytes=-4", "bytes 6-9/10", BODY[6:]),
        ("bytes=8-100", "bytes 8-9/10", BODY[8:]),
        ("bytes=-100", "bytes 0-9/10", BODY),
        ("bytes=2-3,6-7", "bytes 2-3/10", BODY[2:4]),  # only the first range is served
    ],
)
def test_partial_content(server: int, header: str, content_range: str, expected: bytes) -> None:
    resp, body = _request(server, range_header=header)
    assert resp.status == 206
    assert resp.getheader("Content-Range") == content_range
    assert resp.getheader("Content-Length") == str(len(expected))
    assert body == expected


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=5-2", "bytes=-0", "bytes=abc-", "bytes=1-x", "items=0-1"])
def test_unsatisfiable_or_malformed_range_is_416(server: int, header: str) -> None:
    resp, _ = _request(server, range_header=header)
    assert resp.status == 416


def test_head_with_range_sends_headers_only(server: int) -> None:
    # http.client never reads a body for HEAD, so look at the raw bytes.
    with socket.create_connection(("127.0.0.1", server), timeout=5) as sock:
        sock.sendall(b"HEAD /planet.pmtiles HTTP/1.0\r\nRange: bytes=0-3\r\n\r\n")
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 206")
    assert b"Content-Length: 4" in head
    assert body == b""


def test_missing_file_is_404(server: int) -> None:
    resp, _ = _request(server, path="/nope.pmtiles", range_header="bytes=0-1")
    assert resp.status == 404
