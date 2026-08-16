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
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import board as board_mod
from . import render as render_mod
from .models import Dataset

KEEPALIVE_SECONDS = 15


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
            self.undo(int(body["undo"]), source)
        elif "pick" in body:
            spec = body["pick"] or {}
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


def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


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

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/state":
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                self._json(200, bus.apply(body, source=self.headers.get("X-Source", "http")))
            except (ValueError, json.JSONDecodeError) as exc:
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
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self.bus, html.encode("utf-8")))
        self.httpd.daemon_threads = True
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

    def start(self) -> BoardServer:
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="fantasyleague-serve", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> BoardServer:
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
