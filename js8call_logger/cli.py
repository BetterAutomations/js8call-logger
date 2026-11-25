"""js8call-logger command line.

Verbs are hyphenated, output is one fact per line, and explanations belong in
the README rather than in status output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

from . import __version__
from . import api, clock, config, engine, pulse, records

APP = config.APP
_STOP = {"now": False}


# --------------------------------------------------------------- utilities
def _pct_int(v):
    return None if v is None else int(v)


def _daily_path(p: dict, day: str, kind: str) -> str:
    if kind == "jsonl":
        return os.path.join(p["logs"], "js8call-logger.jsonl")
    return os.path.join(p["logs"], f"js8call-logger-{day}.txt")


def _path_fn(p: dict, day: str | None, kind: str):
    def fn():
        d = day or time.strftime("%Y-%m-%d")
        return _daily_path(p, d, kind)
    return fn


def _install_stop_handlers():
    def _stop(_sig, _frm):
        _STOP["now"] = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass


def _follow_lines(path_fn, poll=0.5, start_at_end=True):
    """Yield complete lines, surviving midnight rollover and truncation.

    Four ways a naive tail goes quiet forever, all handled: the filename is
    re-resolved every poll (daily files), a half-written line is held back,
    a smaller size or a new inode restarts from zero, and positions are byte
    offsets on a binary handle.
    """
    path = path_fn()
    ino, pos, tail = None, 0, b""
    try:
        st = os.stat(path)
        ino, pos = st.st_ino, (st.st_size if start_at_end else 0)
    except OSError:
        pass
    backoff = poll
    while not _STOP["now"]:
        p = path_fn()
        try:
            st = os.stat(p)
        except OSError:
            if not _STOP["now"]:
                time.sleep(backoff)
            continue
        if p != path or st.st_ino != ino:
            path, ino, pos, tail = p, st.st_ino, 0, b""
        elif st.st_size < pos:
            pos, tail = 0, b""
        if st.st_size > pos:
            try:
                with open(p, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except OSError:
                time.sleep(backoff)
                continue
            data = tail + chunk
            cut = data.rfind(b"\n")
            if cut >= 0:
                tail = data[cut + 1:]
                for line in data[:cut + 1].splitlines():
                    if line:
                        yield line.decode("utf-8", "replace")
            else:
                tail = data
        else:
            time.sleep(backoff)


def _rec_passes(rec: dict, args) -> bool:
    """The one filter predicate, shared by the dump and the follow loop."""
    if not getattr(args, "all", False) and rec.get("heartbeat"):
        return False
    if getattr(args, "min_snr", None) is not None:
        snr = rec.get("snr")
        if snr is None or snr < args.min_snr:
            return False
    if getattr(args, "station", None):
        want = args.station.upper()
        if want not in ((rec.get("src") or "").upper(),
                        (rec.get("dst") or "").upper()):
            return False
    return True


# -------------------------------------------------------------------- verbs
def cmd_start(args):
    cfg = config.load()
    if args.foreground:
        eng = engine.Engine(cfg)
        try:
            return eng.run()
        except engine.EngineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    p = config.ensure_dirs()
    if engine.read_pid(p) and engine._alive(engine.read_pid(p)):
        print("already running")
        return 0
    log = open(os.path.join(p["run"], "daemon.log"), "ab", buffering=0)
    subprocess.Popen([sys.executable, "-m", "js8call_logger.cli", "start",
                      "--foreground"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        time.sleep(0.5)
        st = engine.read_status(p)
        if st.get("running"):
            print("started")
            return 0
        if st.get("error"):
            print(f"error: {st['error']}")
            return 1
    print("started (no status yet; see run/daemon.log)")
    return 0


def cmd_stop(_args):
    p = config.ensure_dirs()
    stopped = engine.stop(p)
    print("stopped" if stopped else "not running")
    return 0


def cmd_status(args):
    p = config.ensure_dirs()
    st = engine.read_status(p)
    cfg = config.load(create=False)
    running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))
    if getattr(args, "json", False):
        out = dict(st)
        out["running"] = running
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(f"service      {'running' if running else 'stopped'}")
    if not running:
        return 0
    decs = st.get("decoders") or []
    if decs:
        up = sum(1 for d in decs if d.get("up"))
        print(f"decoders     {up}/{len(decs)} up")
        for d in decs:
            print(f"  channel {d.get('channel')}  port {d.get('port')}  "
                  f"{'up' if d.get('up') else 'down'}")
    else:
        print("decoder      down")
    print(f"decodes      {st.get('decodes', 0)}")
    last = st.get("last_decode_s_ago")
    print(f"last decode  {('never' if last is None else str(last) + 's ago')}")
    print(f"restarts     {st.get('restarts', 0)}")
    off = st.get("clock_offset")
    samples = st.get("clock_samples", 0)
    if off is None or samples < 3:
        print(f"clock offset -- (needs 3 decodes, have {samples})")
    else:
        print(f"clock offset {off:+.2f}s from {samples} decodes")
    print(f"dial         {st.get('dial_hz', cfg.get('dial_hz'))} Hz")
    print(f"api          {cfg.get('api_host')}:{cfg.get('api_port')}")
    return 0


def cmd_tail(args):
    p = config.ensure_dirs()
    cfg = config.load(create=False)

    if args.js8call_debug:
        return _tail_debug(cfg)

    kind = "jsonl" if (args.json or args.min_snr is not None or args.station) else "text"
    day = args.date or time.strftime("%Y-%m-%d")
    path = _daily_path(p, day, kind)

    if kind == "text":
        if os.path.exists(path):
            with open(path, errors="replace") as f:
                lines = f.readlines()
            if not args.all:
                lines = [ln for ln in lines if not records.HEARTBEAT_RE.search(ln)]
            sys.stdout.write("".join(lines[-args.lines:]))
            sys.stdout.flush()
        elif not args.follow:
            print(f"(no log for {day} yet: {path})")
            return 1
        if args.follow:
            _install_stop_handlers()
            for line in _follow_lines(_path_fn(p, args.date, "text"), start_at_end=True):
                if not args.all and records.HEARTBEAT_RE.search(line):
                    continue
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        return 0

    rows = []
    if os.path.exists(path):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if (rec.get("ts") or "")[:10] != day:
                    continue
                if _rec_passes(rec, args):
                    rows.append(rec)
    for rec in rows[-args.lines:]:
        print(json.dumps(rec, ensure_ascii=False))
    if not args.follow:
        return 0
    _install_stop_handlers()
    for line in _follow_lines(_path_fn(p, None, "jsonl"), start_at_end=True):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if args.date and (rec.get("ts") or "")[:10] != args.date:
            continue
        if _rec_passes(rec, args):
            print(json.dumps(rec, ensure_ascii=False), flush=True)
    return 0


def _tail_debug(cfg):
    """Print raw API frames, unfiltered, with local receive time."""
    client = api.Js8Api(cfg["api_host"], int(cfg["api_port"]))
    try:
        client.connect(3.0)
    except (OSError, api.ApiError):
        print(f"no API at {cfg['api_host']}:{cfg['api_port']} - is the logger running?")
        return 1
    _install_stop_handlers()
    print("-- raw JS8Call API frames (Ctrl-C to stop)", flush=True)
    try:
        while not _STOP["now"]:
            for frame in client.read(0.5):
                stamp = time.strftime("%H:%M:%S")
                line = json.dumps(frame, ensure_ascii=False)
                print(f"{stamp} {frame.get('type', '?')} {line}", flush=True)
    except api.ApiError as exc:
        print(f"API closed: {exc}")
        return 1
    return 0


def cmd_clock(args):
    p = config.ensure_dirs()
    st = engine.read_status(p)
    off = st.get("clock_offset")
    if off is None:
        print("no clock measurement yet (needs decodes)")
        return 1
    print(f"offset       {off:+.2f}s from {st.get('clock_samples', 0)} decodes")
    if args.set:
        ok, note = clock.apply_system(float(off), dry_run=args.dry_run)
        print(("ok           " if ok else "failed       ") + note)
        return 0 if ok else 1
    return 0


def cmd_cable(args):
    """Manage the virtual audio cable: up, down, status."""
    p = config.ensure_dirs()
    sf = os.path.join(p["run"], "pulse-modules.json")
    cfg = config.load()
    if args.action == "up":
        if not args.no_monitor and pulse._tap_active():
            print("the copy loop is on (your speakers -> the cable), so monitoring")
            print("would feed the cable back into your speakers.")
            print("pick one: 'js8call-logger route-audio --stop-copy' to remove it, "
                  "or 'js8call-logger cable up --no-monitor'.")
            return 2
        removed = pulse.stop_monitor(sf) if args.no_monitor else []
        channels = max(1, int(getattr(args, "channels", 1) or 1))
        done = {}
        for ch in range(1, channels + 1):
            done = pulse.ensure_cable(state_file=sf, monitor=(not args.no_monitor and ch == 1),
                                      rate=int(cfg.get("cable_rate") or 48000), channel=ch)
            print(f"virtual cable {ch}: sink '{pulse.sink_name(ch)}' -> "
                  f"source '{pulse.source_name(ch)}'")
        if channels != int(cfg.get("channels") or 1):
            cfg["channels"] = channels
            config.save(cfg)
            running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))
            print(f"config: channels={channels}"
                  + (" - restart the logger to run them" if running else ""))
        if done.get("monitor_skipped"):
            print("monitoring left off: the copy loop is running "
                  "('js8call-logger route-audio --stop-copy' to remove it)")
        if removed:
            print(f"monitoring off ({len(removed)} loopback(s) removed)")
        if done.get("monitor") or pulse.cable_status()["monitor"]:
            print("monitoring enabled: you still hear the cable output on your speakers")
        if done.get("sink"):
            print("(created just now)")
        return 0
    if args.action == "down":
        only = getattr(args, "channels", 1) if args.channels > 1 else None
        removed = pulse.cable_down(sf, channel=only)
        print(f"cable {only} removed ({len(removed)} module(s) unloaded)" if only
              else f"all cables removed ({len(removed)} module(s) unloaded)")
        return 0
    st = pulse.cable_status()
    print(f"sink   {pulse.SINK}: {'present' if st['sink'] else 'MISSING'}")
    print(f"source {pulse.SOURCE}: {'present' if st['source'] else 'MISSING'}")
    print(f"monitor loopback: {'on' if st['monitor'] else 'off'}")
    print(f"copy loop:        {'on' if st['tap'] else 'off'}")
    if st["channels"]:
        print("channels: " + ", ".join(str(c) for c in st["channels"]))
    print("\nsinks:    " + ", ".join(st["sinks"]))
    print("sources:  " + ", ".join(st["sources"]))
    return 0


def cmd_route(args):
    """Get audio onto the cable: move streams, copy the speakers, loop an input."""
    p = config.ensure_dirs()
    sf = os.path.join(p["run"], "pulse-modules.json")
    cfg = config.load()
    wants_move = bool(args.all or args.index)
    wants_copy = bool(args.copy or args.stop_copy)
    wants_source = bool(args.source or args.stop_source)

    if args.list_sources:
        hw = pulse.source_names()
        mon = pulse.monitor_sources()
        if hw:
            print("  hardware inputs you can loop into the cable:")
            for n in hw:
                print(f"    {n}")
        if mon:
            print("  other programs' outputs (copied into the cable, nothing moved):")
            for n in mon:
                print(f"    {n}")
        if not hw and not mon:
            print("no capture sources found (only monitors and the cable)")
            return 0
        print(f"\n  use: {APP} route-audio --source NAME")
        return 0

    if wants_source and (wants_move or wants_copy):
        print("pick one: --source loops a hardware input; --all/--index move "
              "playback streams; --copy copies the speakers.")
        return 2

    if args.source:
        try:
            created = pulse.loop_source(args.source, state_file=sf)
        except pulse.PulseError as exc:
            print(str(exc))
            return 2
        if created:
            print(f"line input loop on: {args.source} -> the cable")
            print("the input is still usable by other programs; nothing was moved")
        else:
            print(f"already looped: {args.source} is already feeding the cable")
        return 0

    if args.stop_source:
        killed = pulse.stop_source_loops(state_file=sf)
        print(f"line input loop off ({len(killed)} module(s) unloaded)" if killed
              else "no hardware-input loop was running")
        return 0

    if wants_copy and wants_move:
        print("pick one: --copy sends a copy of your speakers to the cable, "
              "while --all/--index move individual streams.")
        return 2
    if args.copy and args.stop_copy:
        print("pick one: --copy turns the copy on, --stop-copy turns it off.")
        return 2

    if args.copy:
        if pulse.cable_status()["monitor"]:
            print("monitoring is on (cable -> your speakers), so copying your "
                  "speakers back into the cable would be a feedback loop.")
            print("pick one: run 'js8call-logger cable up --no-monitor' first, "
                  "or move streams instead with 'js8call-logger route-audio --all'.")
            return 2
        done = pulse.ensure_cable(state_file=sf, monitor=False,
                                  rate=int(cfg.get("cable_rate") or 48000), tap=True)
        if done.get("tap") or pulse._tap_active():
            print("copy on: everything your speakers play now also goes to the cable")
            print("nothing was moved, so your apps keep playing where they are")
            return 0
        print("could not create the copy loop (is the cable up?)")
        return 1

    if args.stop_copy:
        n = pulse.stop_tap(sf)
        print(f"copy off ({len(n)} module(s) unloaded)" if n else "copy was not on")
        return 0

    if wants_move and pulse._tap_active():
        print("the copy loop is on, so streams are already being fed to the cable.")
        print("pick one: 'js8call-logger route-audio --stop-copy' first if you want "
              "to move streams instead.")
        return 2

    if args.list:
        rows = pulse.sink_input_details()
        if not rows:
            print("no application audio streams are playing right now")
            return 0
        print(f"{'idx':>4}  {'sink':<28} {'app':<20} stream")
        for r in rows:
            print(f"{r['index']:>4}  {r['sink_name']:<28} {r['app'][:20]:<20} {r['stream']}")
        return 0

    to = int(getattr(args, "to", 1) or 1)
    try:
        note = engine.ensure_cable(cfg, sf, to)
        if note:
            print(note)
    except engine.EngineError as exc:
        print(str(exc))
        return 2
    target = pulse.sink_name(to)
    picked = [row for row in pulse.sink_input_details()
              if (not args.index or str(row["index"]) in {str(i) for i in args.index})
              and not pulse.is_own_loopback(row)]
    moved, skipped = pulse.route_sink_inputs(target=target, indices=args.index)
    # Remember the applications whether or not they had to move, so a stream
    # already on the cable still gets its app watched for reloads.
    apps = sorted({(row["app"] if row["app"] != "?" else row["stream"])
                   for row in picked})
    if apps:
        cfg["route_apps"] = sorted(set(list(cfg.get("route_apps") or []) + apps))
        config.save(cfg)
        print(f"  remembered {', '.join(apps)}: new streams from these apps are "
              f"routed automatically while js8call-logger runs "
              f"(config route_watch=false to stop that)")
    for idx, name in moved:
        print(f"moved stream {idx} ({name}) to '{target}'")
    for idx, name, why in skipped:
        print(f"skipped stream {idx} ({name}) - {why}")
    if not moved:
        if any(why == "already on the cable" for _i, _n, why in skipped):
            print("everything that is playing is already on the cable")
            return 0
        print("nothing moved (is the WebSDR/browser actually playing audio?)")
        return 1
    return 0


def cmd_tune(args):
    cfg = config.load()
    ch = int(getattr(args, "channel", 1) or 1)
    ok, why = engine.cable_present(cfg, ch)
    print(f"cable        {'up' if ok else 'missing'}")
    if why:
        print(f"             {why}")
    client = api.Js8Api(cfg["api_host"], engine.channel_port(cfg, ch))
    try:
        client.connect(3.0)
    except (OSError, api.ApiError):
        print("api          not reachable (start the logger first)")
        return 1
    freq = client.get_freq()
    print(f"dial         {freq.get('DIAL')} Hz")
    print(f"offset       {freq.get('OFFSET')} Hz")
    print(f"version      {client.version() or '?'}")
    if args.dial:
        client.set_freq(int(args.dial), args.offset, )
        cfg["dial_hz"] = int(args.dial)
        config.save(cfg)
        print(f"set          {args.dial} Hz + {args.offset} Hz")
        print("saved        dial_hz in config.json (kept across restarts)")
    return 0


def fmt_uptime(seconds) -> str:
    s = int(seconds or 0)
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m:02d}m{s % 60:02d}s"


def cmd_doctor(_args):
    """Report on the whole installation, like morse-logger's doctor.

    Sections matter: a flat list hides the difference between "the decoder is
    missing" and "audio is arriving but nothing is logging". Only [FAIL] lines
    affect the exit status - [!!] is a warning, [--] is information.
    """
    p = config.ensure_dirs()
    cfg = config.load()
    print(f"{APP} {__version__}  home {p['home']}")
    ok = True

    def chk(name, good, detail=""):
        nonlocal ok
        ok = ok and bool(good)
        print(f"  [{'ok' if good else 'FAIL'}] {name}"
              + (f" - {detail}" if detail else ""))

    def note(name, detail=""):
        print(f"  [--] {name}" + (f" - {detail}" if detail else ""))

    def warn(name, detail=""):
        print(f"  [!!] {name}" + (f" - {detail}" if detail else ""))

    st = engine.read_status(p)
    running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))

    print("\nData")
    if os.path.abspath(p["logs"]).startswith(os.path.abspath(p["home"]) + os.sep):
        print(f"  [ok] log directory {p['logs']}")
    else:
        print(f"  [!!] log directory {p['logs']} is outside the data directory "
              f"{p['home']} - old logs look lost when they are not")
    try:
        du = shutil.disk_usage(p["home"])
        chk("free disk space", du.free > 200 * 1024 * 1024,
            f"{du.free // (1024 ** 2)} MB free")
    except OSError:
        pass
    if st.get("restarts", 0) > 5:
        warn("decoder restarts", f"{st['restarts']} times this session - something "
             f"is stopping it from staying up; see {p['run']}/restarts.log")

    print("\nDecoder")
    try:
        chk("js8call binary", True, engine.find_binary(cfg))
    except engine.EngineError as exc:
        chk("js8call binary", False, str(exc))
    chk("pactl", shutil.which("pactl"), "pulseaudio-utils / pipewire-pulse")
    chk("parec", shutil.which("parec"), "pulseaudio-utils / pipewire-pulse")
    chk("pacat", shutil.which("pacat"), "needed by selftest")
    chk("python", sys.version_info >= (3, 10), sys.version.split()[0])

    print("\nAudio server")
    if pulse.available():
        try:
            info = pulse.server_info()
            chk("pulse/pipewire server", True,
                f"{info.get('Server Name', '?')} {info.get('Server Version', '')}".strip())
        except pulse.PulseError as exc:
            chk("pulse/pipewire server", False, str(exc))
        cst = pulse.cable_status()
        chk("virtual cable", cst["sink"] and cst["source"],
            f"sink={cst['sink']} source={cst['source']} monitor={cst['monitor']} "
            f"(create with: {APP} cable up)")
    else:
        chk("pulse/pipewire server", False, "pactl/parec not found")

    print("\nAudio into the cable")
    try:
        all_rows = pulse.sink_input_details()
        rows = [r for r in all_rows if r.get("sink_name") == pulse.sink_name(1)]
        elsewhere = [r for r in all_rows
                     if r.get("sink_name") != pulse.sink_name(1)
                     and not pulse.is_own_loopback(r)]
        if not rows:
            note("nothing is playing into the cable yet")
            # Audio can be playing and still be invisible here: it is going to
            # the speakers, or - the confusing case - into another tool's
            # cable. Name where it actually is, or the message reads as "your
            # browser is not playing".
            for r in elsewhere[:6]:
                who = r.get("app") if r.get("app") != "?" else r.get("stream")
                print(f"  [!!] {who or '?'} is playing on {r.get('sink_name') or '?'}")
            if elsewhere:
                print(f"       send it over with: {APP} route-audio --all   "
                      f"(--index N for one stream, --copy to leave it where it is)")
        for r in rows:
            # A loopback *into* our cable is a feed (the speakers tap, a
            # hardware input, or another program's cable); the cable->speakers
            # monitor lives on the speakers sink and never appears here.
            kind = "feed (loopback)" if pulse.is_own_loopback(r) else "source"
            who = r.get("app") if r.get("app") != "?" else (r.get("stream") or "?")
            print(f"  [ok] {who} -> {kind} (stream {r.get('index')})")
        real = rows
        if real and pulse.cable_status().get("source"):
            sample = pulse.capture_samples(1.0, rate=8000)
            if not any(sample):
                warn("digital silence", "the streams are open but delivering zeros - "
                     "the application has lost the audio device; restart it fully "
                     "(a tab reload is not enough)")
            else:
                db = pulse.peak_dbfs(sample)
                if db < -30:
                    warn(f"cable level {db} dBFS", "far too quiet to decode; raise the "
                         "volume (aim for -20 to -10 dBFS)")
                elif db > -1.5:
                    warn(f"cable level {db} dBFS", "clipping; lower the volume")
                else:
                    print(f"  [ok] cable level {db} dBFS")
        if real and not running:
            warn("nothing is logging", f"{len(real)} stream(s) on the cable but the "
                 f"logger is not running ({APP} start)")
    except pulse.PulseError as exc:
        note("could not inspect", str(exc))

    print("\nLogger")
    if running:
        print(f"  [ok] logger running - pid {engine.read_pid(p)}")
    else:
        note("logger running", f"not running (start it with: {APP} start)")
    decs = st.get("decoders") or []
    for d in decs:
        chk(f"decoder {d.get('channel')}", d.get("up"),
            f"port {d.get('port')}"
            + ("" if d.get("up") else f" - not responding, see {p['run']}/js8call-"
               f"{d.get('channel')}.log"))
    if running and decs:
        port = int(decs[0].get("port") or cfg["api_port"])
        client = api.Js8Api(cfg["api_host"], port)
        try:
            client.connect(2.0)
            chk("api responding", True,
                f"JS8Call {client.version() or '?'} on {cfg['api_host']}:{port}")
        except (OSError, api.ApiError):
            chk("api responding", False, f"nothing answered on {cfg['api_host']}:{port}")
        finally:
            client.close()
        print(f"       decodes logged: {st.get('decodes', 0)}  uptime "
              f"{fmt_uptime(time.time() - st.get('started_at', time.time()))}")
        last = st.get("last_decode_s_ago")
        print("       last decode: "
              + ("never" if last is None else f"{last}s ago"))
        samples = st.get("clock_samples", 0)
        if samples >= 3:
            print(f"       clock offset: {st.get('clock_offset', 0):+.2f}s "
                  f"from {samples} decodes")
        else:
            print(f"       clock offset: needs 3 decodes, have {samples}")
    print(f"       log dir: {p['logs']}")

    print("\nInstallation")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    appimage = os.path.join(here, "bundle", "JS8Call.AppImage")
    binary = cfg.get("js8call_binary")
    if binary and os.path.exists(binary):
        # An installed copy always has this; it is a fact, so it is a check.
        chk("decoder installed", True, binary)
    elif os.path.exists(appimage):
        # Running from a checkout: the bundle is the decoder, and the Decoder
        # section has already verified it. Informational, not a failure.
        note("offline bundle", f"{appimage} "
             f"({os.path.getsize(appimage) // (1024 ** 2)} MB, not unpacked)")
    else:
        # Do not double-count: a missing decoder is already [FAIL] above.
        warn("offline bundle", "bundle/JS8Call.AppImage missing - run "
             "refresh-bundle.sh before installing on a machine without network")

    print("\n" + ("All checks passed." if ok else "Some checks FAILED (see above)."))
    return 0 if ok else 1


def cmd_selftest(args):
    """Offline checks of the log pipeline; --live also proves the decoder."""
    fails = 0

    def chk(name, ok, detail=""):
        nonlocal fails
        if not ok:
            fails += 1
        print(f"{'ok  ' if ok else 'FAIL'} {name:<26} {detail}")

    rec = {"ts": "2026-11-25 02:38:00", "dial": 14078000, "snr": 33,
           "offset": 1500, "src": "N0CALL", "dst": "@ALLCALL",
           "text": "CQ CQ CQ EM48 " * 20, "kind": "directed", "utc_ms": 1,
           "heartbeat": False}
    lines = records.render(rec, 150)
    chk("wrap<=150", all(len(ln) <= 150 for ln in lines), f"{len(lines)} lines, "
        f"max {max(len(ln) for ln in lines)}")
    chk("continuation ts", all(ln.startswith("2026-11-25 02:38:00")
                               for ln in lines), "same timestamp on every line")
    long_tok = {"ts": "2026-11-25 02:38:00", "dial": 14078000, "snr": 1,
                "offset": 1500, "src": "A1AA", "dst": "B2BB",
                "text": "x" * 400, "kind": "directed", "utc_ms": 2,
                "heartbeat": False}
    lt = records.render(long_tok, 150)
    chk("oversize token", all(len(ln) <= 150 for ln in lt), f"{len(lt)} lines")

    d = records.Deduper(grace_s=2.0)
    act = {"kind": "activity", "utc_ms": 5, "dial": 1, "offset": 2, "text": "a"}
    dir_ = {"kind": "directed", "utc_ms": 5, "dial": 1, "offset": 2, "text": "b"}
    held = d.add(act, now=0) + d.add(dir_, now=0.1)
    out = held + d.flush(now=3.0)
    chk("dedupe pair", len(out) == 1 and out[0]["kind"] == "directed",
        f"{len(out)} record(s)")

    hb = records.parse_event({"type": "RX.DIRECTED", "value": "K1ABC: @HB HEARTBEAT",
                              "params": {"FROM": "K1ABC", "TO": "@HB", "CMD": " HEARTBEAT",
                                         "TEXT": "", "UTC": 1000}})
    chk("heartbeat detect", bool(hb and hb["heartbeat"]))

    if not getattr(args, "offline", False):
        fails += _selftest_live(chk)
    return 0 if fails == 0 else 1


def _start_logger() -> bool:
    """Start the daemon for the duration of a self test. True if we started it."""
    p = config.ensure_dirs()
    if engine.read_status(p).get("running"):
        return False
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = here + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.run([sys.executable, "-m", "js8call_logger.cli", "start"],
                   capture_output=True, text=True, env=env)
    for _ in range(120):
        time.sleep(0.5)
        if engine.read_status(p).get("running"):
            return True
    return True


def _selftest_live(chk) -> int:
    """Play a known JS8 frame into the cable and wait for it to be logged.

    This is the check that proves the whole chain: the bundled decoder runs,
    the cable carries audio, the API answers and the record reaches the log.
    It starts the logger if it is not already running, and stops it again
    afterwards, so a fresh install can be verified with one command.
    """
    cfg = config.load(create=False)
    p = config.ensure_dirs()
    fixture = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tests", "fixtures", "n0call-cq.raw")
    if not os.path.exists(fixture):
        chk("live decode", False, f"fixture missing: {fixture}")
        return 1
    started = _start_logger()
    try:
        if not engine.read_status(p).get("running"):
            chk("live decode", False, "could not start the logger; see run/daemon.log")
            return 1
        ok, why = engine.cable_present(cfg, 1)
        if not ok:
            chk("live decode", False, why)
            return 1
        before = int(engine.read_status(p).get("decodes") or 0)
        # JS8 frames begin on a 15 s boundary; start just after one.
        time.sleep(max(0, (int(time.time() // 15) + 1) * 15 - time.time()))
        with open(fixture, "rb") as f:
            subprocess.run(["pacat", "--device=" + pulse.sink_name(1),
                            "--rate=48000", "--channels=1",
                            "--format=s16le", "--raw"], stdin=f)
        deadline = time.time() + 30
        while time.time() < deadline:
            if int(engine.read_status(p).get("decodes") or 0) > before:
                chk("live decode", True, "frame decoded and logged")
                return 0
            time.sleep(1)
        chk("live decode", False,
            "nothing decoded in 30 s (audio path or clock; see --js8call-debug)")
        return 1
    finally:
        if started:
            engine.stop(p)


# --------------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(prog=APP, description="Headless JS8 logger")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="start the logger")
    sp.add_argument("-f", "--foreground", action="store_true")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="stop the logger")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("status", help="service status")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("tail", help="show the log")
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("-n", "--lines", type=int, default=20)
    sp.add_argument("--date", help="YYYY-MM-DD (default today)")
    sp.add_argument("--json", action="store_true", help="JSONL view")
    sp.add_argument("--all", action="store_true", help="include heartbeats")
    sp.add_argument("--min-snr", type=_pct_int, help="hide weaker decodes")
    sp.add_argument("--station", help="only this callsign")
    sp.add_argument("--js8call-debug", action="store_true",
                    help="print raw API frames")
    sp.set_defaults(func=cmd_tail)

    sp = sub.add_parser("cable", help="manage the virtual audio cable")
    sp.add_argument("action", choices=["up", "down", "status"])
    sp.add_argument("--no-monitor", action="store_true",
                    help="do not play the cable on your speakers")
    sp.add_argument("--channels", type=int, default=1)
    sp.set_defaults(func=cmd_cable)

    sp = sub.add_parser("route-audio", help="get audio onto the cable")
    sp.add_argument("--list", action="store_true", help="list playing streams")
    sp.add_argument("--all", action="store_true", help="move every stream to the cable")
    sp.add_argument("--index", type=int, nargs="+", help="move these stream indexes")
    sp.add_argument("--to", type=int, default=1, help="cable channel (default 1)")
    sp.add_argument("--copy", action="store_true",
                    help="copy the speakers into the cable, moving nothing")
    sp.add_argument("--stop-copy", action="store_true")
    sp.add_argument("--source", help="loop a hardware input into the cable")
    sp.add_argument("--stop-source", action="store_true")
    sp.add_argument("--list-sources", action="store_true",
                    help="list hardware inputs you can loop")
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("tune", help="show/change dial frequency")
    sp.add_argument("--dial", type=int)
    sp.add_argument("--offset", type=int, default=1500)
    sp.add_argument("--channel", type=int, default=1,
                    help="which decoder (default 1)")
    sp.set_defaults(func=cmd_tune)

    sp = sub.add_parser("clock", help="report or apply the clock offset")
    sp.add_argument("--set", action="store_true", help="write the system clock")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_clock)

    sp = sub.add_parser("doctor", help="check the environment")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("selftest", help="check the log pipeline and decode a frame")
    sp.add_argument("--offline", action="store_true",
                    help="skip the decode test (no cable, no decoder)")
    sp.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except engine.EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
