"""Turning raw JS8Call API events into log records.

Two things here are worth understanding before changing them.

1. One transmission arrives twice. JS8Call emits RX.ACTIVITY for every decode
   and RX.DIRECTED for every decode it could parse into a message, both
   carrying the same UTC millisecond. The directed one has FROM/TO/CMD, so it
   wins; the activity twin is dropped. Because they arrive a fraction of a
   second apart and in either order, records are held for `dedupe_grace_s`
   before being written.

2. The 150 character limit applies to the rendered line, prefix included.
   Wrapping on raw text overshoots badly (that bug produced 188 character
   lines in morse-logger). Continuation lines carry the same timestamp and a
   "..." marker so a reader never mistakes them for a new transmission, and a
   token that cannot fit is moved whole to the next line, only truncated when
   it is longer than a whole line.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

from .coherence import coherence

HEARTBEAT_RE = re.compile(r"(^|\s)@?HB(\s|$)")
CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}$")


def _ms_to_local(ms, tz_offset_s: float = 0.0) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0 + tz_offset_s)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def is_heartbeat(cmd: str, dst: str, text: str) -> bool:
    """Heartbeats are the bulk of JS8 traffic: '@HB HEARTBEAT', 'HB FN42'."""
    if cmd and "HEARTBEAT" in cmd.upper():
        return True
    if dst and dst.upper().rstrip() == "@HB":
        return True
    return bool(HEARTBEAT_RE.search(text or ""))


def _split_activity(value: str) -> tuple[str, str]:
    """RX.ACTIVITY's value is the rendered line: 'K1ABC: some text'."""
    v = (value or "").strip()
    if ": " in v:
        who, rest = v.split(": ", 1)
        if CALL_RE.match(who.strip()):
            return who.strip(), rest.strip()
    return "", v


def parse_event(ev: dict, tz_offset_s: float = 0.0) -> dict | None:
    """Map one API frame to a record, or None when it is not a reception."""
    typ = str(ev.get("type") or "")
    if typ not in ("RX.DIRECTED", "RX.ACTIVITY"):
        return None
    params = ev.get("params") or {}
    value = str(ev.get("value") or "")
    src = dst = cmd = text = ""
    if typ == "RX.DIRECTED":
        src = str(params.get("FROM") or "").strip()
        dst = str(params.get("TO") or "").strip()
        cmd = str(params.get("CMD") or "").strip()
        text = str(params.get("TEXT") or "").strip() or value.strip()
        if not text:
            return None
    else:
        src, text = _split_activity(value)
        if not text:
            return None
    utc_ms = params.get("UTC")
    return {
        "kind": "directed" if typ == "RX.DIRECTED" else "activity",
        "utc_ms": utc_ms,
        "ts": _ms_to_local(utc_ms or time.time() * 1000, tz_offset_s),
        "src": src,
        "dst": dst,
        "cmd": cmd,
        "text": text,
        "snr": _as_int(params.get("SNR")),
        "offset": _as_int(params.get("OFFSET")),
        "dial": _as_int(params.get("DIAL")),
        "speed": params.get("SPEED"),
        "tdrift": params.get("TDRIFT"),
        "grid": str(params.get("GRID") or "").strip(),
        "heartbeat": is_heartbeat(cmd, dst, text),
    }


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Deduper:
    """Collapse the RX.ACTIVITY / RX.DIRECTED pair for one transmission.

    Keyed on (utc_ms, dial, offset): the two frames describe the same decode
    and carry the same millisecond. Records are held for `grace_s` so the
    pair usually merges before anything is written; a duplicate that arrives
    after the record was already emitted is dropped against `recent_s` of
    memory, because writing the second line would double the log.
    """

    def __init__(self, grace_s: float = 2.0, max_pending: int = 100,
                 recent_s: float = 15.0):
        self.grace_s = grace_s
        self.max_pending = max_pending
        self.recent_s = recent_s
        self._pending: list[dict] = []
        self._recent: list[tuple[tuple, float]] = []
        self.dropped_duplicates = 0

    @staticmethod
    def _key(rec: dict):
        return (rec.get("utc_ms"), rec.get("dial"), rec.get("offset"))

    def _seen_recently(self, key, now: float) -> bool:
        self._recent = [(k, t) for k, t in self._recent if now - t <= self.recent_s]
        return any(k == key for k, _ in self._recent)

    def add(self, rec: dict, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rec = dict(rec)
        rec["_t"] = now
        key = self._key(rec)
        for i, old in enumerate(self._pending):
            if self._key(old) == key:
                if rec["kind"] == "directed" and old["kind"] != "directed":
                    self._pending[i] = rec
                self.dropped_duplicates += 1
                return self.flush(now)
        if self._seen_recently(key, now):
            self.dropped_duplicates += 1
            return self.flush(now)
        self._pending.append(rec)
        return self.flush(now)

    def flush(self, now: float | None = None, force: bool = False) -> list[dict]:
        now = time.time() if now is None else now
        ready, keep = [], []
        for rec in self._pending:
            if force or len(self._pending) > self.max_pending or \
                    now - rec["_t"] >= self.grace_s:
                ready.append(rec)
            else:
                keep.append(rec)
        self._pending = keep
        ready.sort(key=lambda r: (r.get("utc_ms") or 0))
        for rec in ready:
            rec.pop("_t", None)
            self._recent.append((self._key(rec), now))
        return ready


def _prefix(rec: dict) -> str:
    """Fixed-width metadata block, morse-logger style.

    Every field is padded to a constant width, so the decoded text starts in
    the same column on every line and the log can be read with `cut -c68-`.
    Unknown values are dashes rather than omitted, which is what keeps the
    column stable.
    """
    dial = rec.get("dial") or 0
    dial_s = f"{dial / 1e6:7.3f}MHz" if dial else "    ---MHz"
    snr = rec.get("snr")
    snr_s = f"snr={snr:>+4d}" if isinstance(snr, int) else "snr=  --"
    coh = rec.get("coherence")
    coh_s = f"coh={coh:>3d}" if isinstance(coh, int) else "coh= --"
    ch_s = ""
    if (rec.get("channels_total") or 1) > 1 and rec.get("channel"):
        ch_s = f"ch{rec['channel']}"
    who = (rec.get("src") or "?")[:9]
    return f"{rec['ts']}  [{dial_s} {snr_s} {coh_s} {ch_s:>4} {who:<9}]  "


def render(rec: dict, width: int = 150) -> list[str]:
    """Render one record to one or more lines, each <= width characters."""
    if not isinstance(rec.get("coherence"), int):
        rec["coherence"] = coherence(rec.get("text") or "")
    head = _prefix(rec)
    text = " ".join((rec.get("text") or "").split())
    cont_mark = f"{rec['ts']}  ... "
    if not text:
        return [head.rstrip()[:width]]
    avail = max(10, width - len(head))
    cont_avail = max(10, width - len(cont_mark))
    lines: list[str] = []
    prefix, cap, cur = head, avail, ""
    for word in text.split(" "):
        while len(word) > cap:
            # a token longer than a whole line: truncate, carry the rest on
            if cur:
                lines.append(prefix + cur)
                prefix, cap, cur = cont_mark, cont_avail, ""
            chunk, word = word[: cap - 1], word[cap - 1:]
            lines.append(prefix + chunk + "\u2026")
            prefix, cap = cont_mark, cont_avail
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= cap:
            cur += " " + word
        else:
            lines.append(prefix + cur)
            prefix, cap, cur = cont_mark, cont_avail, word
    if cur:
        lines.append(prefix + cur)
    return [ln[:width] for ln in lines]


def station_of(rec: dict, mycall: str) -> str:
    """Which other station a record belongs to, for per-conversation files."""
    src, dst = rec.get("src") or "", rec.get("dst") or ""
    me = (mycall or "").upper()
    if src and src.upper() != me and CALL_RE.match(src.upper()):
        return src.upper()
    if dst and not dst.startswith("@") and dst.upper() != me:
        return dst.upper()
    if dst.startswith("@"):
        return "GROUP-" + dst[1:].upper()
    return dst.upper() or src.upper() or "UNKNOWN"


class Sink:
    """Append-only writers: daily text, JSONL, SQLite, per-correspondent text."""

    COLUMNS = ("ts", "kind", "src", "dst", "cmd", "snr", "offset", "dial",
               "speed", "tdrift", "grid", "heartbeat", "text", "utc_ms",
               "channel", "coherence")

    def __init__(self, paths: dict, cfg: dict):
        self.paths = paths
        self.cfg = cfg
        self._text_day = None
        self._text_fh = None
        self._jsonl_fh = None
        self._db = None
        self._conv: dict[str, object] = {}
        os.makedirs(paths["logs"], exist_ok=True)
        os.makedirs(paths["conversations"], exist_ok=True)

    def _db_conn(self):
        if self._db is None:
            import sqlite3
            self._db = sqlite3.connect(os.path.join(self.paths["logs"],
                                                    "js8call-logger.db"))
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts TEXT, kind TEXT, src TEXT, dst TEXT, cmd TEXT,"
                " snr INTEGER, offset INTEGER, dial INTEGER, speed INTEGER,"
                " tdrift REAL, grid TEXT, heartbeat INTEGER, text TEXT,"
                " utc_ms INTEGER, channel INTEGER, coherence INTEGER)")
            # logs written by older versions lack columns added since
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(records)")}
            for extra in ("channel", "coherence"):
                if extra not in cols:
                    self._db.execute(f"ALTER TABLE records ADD COLUMN {extra} INTEGER")
            self._db.execute("CREATE INDEX IF NOT EXISTS records_ts ON records(ts)")
            self._db.execute("CREATE INDEX IF NOT EXISTS records_src ON records(src)")
            self._db.commit()
        return self._db

    def _text_path(self, day: str) -> str:
        return os.path.join(self.paths["logs"], f"js8call-logger-{day}.txt")

    def _open_text(self, day: str):
        if self._text_day != day or self._text_fh is None:
            if self._text_fh:
                self._text_fh.close()
            self._text_fh = open(self._text_path(day), "a", encoding="utf-8", buffering=1)
            self._text_day = day
        return self._text_fh

    def want_text(self, rec: dict) -> bool:
        if rec.get("heartbeat") and not self.cfg.get("heartbeats_in_text"):
            return False
        floor = self.cfg.get("snr_min_text")
        if floor is not None and isinstance(rec.get("snr"), int) and rec["snr"] < floor:
            return False
        return True

    def write(self, rec: dict, lines: list[str]) -> None:
        day = rec["ts"][:10]
        if self.want_text(rec):
            fh = self._open_text(day)
            for line in lines:
                fh.write(line + "\n")
        if self._jsonl_fh is None:
            self._jsonl_fh = open(os.path.join(self.paths["logs"], "js8call-logger.jsonl"),
                                  "a", encoding="utf-8", buffering=1)
        self._jsonl_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        db = self._db_conn()
        db.execute(
            "INSERT INTO records ({} ) VALUES ({})".format(
                ", ".join(self.COLUMNS), ", ".join("?" * len(self.COLUMNS))),
            tuple(1 if rec.get(c) is True else 0 if rec.get(c) is False else rec.get(c)
                  for c in self.COLUMNS))
        db.commit()

        name = station_of(rec, self.cfg.get("mycall", ""))
        if rec.get("heartbeat"):
            return
        key = re.sub(r"[^A-Z0-9]", "", name) or "UNKNOWN"
        fh = self._conv.get(key)
        if fh is None:
            fh = open(os.path.join(self.paths["conversations"], f"{key}.txt"),
                      "a", encoding="utf-8", buffering=1)
            self._conv[key] = fh
        for line in lines:
            fh.write(line + "\n")

    def close(self) -> None:
        for fh in [self._text_fh, self._jsonl_fh, *self._conv.values()]:
            try:
                if fh:
                    fh.close()
            except OSError:
                pass
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
