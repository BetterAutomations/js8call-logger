"""Minimal client for the JS8Call JSON API.

The API is line-delimited JSON over TCP (default 127.0.0.1:2442). It is
asynchronous: JS8Call pushes RX.* events whenever it decodes something, and
answers a request with a frame carrying the same _ID. Requests are therefore
matched by id, and anything unsolicited is just an event.

Two facts learned the hard way:
  * `AcceptTCPRequests` defaults to false in JS8Call's own settings; with it
    off the socket accepts connections, reads commands and answers nothing.
    engine.write_ini() sets it, and connect() says so plainly instead of
    hanging.
  * The port is 2442, while docs and several blog posts say 2242 - that is the
    UDP port. Both exist; we use TCP.
"""

from __future__ import annotations

import itertools
import json
import select
import socket


class ApiError(Exception):
    pass


class Js8Api:
    def __init__(self, host: str = "127.0.0.1", port: int = 2442):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._buf = b""
        self._ids = itertools.count(1)

    # -- connection ---------------------------------------------------------
    def connect(self, timeout: float = 5.0) -> None:
        self.close()
        s = socket.create_connection((self.host, self.port), timeout)
        s.setblocking(False)
        self.sock = s
        self._buf = b""

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self._buf = b""

    @property
    def connected(self) -> bool:
        return self.sock is not None

    # -- io -----------------------------------------------------------------
    def send(self, typ: str, value: str = "", params: dict | None = None) -> int:
        if not self.sock:
            raise ApiError("not connected")
        mid = next(self._ids)
        frame = {"params": dict(params or {}), "type": typ, "value": value}
        frame["params"].setdefault("_ID", mid)
        data = (json.dumps(frame) + "\n").encode()
        try:
            self.sock.setblocking(True)
            self.sock.sendall(data)
        except OSError as exc:
            self.close()
            raise ApiError(f"send failed: {exc}") from exc
        finally:
            if self.sock:
                self.sock.setblocking(False)
        return mid

    def read(self, timeout: float = 0.5) -> list[dict]:
        """Return every complete frame available within `timeout`."""
        if not self.sock:
            raise ApiError("not connected")
        out: list[dict] = []
        try:
            ready, _, _ = select.select([self.sock], [], [], timeout)
        except (OSError, ValueError):
            self.close()
            raise ApiError("socket closed")
        if ready:
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                return out
            except OSError as exc:
                self.close()
                raise ApiError(f"recv failed: {exc}") from exc
            if not chunk:
                self.close()
                raise ApiError("JS8Call closed the connection")
            self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # -- convenience --------------------------------------------------------
    def request(self, typ: str, params: dict | None = None,
                expect: str | None = None, timeout: float = 3.0) -> dict | None:
        mid = self.send(typ, "", params)
        deadline = timeout
        while deadline > 0:
            for frame in self.read(0.2):
                fp = frame.get("params") or {}
                if fp.get("_ID") == mid or (expect and frame.get("type") == expect):
                    return frame
            deadline -= 0.2
        return None

    def version(self) -> str:
        f = self.request("STATION.VERSION", expect="STATION.VERSION")
        return str((f or {}).get("params", {}).get("VERSION", ""))

    def callsign(self) -> str:
        f = self.request("STATION.GET_CALLSIGN", expect="STATION.CALLSIGN")
        return str(f.get("value", "")) if f else ""

    def get_freq(self) -> dict:
        f = self.request("RIG.GET_FREQ", expect="RIG.FREQ")
        return (f or {}).get("params", {})

    def set_freq(self, dial_hz: int, offset_hz: int = 1500) -> dict:
        f = self.request("RIG.SET_FREQ", {"DIAL": int(dial_hz), "OFFSET": int(offset_hz)},
                         expect="STATION.STATUS")
        return (f or {}).get("params", {})

    def speed(self) -> int | None:
        f = self.request("MODE.GET_SPEED", expect="MODE.SPEED")
        try:
            return int((f or {}).get("params", {}).get("SPEED"))
        except (TypeError, ValueError):
            return None
