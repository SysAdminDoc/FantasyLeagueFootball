"""The live server: HTTP round-trips and Server-Sent Events fan-out."""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading

import pytest

from fantasyleague import board, serve


@pytest.fixture
def server():
    with serve.BoardServer(board.load(), host="127.0.0.1", port=0) as s:
        yield s


def _get(s, path):
    c = http.client.HTTPConnection(s.host, s.port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


def _post(s, path, obj):
    c = http.client.HTTPConnection(s.host, s.port, timeout=5)
    c.request("POST", path, body=json.dumps(obj), headers={"Content-Type": "application/json"})
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    return r.status, body


def test_root_serves_live_board(server):
    status, ctype, body = _get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b'"live": true' in body
    assert b"connect-src 'self'" in body


def test_state_round_trip_by_rank_and_name(server):
    status, st = _get(server, "/state")[0], json.loads(_get(server, "/state")[2])
    assert status == 200 and st == {"picks": [], "current_pick": 1}

    status, st = _post(server, "/state", {"pick": {"rank": 3}})
    assert status == 200 and [p["rank"] for p in st["picks"]] == [3]

    status, st = _post(server, "/state", {"pick": {"name": "gibbs"}})
    assert status == 200 and [p["rank"] for p in st["picks"]] == [3, 1]
    assert st["current_pick"] == 3

    # idempotent: picking again does not duplicate
    status, st = _post(server, "/state", {"pick": {"rank": 3}})
    assert [p["rank"] for p in st["picks"]] == [3, 1]

    status, st = _post(server, "/state", {"undo": 3})
    assert [p["rank"] for p in st["picks"]] == [1]

    status, st = _post(server, "/state", {"reset": True})
    assert st["picks"] == []


def _raw_post(s, body, headers, path="/state"):
    """POST without json helpers, so malformed headers/bodies can be exercised."""
    c = http.client.HTTPConnection(s.host, s.port, timeout=5)
    c.request("POST", path, body=body, headers=headers)
    r = c.getresponse()
    payload = r.read()
    c.close()
    return r.status, payload


def test_bad_requests_are_400_not_500(server):
    assert _post(server, "/state", {"pick": {"name": "brown"}})[0] == 400  # ambiguous
    assert _post(server, "/state", {"nonsense": 1})[0] == 400
    assert _post(server, "/state", {"pick": {}})[0] == 400
    # Shapes that used to reach int()/dict.get() and kill the request thread.
    assert _post(server, "/state", {"undo": None})[0] == 400
    assert _post(server, "/state", {"undo": "gibbs"})[0] == 400
    assert _post(server, "/state", {"pick": "gibbs"})[0] == 400
    assert _post(server, "/state", {"pick": None})[0] == 400
    assert _post(server, "/state", [1, 2])[0] == 400
    c = http.client.HTTPConnection(server.host, server.port, timeout=5)
    c.request("POST", "/state", body=b"not json", headers={"Content-Type": "application/json"})
    assert c.getresponse().status == 400
    c.close()
    assert _get(server, "/nope")[0] == 404


def test_undo_accepts_digit_strings_but_not_floats(server):
    _post(server, "/state", {"pick": {"rank": 4}})
    assert _post(server, "/state", {"undo": "4"})[1]["picks"] == []
    assert _post(server, "/state", {"undo": 4.5})[0] == 400


def test_malformed_content_length_is_a_400_not_a_dropped_connection(server):
    status, _ = _raw_post(server, b"", {"Content-Type": "application/json", "Content-Length": "abc"})
    assert status == 400
    # The server must still be answering afterwards.
    assert _get(server, "/state")[0] == 200


def test_oversized_body_is_refused(server):
    blob = json.dumps({"pick": {"name": "x" * (serve.MAX_BODY_BYTES + 10)}})
    status, _ = _raw_post(server, blob, {"Content-Type": "application/json"})
    assert status == 413


def test_writes_require_a_json_content_type(server):
    """The gate that forces a cross-origin fetch into a preflight it cannot pass."""
    status, _ = _raw_post(server, b'{"reset": true}', {"Content-Type": "text/plain"})
    assert status == 415
    status, _ = _raw_post(server, b'{"reset": true}', {})
    assert status == 415
    assert _get(server, "/state")[0] == 200


def test_cross_origin_writes_are_refused(server):
    body = b'{"pick": {"rank": 3}}'
    json_ct = {"Content-Type": "application/json"}
    assert _raw_post(server, body, {**json_ct, "Origin": "https://evil.example"})[0] == 403
    assert _raw_post(server, body, {**json_ct, "Origin": "null"})[0] == 403
    # Its own page is same-origin, and a scripted client sends no Origin at all.
    same = f"http://{server.host}:{server.port}"
    assert _raw_post(server, body, {**json_ct, "Origin": same})[0] == 200
    assert _raw_post(server, b'{"undo": 3}', json_ct)[0] == 200


def test_host_header_must_be_an_address(server):
    """Blunts DNS rebinding: a domain pointed at 127.0.0.1 is refused."""
    json_ct = {"Content-Type": "application/json"}
    assert _raw_post(server, b'{"reset": true}', {**json_ct, "Host": "attacker.example"})[0] == 403
    assert _raw_post(server, b'{"reset": true}', {**json_ct, "Host": "localhost:1"})[0] == 200


def test_source_tag_is_sanitised(server):
    _raw_post(server, b'{"pick": {"rank": 6}}',
              {"Content-Type": "application/json", "X-Source": "<img src=x onerror=alert(1)>"})
    assert server.bus.state()["picks"][0]["source"] == "http"
    _raw_post(server, b'{"pick": {"rank": 7}}',
              {"Content-Type": "application/json", "X-Source": "sleeper"})
    assert server.bus.state()["picks"][1]["source"] == "sleeper"


@pytest.mark.skipif(sys.platform != "win32", reason="SO_REUSEADDR only steals a live port on Windows")
def test_second_server_on_a_busy_port_is_refused(server):
    with pytest.raises(OSError):
        serve.BoardServer(board.load(), host=server.host, port=server.port)


def _read_events(sock_file, count, timeout=5.0):
    """Read *count* SSE events from a file-like socket; returns [(event, data)]."""
    out, event, data = [], None, None
    sock_file._sock.settimeout(timeout) if hasattr(sock_file, "_sock") else None
    while len(out) < count:
        line = sock_file.readline()
        if not line:
            break
        line = line.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line == "" and event is not None:
            out.append((event, data))
            event, data = None, None
    return out


def test_sse_sends_state_then_broadcasts_picks(server):
    sock = socket.create_connection((server.host, server.port), timeout=5)
    sock.sendall(b"GET /events HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
    f = sock.makefile("rb")
    # skip response headers
    while True:
        line = f.readline()
        if line in (b"\r\n", b"\n", b""):
            break
    events = _read_events(f, 1)
    assert events == [("state", {"picks": [], "current_pick": 1})]

    # A pick from another connection reaches the stream.
    got = {}
    def poster():
        got["resp"] = _post(server, "/state", {"pick": {"rank": 2}})
    t = threading.Thread(target=poster)
    t.start()
    t.join(5)
    events = _read_events(f, 1)
    assert events[0][0] == "pick" and events[0][1]["rank"] == 2 and events[0][1]["source"] == "http"

    _post(server, "/state", {"undo": 2})
    assert _read_events(f, 1)[0] == ("undo", {"rank": 2, "source": "http"})
    _post(server, "/state", {"reset": True})
    assert _read_events(f, 1)[0][0] == "reset"
    f.close()
    sock.close()


def test_bus_source_tag_and_client_count(server):
    assert server.bus.client_count() == 0
    server.bus.pick(5, source="sleeper")
    assert server.bus.state()["picks"][0]["source"] == "sleeper"
    assert server.bus.pick(5, source="sleeper") is False


def test_urls_and_context_manager():
    with serve.BoardServer(board.load(), port=0) as s:
        assert s.urls()[0].startswith("http://localhost:")
        assert s.port > 0
