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

from . import glossary
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


def parse_event(ev: dict, tz_offset_s: float = 0.0, eot: str = "\u2662") -> dict | None:
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
    heartbeat = is_heartbeat(cmd, dst, text)
    the_frame = _as_int(params.get("BITS"))
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
        "heartbeat": heartbeat,
        "frame": the_frame,
        # JS8Call appends the end-of-transmission character only to the last
        # frame of a message, so a DIRECTED record without it is a piece. That
        # is the decoder's own opinion - better than guessing from BITS, which
        # only ACTIVITY frames carry.
        "fragment": (not text.rstrip().endswith(eot)) if typ == "RX.DIRECTED"
        else is_fragment(the_frame, text, heartbeat),
    }


# A JS8 frame carries about 13 characters of free text. Measured against
# ALL.TXT on 2026-11-25: type 3 (directed) is a complete message, types 1 and 2
# are a compound message's header and body chunks, and type 0 whose text is not
# a heartbeat is one free-text chunk of a longer transmission.
FRAME_CHARS = 13


def is_fragment(frame, text: str, heartbeat: bool) -> bool:
    """True when this decode is one piece of a longer transmission."""
    if not isinstance(frame, int):
        return False
    if frame in (1, 2):                      # FrameCompound / CompoundDirected
        return True
    if frame == 0 and not heartbeat and len((text or "").strip()) >= FRAME_CHARS:
        return True                          # free-text chunk, more may follow
    return False


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
                 recent_s: float = 60.0):
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

    @staticmethod
    def _weight(rec: dict) -> tuple:
        """How much a record is worth keeping when two describe one decode.

        JS8Call answers a decode twice - once with the text and once with a
        placeholder ("……") for a frame it could not render. Same utc_ms, same
        offset, same SNR: one transmission. Prefer the DIRECTED record, then
        the one whose text actually contains alphanumerics, then the longer.
        """
        text = rec.get("text") or ""
        alnum = sum(1 for ch in text if ch.isalnum())
        return (rec.get("kind") == "directed", alnum, len(text))

    def add(self, rec: dict, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rec = dict(rec)
        rec["_t"] = now
        key = self._key(rec)
        for i, old in enumerate(self._pending):
            if self._key(old) == key:
                if rec.get("frame") is None and old.get("frame") is not None:
                    rec["frame"] = old["frame"]     # only ACTIVITY carries BITS
                if self._weight(rec) > self._weight(old):
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
    off = rec.get("offset")
    tone_s = f"{off:4d}Hz" if isinstance(off, int) else "   -Hz"
    who = (rec.get("src") or "?")[:9]
    return f"{rec['ts']}  [{dial_s} {snr_s} {coh_s} {ch_s:>4} {tone_s} {who:<9}]  "


def render(rec: dict, width: int = 150, expand: str = "off",
           expand_min_coh: int | None = None) -> list[str]:
    """Render one record to one or more lines, each <= width characters.

    `expand` is "off", "gloss" (meaning[TOKEN]) or "replace" (clean prose).
    Expansion happens before wrapping and the wrapper moves *units*, so a
    meaning never separates from the token it explains.
    """
    if not isinstance(rec.get("coherence"), int):
        rec["coherence"] = coherence(rec.get("text") or "")
    head = _prefix(rec)
    raw = " ".join((rec.get("text") or "").split())
    units, n_exp = glossary.translate_units(
        raw, mode=expand, min_coherence=expand_min_coh,
        coherence_fn=lambda t: rec["coherence"])
    if expand != "off" and n_exp:
        rec["text_xlated"] = " ".join(units)
        rec["expansions"] = n_exp
    if rec.get("fragment") and units and not units[-1].endswith("\u2026"):
        units = units + ["\u2026"]          # one frame of a longer message
    if not units:
        return [head.rstrip()[:width]]
    cont_mark = f"{rec['ts']}  ... "
    avail = max(10, width - len(head))
    cont_avail = max(10, width - len(cont_mark))
    lines: list[str] = []
    prefix, cap, cur = head, avail, ""
    for unit in units:
        while len(unit) > cap:
            # a unit longer than a whole line: truncate, carry the rest on
            if cur:
                lines.append(prefix + cur)
                prefix, cap, cur = cont_mark, cont_avail, ""
            chunk, unit = unit[: cap - 1], unit[cap - 1:]
            lines.append(prefix + chunk + "\u2026")
            prefix, cap = cont_mark, cont_avail
        if not cur:
            cur = unit
        elif len(cur) + 1 + len(unit) <= cap:
            cur += " " + unit
        else:
            lines.append(prefix + cur)
            prefix, cap, cur = cont_mark, cont_avail, unit
    if cur:
        lines.append(prefix + cur)
    return [ln[:width] for ln in lines]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper()) or "UNKNOWN"


def conversation_targets(rec: dict, mycall: str) -> list[str]:
    """Files a record belongs in, relative to the conversations directory.

    Two questions worth answering separately, so a record can land in both: who
    said it (a callsign file) and where it was said (a group file for @ALLCALL,
    @SITREP, ...). Heartbeats are excluded by the caller.
    """
    src = (rec.get("src") or "").strip()
    dst = (rec.get("dst") or "").strip()
    me = (mycall or "").upper()
    out: list[str] = []
    if src and src.upper() != me and CALL_RE.match(src.upper()):
        out.append(f"{_safe_name(src)}.txt")
    if dst.startswith("@"):
        out.append(os.path.join("groups", f"{_safe_name(dst[1:])}.txt"))
    elif dst and dst.upper() != me and CALL_RE.match(dst.upper()):
        out.append(f"{_safe_name(dst)}.txt")
    if not out:
        out.append(f"{_safe_name(dst or src)}.txt")
    # de-duplicate, keep order
    seen: list[str] = []
    for name in out:
        if name not in seen:
            seen.append(name)
    return seen


def station_of(rec: dict, mycall: str) -> str:
    """Primary correspondent, used for display and older log files."""
    return os.path.basename(conversation_targets(rec, mycall)[0])[:-4]


class Sink:
    """Append-only writers: daily text, JSONL, SQLite, per-correspondent text."""

    COLUMNS = ("ts", "kind", "src", "dst", "cmd", "snr", "offset", "dial",
               "speed", "tdrift", "grid", "heartbeat", "text", "utc_ms",
               "channel", "coherence", "frame", "fragment",
               "text_xlated", "expansions")

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
                " utc_ms INTEGER, channel INTEGER, coherence INTEGER,"
                " frame INTEGER, fragment INTEGER,"
                " text_xlated TEXT, expansions INTEGER)")
            # logs written by older versions lack columns added since
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(records)")}
            for extra, typ in (("channel", "INTEGER"), ("coherence", "INTEGER"),
                               ("frame", "INTEGER"), ("fragment", "INTEGER"),
                               ("text_xlated", "TEXT"), ("expansions", "INTEGER")):
                if extra not in cols:
                    self._db.execute(f"ALTER TABLE records ADD COLUMN {extra} {typ}")
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
            path = self._text_path(day)
            new = not os.path.exists(path)
            self._text_fh = open(path, "a", encoding="utf-8", buffering=1)
            self._text_day = day
            if new and self.cfg.get("expand"):
                # A month from now nobody remembers what meaning[TOKEN] meant.
                self._text_fh.write(glossary.legend() + "\n")
        return self._text_fh

    def want_text(self, rec: dict) -> bool:
        if rec.get("heartbeat") and not self.cfg.get("heartbeats_in_text"):
            return False
        # A frame JS8Call could not render as text ("……") is noise in the human
        # log; it stays in the JSONL and SQLite where nothing is lost.
        if not any(ch.isalnum() for ch in (rec.get("text") or "")):
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

        if rec.get("heartbeat"):
            return
        for name in conversation_targets(rec, self.cfg.get("mycall", "")):
            path = os.path.join(self.paths["conversations"], name)
            fh = self._conv.get(name)
            if fh is None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fh = open(path, "a", encoding="utf-8", buffering=1)
                self._conv[name] = fh
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
