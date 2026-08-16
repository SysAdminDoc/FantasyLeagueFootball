"""Serve the board over HTTP with Server-Sent Events so every open tab — a laptop,
a phone on the LAN — shows the same crossed-off state, and so sync sources can
push picks in without a browser extension.

Endpoints
    GET  /          the rendered board (payload has live: true)
    GET  /events    SSE stream: `state` on connect, then `pick` / `undo` / `reset`
    GET  /state     JSON {picks: [{rank, ts, source}], current_pick}
    POST /state     {"pick": {"rank": 3}} | {"pick": {"name": "gibbs"}} |
                    {"undo": 3} | {"reset": true}   → applied and broadcast

Standard library only: ThreadingHTTPServer, one daemon thread per SSE client,
a queue per client for fan-out.

Writes are same-origin only: `POST /state` requires a JSON content type and, when
a browser supplies one, an `Origin` matching `Host`. That combination means a page
on another site cannot reach this endpoint without a CORS preflight, which fails
because no `Access-Control-Allow-*` header is ever sent. Anyone who can reach the
port directly (curl, the LAN when bound to 0.0.0.0) can still drive the board —
that is the point of the endpoint, and the README says so.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self
from urllib.parse import urlsplit

from . import board as board_mod
from . import render as render_mod
from .models import Dataset

KEEPALIVE_SECONDS = 15
MAX_BODY_BYTES = 64 * 1024
SOURCE_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")


@dataclass
class Bus:
    """Authoritative pick log for a live session plus SSE fan-out."""

    data: Dataset
    picks: list[dict] = field(default_factory=list)
    _clients: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- state -------------------------------------------------------------

    def state(self) -> dict:
        with self._lock:
            return {"picks": list(self.picks), "current_pick": len(self.picks) + 1}

    def pick(self, rank: int, source: str = "manual") -> bool:
        """Cross *rank* off. Returns False if it was already gone (idempotent)."""
        with self._lock:
            if any(p["rank"] == rank for p in self.picks):
                return False
            entry = {"rank": rank, "ts": int(time.time() * 1000), "source": source}
            self.picks.append(entry)
        self._broadcast("pick", entry)
        return True

    def undo(self, rank: int, source: str = "manual") -> bool:
        with self._lock:
            before = len(self.picks)
            self.picks = [p for p in self.picks if p["rank"] != rank]
            changed = len(self.picks) != before
        if changed:
            self._broadcast("undo", {"rank": rank, "source": source})
        return changed

    def reset(self, source: str = "manual") -> None:
        with self._lock:
            self.picks = []
        self._broadcast("reset", {"source": source})

    def apply(self, body: dict, source: str = "manual") -> dict:
        """Apply a POST /state body. Raises ValueError on a bad request."""
        if "reset" in body:
            self.reset(source)
        elif "undo" in body:
            self.undo(_as_rank(body["undo"], "undo"), source)
        elif "pick" in body:
            spec = body["pick"]
            if not isinstance(spec, dict):
                # ValueError, not TypeError: the handler turns it into a 400.
                raise ValueError('pick must be an object, e.g. {"pick": {"rank": 3}}')
            token = spec.get("rank", spec.get("name"))
            if token is None:
                raise ValueError("pick needs a rank or a name")
            self.pick(board_mod.resolve(self.data, token).rank, source)
        else:
            raise ValueError("expected one of: pick, undo, reset")
        return self.state()

    # ---- fan-out -----------------------------------------------------------

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _broadcast(self, event: str, payload: dict) -> None:
        with self._lock:
            targets = list(self._clients)
        for q in targets:
            q.put((event, payload))


def _as_rank(value: object, field_name: str) -> int:
    """An integer rank from JSON, rejecting None/floats/strings that aren't digits."""
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        raise ValueError(f"{field_name} must be an integer rank, got {value!r}")
    return value


def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def _is_local_host_header(host: str) -> bool:
    """True when Host names an address rather than a domain.

    A DNS-rebinding attack points a domain it controls at 127.0.0.1; the browser
    then sends that domain in Host. Requiring a literal IP (or localhost) means
    only requests that already knew the address get through.
    """
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.strip("[]") or host
    if name.lower() in ("localhost", ""):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def make_handler(bus: Bus, html: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FantasyLeagueFootball"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default; the CLI prints what matters
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/state":
                self._json(200, bus.state())
            elif path == "/events":
                self._events()
            else:
                self._json(404, {"error": "not found"})

        # ---- write guards ---------------------------------------------------

        def _same_origin(self) -> bool:
            """True unless a browser sent an Origin that isn't this server.

            No Origin at all means a non-browser client (curl, a sync script) —
            those are allowed, since reaching the socket is the only credential
            this server has ever had.
            """
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            host = (self.headers.get("Host") or "").strip()
            return bool(host) and urlsplit(origin).netloc == host

        def _body(self) -> bytes | None:
            """Request body, or None after sending the right error response."""
            raw_len = self.headers.get("Content-Length")
            try:
                length = int(raw_len) if raw_len is not None else 0
            except ValueError:
                self._json(400, {"error": f"Content-Length is not a number: {raw_len!r}"})
                return None
            if length < 0:
                self._json(400, {"error": "Content-Length must not be negative"})
                return None
            if length > MAX_BODY_BYTES:
                self._json(413, {"error": f"body larger than {MAX_BODY_BYTES} bytes"})
                return None
            return self.rfile.read(length) if length else b"{}"

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/state":
                self._json(404, {"error": "not found"})
                return
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                # Rejecting other types is what forces a cross-origin fetch into a
                # preflight, which fails: no Access-Control-Allow-* is ever sent.
                self._json(415, {"error": "Content-Type must be application/json"})
                return
            if not self._same_origin():
                self._json(403, {"error": "cross-origin writes are refused"})
                return
            if not _is_local_host_header(self.headers.get("Host") or ""):
                self._json(403, {"error": "Host must be an address, not a domain name"})
                return
            raw = self._body()
            if raw is None:
                return
            source = self.headers.get("X-Source", "http")
            if not SOURCE_RE.fullmatch(source or ""):
                source = "http"
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    # ValueError, not TypeError: the handler turns it into a 400.
                    raise ValueError("body must be a JSON object")  # noqa: TRY004
                self._json(200, bus.apply(body, source=source))
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = bus.subscribe()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.write(_sse("state", bus.state()))
                self.wfile.flush()
                while True:
                    try:
                        event, payload = q.get(timeout=KEEPALIVE_SECONDS)
                        self.wfile.write(_sse(event, payload))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                bus.unsubscribe(q)

    return Handler


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that fails loudly on a busy port and quietly on hangups."""

    # SO_REUSEADDR means "reuse a TIME_WAIT port" on POSIX but "steal a live one"
    # on Windows: with it on, a second `serve` binds the same port, reports success
    # and never sees a request. Off there, the second bind raises instead.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        # A closed tab aborts its SSE stream; that is normal, and a traceback per
        # reload would bury the pick log the CLI prints during a draft.
        if isinstance(exc, BrokenPipeError | ConnectionResetError | ConnectionAbortedError):
            return
        super().handle_error(request, client_address)


class BoardServer:
    """Owns the HTTP server thread and the Bus. Use as a context manager in tests."""

    def __init__(
        self,
        data: Dataset,
        host: str = "127.0.0.1",
        port: int = 8765,
        title: str | None = None,
        league: str | None = None,
        teams: int | None = None,
        slot: int | None = None,
    ):
        self.data = data
        self.bus = Bus(data)
        html = render_mod.render(data, title=title, league=league, teams=teams, slot=slot, live=True)
        self.httpd = _Server((host, port), make_handler(self.bus, html.encode("utf-8")))
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self.httpd.server_address[0]

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def urls(self) -> list[str]:
        out = [f"http://{'localhost' if self.host in ('127.0.0.1', '0.0.0.0') else self.host}:{self.port}/"]
        if self.host == "0.0.0.0":
            for ip in _lan_ips():
                out.append(f"http://{ip}:{self.port}/")
        return out

    def start(self) -> Self:
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="fantasyleague-serve", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips
