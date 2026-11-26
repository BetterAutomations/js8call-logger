"""js8call-logger command line.

Verbs are hyphenated, output is one fact per line, and explanations belong in
the README rather than in status output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

from . import __version__
from . import (api, burst, clock, config, engine, migrate, pulse, records,
               retention, sdr, service, tune)

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
    quiet = bool(getattr(args, "quiet", False))
    say = (lambda *a, **k: None) if quiet else print
    if args.foreground:
        p = config.ensure_dirs()
        pid = engine.read_pid(p)
        if not (pid and engine._alive(pid)):
            try:
                pruned = retention.maybe_auto_prune(p, cfg)
            except Exception as exc:         # never block a start over pruning
                print(f"auto-prune skipped: {exc}", file=sys.stderr, flush=True)
                pruned = None
            if pruned:
                print("auto-prune: " + retention.summary_text(pruned).replace(
                    "\n", " | "), flush=True)
            due_now, why = tune.due(cfg, p["home"])
            if due_now:
                if cfg.get("input_device"):
                    print(f"tune: skipping the sweep ({why}) - the input is "
                          f"{cfg['input_device']}, not the cable", flush=True)
                else:
                    print(f"tune: decode sweep due ({why}) - measuring now",
                          flush=True)
                    try:
                        tune.auto_sweep(cfg, p["home"])
                    except Exception as exc:   # never block a start over this
                        print(f"tune: sweep failed: {exc}", flush=True)
        eng = engine.Engine(cfg)
        try:
            return eng.run()
        except engine.EngineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    p = config.ensure_dirs()
    if engine.read_pid(p) and engine._alive(engine.read_pid(p)):
        say("already running")
        return 0
    log = open(os.path.join(p["run"], "daemon.log"), "ab", buffering=0)
    subprocess.Popen([sys.executable, "-m", "js8call_logger.cli", "start",
                      "--foreground"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        time.sleep(0.5)
        st = engine.read_status(p)
        if st.get("running"):
            say("started")
            return 0
        if st.get("error"):
            say(f"error: {st['error']}")
            return 1
    say("started (no status yet; see run/daemon.log)")
    return 0


def cmd_stop(_args):
    p = config.ensure_dirs()
    stopped = engine.stop(p)
    print("stopped" if stopped else "not running")
    return 0


def cmd_restart(args):
    """Stop and start again, keeping the dial and every other setting."""
    p = config.ensure_dirs()
    was_running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))
    engine.stop(p)
    rc = cmd_start(argparse.Namespace(foreground=False, quiet=True))
    print("restarted" if was_running else "started")
    return rc


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
    print(f"cpu          {cfg.get('_cpu_profile')} ({cfg.get('_cpu_score')} s) "
          f"intervals x{float(cfg.get('_cpu_scale') or 1):g}")
    print(f"api          {cfg.get('api_host')}:{cfg.get('api_port')}")
    if st.get("last_event"):
        ago = st.get("last_event_s_ago")
        when = f"{ago}s ago" if isinstance(ago, int) else "recently"
        print(f"event        {st['last_event'][:58]} ({when})")
    armed = []
    if cfg.get("level_auto"):
        armed.append("level")
    if cfg.get("watchdog", True):
        armed.append("watchdog")
    if cfg.get("cable_watch", True):
        armed.append("cable")
    if cfg.get("prune_auto") and int(cfg.get("retention_days") or 0) > 0:
        armed.append("prune")
    if cfg.get("decode_auto"):
        armed.append("sweep")
    print(f"automatic    {', '.join(armed) if armed else 'none'}")
    if decode_sweep_due(cfg, p):
        print("             decode-depth re-sweep is due: tune --decoder --apply")
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
    if getattr(args, "bootstrap", False):
        return _clock_bootstrap(args)
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


def _clock_bootstrap(args) -> int:
    """Phase-lock to the 15 s frame grid without needing a decode."""
    seconds = float(getattr(args, "seconds", None) or 20)
    cfg = config.load()
    res = burst.measure(seconds=seconds, source=config.input_device(cfg))
    if res["phase"] is None:
        print(f"only {res['windows']} windows of audio - is anything reaching "
              f"the cable? (doctor)")
        return 1
    ratio = res.get("ratio") or 0.0
    print(f"windows      {res['windows']} in {seconds:g}s")
    print(f"phase        {res['phase']:+.2f}s off the 15 s boundaries")
    print(f"structure    {ratio:.2f}x frame-vs-gap")
    if ratio < 1.2 and not getattr(args, "force", False):
        print("no clear 15 s structure - this needs one strong, isolated signal.")
        print("On a busy or noisy band it cannot work; set the clock by hand, or")
        print("retry when a nearby station is the only thing on the frequency.")
        return 2
    print(f"correction   {res['correction']:+.2f}s to the system clock")
    print("note         includes roughly a second of audio pipeline delay")
    if args.set:
        ok, note = clock.apply_system(float(res["correction"]), dry_run=args.dry_run)
        print(("ok           " if ok else "failed       ") + note)
        return 0 if ok else 1
    print("note         add --set to apply it (needs root)")
    return 0


def _want_monitor(args, cfg) -> bool:
    """Tri-state: --monitor, --no-monitor, or whatever the config says."""
    if getattr(args, "no_monitor", False):
        return False
    if getattr(args, "monitor", False):
        return True
    return bool(cfg.get("monitor", True))


def cmd_cable(args):
    """Manage the virtual audio cable: up, down, status."""
    p = config.ensure_dirs()
    sf = os.path.join(p["run"], "pulse-modules.json")
    cfg = config.load()
    if args.action == "up":
        want = _want_monitor(args, cfg)
        if want and pulse._tap_active():
            print("the copy loop is on (your speakers -> the cable), so monitoring")
            print("would feed the cable back into your speakers.")
            print("pick one: 'js8call-logger route-audio --stop-copy' to remove it, "
                  "or 'js8call-logger cable up --no-monitor'.")
            return 2
        removed = [] if want else pulse.stop_monitor(sf)
        channels = max(1, int(getattr(args, "channels", 1) or 1))
        done = {}
        for ch in range(1, channels + 1):
            done = pulse.ensure_cable(state_file=sf, monitor=(want and ch == 1),
                                      rate=int(cfg.get("cable_rate") or 48000), channel=ch)
            print(f"virtual cable {ch}: sink '{pulse.sink_name(ch)}' -> "
                  f"source '{pulse.source_name(ch)}'")
        if bool(cfg.get("monitor", True)) != want:
            cfg["monitor"] = want
            config.save(cfg)
            print(f"config: monitor={str(want).lower()}")
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
        if int(cfg.get("channels") or 1) >= 1 and bool(cfg.get("monitor", True)) != (not args.no_monitor):
            cfg["monitor"] = not args.no_monitor
            config.save(cfg)
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
        # Remember it whether or not we had to create it: "already looped" is
        # exactly the case where a reboot would otherwise lose the loop.
        loops = list(cfg.get("source_loops") or [])
        if args.source not in loops:
            loops.append(args.source)
            cfg["source_loops"] = loops
            config.save(cfg)
        if created:
            print(f"line input loop on: {args.source} -> the cable")
            print("the input is still usable by other programs; nothing was moved")
            if args.source.endswith(".monitor") and pulse.cable_status().get("monitor"):
                # Our own monitor plays the cable on the speakers, and the
                # source's owner probably does the same for its cable: you
                # hear the same audio twice, a few tens of ms apart.
                print("note: monitoring is on, so you may hear this audio twice "
                      f"(the source's program is also playing it). "
                      f"Use '{APP} cable up --no-monitor' if so.")
        else:
            print(f"already looped: {args.source} is already feeding the cable")
        return 0

    if args.stop_source:
        if cfg.get("source_loops"):
            cfg["source_loops"] = []
            config.save(cfg)
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
            cfg["tap"] = True
            cfg["monitor"] = False        # required, and worth remembering
            config.save(cfg)
            print("copy on: everything your speakers play now also goes to the cable")
            print("nothing was moved, so your apps keep playing where they are")
            return 0
        print("could not create the copy loop (is the cable up?)")
        return 1

    if args.stop_copy:
        n = pulse.stop_tap(sf)
        if cfg.get("tap"):
            cfg["tap"] = False
            config.save(cfg)
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


def parse_freq(text: str) -> int:
    """Hz from '7.078', '7078', '7078.00', '7.078MHz', '7078000Hz'.

    Unit traps are the point here: a WebSDR is labelled in kHz, a band plan in
    MHz, the API in Hz, and 7041.09 is kHz even though it looks like MHz.
    """
    s = str(text).strip().replace(",", "").replace(" ", "")
    low = s.lower()
    mult = None
    for suffix, m in (("mhz", 1_000_000), ("khz", 1000), ("hz", 1)):
        if low.endswith(suffix):
            mult = m
            s = s[: -len(suffix)]
            break
    v = float(s)
    if mult is not None:
        hz = v * mult
    elif v >= 100_000:
        hz = v                    # already Hz
    elif v >= 100:
        hz = v * 1000             # kHz, how WebSDRs label the dial
    else:
        hz = v * 1_000_000        # MHz
    return int(round(hz))


def cmd_dial(args):
    """Show or set the dial stamped on new log lines, morse-logger's dial."""
    p = config.ensure_dirs()
    cfg = config.load()
    running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))
    if args.freq is None and not args.clear:
        hz = cfg.get("dial_hz")
        print(f"dial: {hz / 1e6:g} MHz" if hz else "dial: not set")
        if not running:
            print("(the logger is not running; this is the value it will use)")
        return 0
    if args.clear:
        cfg["dial_hz"] = None
        config.save(cfg)
        print("dial cleared - new lines carry whatever JS8Call reports")
        return 0
    try:
        hz = parse_freq(args.freq)
    except ValueError:
        print(f"not a frequency: {args.freq!r}")
        return 2
    if 100 <= float(str(args.freq).strip().replace(",", "")) < 100_000:
        print(f"note: {args.freq} looks like kHz - recording {hz / 1e6:g} MHz")
    cfg["dial_hz"] = hz
    config.save(cfg)

    applied = "not running (applied at next start)"
    if running:
        ports = [int(cfg.get("api_port", 2442)) + ch - 1
                 for ch in range(1, int(cfg.get("channels", 1)) + 1)]
        done = []
        for port in ports:
            client = api.Js8Api(cfg["api_host"], port)
            try:
                client.connect(2.0)
                client.set_freq(hz, args.offset)
                done.append(port)
            except (OSError, api.ApiError) as exc:
                print(f"port {port}: {exc}")
            finally:
                client.close()
        applied = ("running, applied now" if len(done) == len(ports)
                   else "running, but the API did not answer on every decoder")
    print(f"dial set to {hz / 1e6:g} MHz - {applied}")
    return 0


def _launcher_path() -> str:
    """The launcher that owns this package, not the module argv[0]."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(here, "js8call-logger")
    if os.access(cand, os.X_OK):
        return cand
    found = shutil.which("js8call-logger")
    return os.path.realpath(found) if found else cand


def cmd_service(args):
    """Install a systemd user unit so the logger comes back after a reboot."""
    if not service.available():
        print("no reachable user systemd on this machine - keep starting it "
              "by hand, or use a cron @reboot line")
        return 2
    if args.action == "status":
        ok, out = service.status()
        print(out or f"{service.NAME}: no output")
        return 0 if ok else 1
    if args.action == "remove":
        ok, out = service.remove()
        print(("ok      " if ok else "failed  ") + out)
        return 0 if ok else 1
    p = config.ensure_dirs()
    pid = engine.read_pid(p)
    if pid and engine._alive(pid):
        print("stopping the manually started daemon first")
        engine.stop(p)
    exec_path = _launcher_path()
    home = os.environ.get("JS8CALL_LOGGER_HOME") or None
    doc = os.path.join(os.path.dirname(exec_path), "README.md")
    ok, out = service.install(exec_path, home, doc, start=not args.no_start)
    if not ok:
        print(f"failed  {out}")
        return 1
    print(f"installed  {service.unit_path()}")
    print(f"launcher   {exec_path}")
    print(f"{'started' if not args.no_start else 'enabled'}    {service.NAME}")
    if not service.linger_enabled():
        print("note       a user unit stops when you log out; to keep logging "
              "without a session: sudo loginctl enable-linger "
              + (os.environ.get("USER") or "$USER"))
    return 0


def cmd_sdr(args):
    """Use an RTL-SDR as the input: rtl_fm | pacat into the cable."""
    p = config.ensure_dirs()
    cfg = config.load();
    changed = False
    if args.gain is not None:
        cfg["rtl_gain"] = args.gain
        changed = True
    if args.ppm is not None:
        cfg["rtl_ppm"] = args.ppm
        changed = True
    if args.serial is not None:
        cfg["rtl_serial"] = args.serial
        changed = True
    if args.state:
        cfg["source"] = "sdr" if args.state == "on" else "cable"
        changed = True
    if changed:
        config.save(cfg)
        print(f"source: {cfg.get('source')}"
              + (f"  gain {cfg['rtl_gain']}" if cfg.get("rtl_gain") else "")
              + (f"  ppm {cfg['rtl_ppm']}" if cfg.get("rtl_ppm") else "")
              + (f"  serial {cfg['rtl_serial']}" if cfg.get("rtl_serial") else ""))
        if engine.read_pid(p) and engine._alive(engine.read_pid(p)):
            print("restarting")
            return cmd_restart(argparse.Namespace(foreground=False))
        return 0
    if args.probe:
        print(sdr.probe())
        return 0
    print(f"source       {cfg.get('source')}")
    print(f"rtl_fm       {sdr.binary() or 'not installed (rtl-sdr package)'}")
    if (cfg.get("source") or "cable") == "sdr":
        try:
            print("command      " + " ".join(sdr.fm_command(cfg)))
        except sdr.SdrError as exc:
            print(f"command      cannot build it: {exc}")
    else:
        print("             turn it on with: js8call-logger sdr on")
    return 0


def cmd_input(args):
    """Show, choose or clear the capture device the decoder listens to."""
    p = config.ensure_dirs()
    cfg = config.load()
    current = config.input_device(cfg)

    if args.list or (args.device is None and not args.clear):
        for row in pulse.sources():
            name = row[1] if len(row) > 1 else row[0]
            print(f"{'*' if name == current else ' '} {name}")
        if args.device is None and not args.list:
            print(f"input: {current}"
                  + ("" if cfg.get("input_device") else " (the cable)"))
        return 0

    if args.clear:
        cfg["input_device"] = None
        config.save(cfg)
        print(f"input cleared - back to the cable source {config.input_device(cfg)}")
    else:
        name = args.device
        if not pulse.source_exists(name):
            print(f"no such capture source: {name}")
            print(f"see: {APP} input --list")
            return 2
        cfg["input_device"] = name
        config.save(cfg)
        print(f"input set to {name}")

    if engine.read_pid(p) and engine._alive(engine.read_pid(p)):
        print("restarting so the decoder opens it")
        return cmd_restart(argparse.Namespace(foreground=False))
    print("applies at the next start")
    return 0


def cmd_level(args):
    """Measure the cable's input level and trim the source volume toward target."""
    cfg = config.load()
    if getattr(args, "auto", False) or getattr(args, "no_auto", False):
        cfg["level_auto"] = bool(args.auto)
        config.save(cfg)
        print(f"level auto-trim {'on' if args.auto else 'off'}"
              + (" - the daemon will re-check every "
                 f"{cfg.get('level_auto_interval_s', 20)}s" if args.auto else ""))
        if not args.set:
            return 0
    target = args.target if args.target is not None else float(
        cfg.get("level_target_db", -14.0))
    step_db = float(cfg.get("level_max_step_db", 6.0))
    src = config.input_device(cfg, getattr(args, "channel", 1))
    if not pulse.source_exists(src):
        print(f"no cable source {src} - run 'js8call-logger cable up' first")
        return 2

    sample = pulse.capture_samples(args.seconds, rate=8000, source=src)
    if not sample:
        print(f"could not capture audio from {src}")
        return 2
    db = pulse.peak_dbfs(sample)
    vol = pulse.source_volume(src)
    verdict = ("too quiet" if db < -20 else "clipping" if db > -10
               else "ok")
    print(f"level: {db} dBFS  volume {vol:.0f}%  target {target:g} dBFS  [{verdict}]"
          if vol is not None else
          f"level: {db} dBFS  volume unknown  target {target:g} dBFS  [{verdict}]")
    if db <= -80:
        print("  digital silence - the streams are open but delivering zeros; "
              "fix the source, do not raise the volume")
        return 0

    plan = pulse.trim_volume(db, vol, target_db=target, max_step_db=step_db)
    if plan["action"] == "none":
        print(f"  {plan['reason']}")
        return 0
    print(f"  {plan['action']} by {abs(plan['delta_db']):.1f} dB -> "
          f"{plan['percent']:.0f}%")
    if args.dry_run:
        print("  dry run - nothing was changed")
        return 0
    if not args.set:
        print(f"  nothing changed - add --set to apply it")
        return 0
    if not pulse.set_source_volume(src, plan["percent"]):
        print(f"  could not set the volume on {src}")
        return 2
    print(f"  volume set to {plan['percent']:.0f}% - re-run 'level' to confirm")
    return 0


def cmd_tune_decoder(args):
    """Measure decodes against CPU for each decode-depth profile."""
    p = config.ensure_dirs()
    cfg = config.load()
    levels = tuple(int(x) for x in str(args.levels).replace(" ", "").split(",") if x)
    runs = max(1, int(args.runs))
    if args.dry_run:
        print("would test, in this order:")
        for name in tune.PROFILES:
            off = [k for k, v in tune.PROFILES[name].items() if not v]
            print(f"  {name:<8} {'all passes on' if not off else 'off: ' + ', '.join(off)}")
        print(f"levels       {', '.join(f'{lv:+d} dB' for lv in levels)}")
        print(f"repetitions  {runs} per level, {runs * len(levels)} transmissions each")
        print("cost         roughly {} min; the logger is stopped meanwhile".format(
            max(1, round((len(tune.PROFILES) * (25 + 15 * runs * len(levels))) / 60))))
        return 0

    was_running = bool(engine.read_pid(p) and engine._alive(engine.read_pid(p)))
    loops = list(cfg.get("source_loops") or [])
    sf = os.path.join(p["run"], "pulse-modules.json")
    if was_running:
        print("stopping the logger for a clean measurement")
        engine_rc = cmd_stop(argparse.Namespace())
        if engine_rc not in (0, None):
            return engine_rc
    if loops:
        # The share loop keeps playing live audio into the cable; the fixture
        # would be decoded against that noise instead of against silence.
        print(f"pausing {len(loops)} source loop(s) while measuring")
        pulse.stop_source_loops(state_file=sf)
    try:
        results = tune.sweep(cfg, levels=levels, runs=runs)
    finally:
        for src in loops:
            try:
                pulse.loop_source(src, state_file=sf)
            except pulse.PulseError as exc:
                print(f"could not restore source loop {src}: {exc}")
        if was_running:
            cmd_start(argparse.Namespace(foreground=False, quiet=True))
            print("logger started again")

    try:
        with open(os.path.join(p["run"], "decode-sweep.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"last": time.time(), "levels": levels, "runs": runs}, f)
    except OSError:
        pass

    print()
    print(tune.table(results))
    best = tune.pick(results)
    if not best:
        print("no results")
        return 1
    print()
    print(f"winner       {best['profile']}")
    if args.apply:
        cfg["decode_env"] = {k: bool(best["passes"].get(k, True))
                             for k in tune.PASSES}
        config.save(cfg)
        off = [k for k, v in cfg["decode_env"].items() if not v]
        print("saved        decode_env" + (f" (off: {', '.join(off)})" if off else " (all on)"))
        print("             applies at the next start")
    else:
        print("not saved    re-run with --apply to keep this profile")
    return 0


def cmd_migrate(args):
    """Fold legacy flat GROUP*.txt conversation files into groups/."""
    p = config.ensure_dirs()
    if engine.read_pid(p) and engine._alive(engine.read_pid(p)) and not args.force:
        print("the logger is running; stop it first, or use --force "
              "(a running daemon holds the conversation files open)")
        return 2
    report = migrate.fold_legacy_groups(p, dry_run=args.dry_run)
    print(migrate.report_text(report, args.dry_run))
    return 0


def cmd_prune(args):
    """Archive (or delete) logs older than N days. Off unless asked."""
    p = config.ensure_dirs()
    cfg = config.load(create=False)
    days = args.days if args.days is not None else int(cfg.get("retention_days") or 0)
    if days <= 0:
        print("retention is off: pass --days N, or set retention_days in config.json")
        return 2
    if engine.read_pid(p) and engine._alive(engine.read_pid(p)) and not args.force:
        print("the logger is running; stop it first, or use --force "
              "(a running daemon holds the JSONL open)")
        return 2
    result = retention.prune(p, days, dry_run=args.dry_run, delete=args.delete)
    print(retention.summary_text(result))
    return 0


def decode_sweep_due(cfg: dict, p: dict) -> bool:
    """True when a decode-depth re-sweep is older than decode_auto_days."""
    return tune.due(cfg, p["home"])[0]


def cmd_tune(args):
    if getattr(args, "auto", False) or getattr(args, "no_auto", False):
        cfg = config.load()
        cfg["decode_auto"] = bool(args.auto)
        config.save(cfg)
        if args.auto:
            print(f"decode auto-tune on - a re-sweep is suggested every "
                  f"{cfg.get('decode_auto_days', 14)} days; doctor will say when "
                  "it is due. It is not run unattended: the sweep needs the "
                  "cable to itself and a quiet band.")
        else:
            print("decode auto-tune off")
        if not getattr(args, "decoder", False):
            return 0
    if getattr(args, "decoder", False):
        return cmd_tune_decoder(args)
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


def cmd_doctor(args):
    """Report on the whole installation, like morse-logger's doctor.

    Sections matter: a flat list hides the difference between "the decoder is
    missing" and "audio is arriving but nothing is logging". Only [FAIL] lines
    affect the exit status - [!!] is a warning, [--] is information.
    """
    p = config.ensure_dirs()
    cfg = config.load()
    print(f"{APP} {__version__}  home {p['home']}")
    if getattr(args, "benchmark", False):
        from . import cpu as _cpu
        score, profile = _cpu.detect(p["home"], force=True)
        print(f"benchmark    {score} s - profile {profile} (re-measured)")
    prof = cfg.get("_cpu_profile")
    print("\nMachine")
    print(f"  [ok] cpu profile {prof} - benchmark {cfg.get('_cpu_score')} s, "
          f"intervals x{float(cfg.get('_cpu_scale') or 1):g}")
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
    dev = config.input_device(cfg)
    if cfg.get("input_device"):
        if pulse.source_exists(dev):
            chk("input device", True, dev)
        else:
            warn("input device", f"{dev} is configured but not present - "
                 f"run: {APP} input --list (falling back to nothing)")
    else:
        note("input device", f"{dev} (the cable)")

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
        if not cfg.get("monitor"):
            stray = pulse.monitor_loops_to_default()
            if stray:
                warn("a cable monitor loopback is playing to your speakers",
                     "that is the echo - remove it: " + APP +
                     " cable up --no-monitor")
            else:
                print("  [ok] no monitor loopback into your speakers")
        else:
            print("  [--] monitoring on - the cable also plays to your speakers")
        if not rows:
            note("nothing is playing into the cable yet")
            # Audio can be playing and still be invisible here: it is going to
            # the speakers, or - the confusing case - into another tool's
            # cable. Name where it actually is, or the message reads as "your
            # browser is not playing".
            share = None
            for r in elsewhere[:6]:
                who = r.get("app") if r.get("app") != "?" else r.get("stream")
                sink = r.get("sink_name") or "?"
                other_cable = sink.endswith("_cable")
                print(f"  [!!] {who or '?'} is playing on {sink}"
                      + ("  (another logger's cable)" if other_cable else ""))
                if other_cable and not share:
                    share = sink
            if share:
                # Moving it would fight that program's own routing watch.
                print(f"       share it instead of moving it: "
                      f"{APP} route-audio --source {share}.monitor")
            elif elsewhere:
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
            sample = pulse.capture_samples(
                1.0, rate=8000, source=config.input_device(cfg))
            if not any(sample):
                warn("digital silence", "the streams are open but delivering zeros - "
                     "the application has lost the audio device; restart it fully "
                     "(a tab reload is not enough)")
            else:
                db = pulse.peak_dbfs(sample)
                vol = pulse.source_volume(config.input_device(cfg))
                at = f" at volume {vol:.0f}%" if vol is not None else ""
                if db < -30:
                    warn(f"cable level {db} dBFS{at}", "far too quiet to decode; raise the "
                         f"volume (aim for -20 to -10 dBFS), or let it be trimmed: "
                         f"{APP} level --set")
                elif db > -1.5:
                    warn(f"cable level {db} dBFS{at}", "clipping; lower the volume, or "
                         f"let it be trimmed: {APP} level --set")
                else:
                    print(f"  [ok] cable level {db} dBFS{at}")
        if real and not running:
            warn("nothing is logging", f"{len(real)} stream(s) on the cable but the "
                 f"logger is not running ({APP} start)")
    except pulse.PulseError as exc:
        note("could not inspect", str(exc))

    print("\nAutomatic")
    auto = []
    if cfg.get("level_auto"):
        auto.append(f"level trim every {cfg.get('level_auto_interval_s', 20)}s")
    if cfg.get("watchdog", True):
        auto.append(f"decoder restart after {cfg.get('watchdog_silence_s', 300)}s "
                    "of audio with no decodes")
    if cfg.get("cable_watch", True):
        auto.append("cable re-created when the sound server loses it")
    if cfg.get("prune_auto") and int(cfg.get("retention_days") or 0) > 0:
        auto.append(f"prune {cfg['retention_days']} days old, at most every "
                    f"{cfg.get('prune_interval_h', 24)}h")
    if cfg.get("decode_auto"):
        st_tune, why_tune = tune.due(cfg, p["home"])
        auto.append(f"decode sweep every {cfg.get('decode_auto_days', 14)}d "
                    f"({why_tune})")
    if (cfg.get("source") or "cable") == "sdr":
        auto.append("rtl_fm supervised, restarted with backoff")
    if auto:
        for a in auto:
            note(a)
    else:
        note("nothing automatic is armed")
    if decode_sweep_due(cfg, p):
        warn("decode-depth re-sweep is due",
             f"run: {APP} tune --decoder --apply")

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
            if not st.get("decodes"):
                print("       nothing decoded yet - if the clock is more than "
                      "2 s out, run: js8call-logger clock --bootstrap")
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
        fails += _selftest_live(chk, getattr(args, "recording", None),
                                getattr(args, "expect", None))
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


def _similarity(expected: str, got: str) -> float:
    """How much of the expected text came back, 0..1 (morse-logger's measure)."""
    import difflib

    def norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9 ]+", "", (s or "").upper()).strip()

    a, b = norm(expected), norm(got)
    if not a:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _play_file(path: str, sink: str) -> None:
    """Play a wav or raw s16 file into the cable, by preference with pw-play."""
    exe = shutil.which("pw-play")
    if exe and path.lower().endswith(".wav"):
        subprocess.run([exe, "--target=" + sink, path], check=True)
        return
    import wave

    rate, data = 48000, None
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as w:
            rate, data = w.getframerate(), w.readframes(w.getnframes())
    with open(path, "rb") as f:
        subprocess.run(["pacat", "--device=" + sink, f"--rate={rate}",
                        "--channels=1", "--format=s16le", "--raw"],
                       input=(data if data is not None else f.read()), check=True)


def _last_text(p: dict, expect: str | None = None, tail: int = 25) -> str:
    """Best matching text among the newest records.

    With a whole recording replayed, several frames land at once; judging the
    single newest line would depend on arrival order.
    """
    texts: list[str] = []
    try:
        with open(os.path.join(p["logs"], "js8call-logger.jsonl"),
                  encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    texts.append(line)
    except OSError:
        return ""
    best, best_ratio = "", -1.0
    for line in texts[-tail:]:
        try:
            text = json.loads(line).get("text") or ""
        except ValueError:
            continue
        ratio = _similarity(expect, text) if expect else 0.0
        if ratio > best_ratio:
            best, best_ratio = text, ratio
    return best


def _selftest_live(chk, recording: str | None = None, expect: str | None = None) -> int:
    """Play a known JS8 frame into the cable and wait for it to be logged.

    This is the check that proves the whole chain: the bundled decoder runs,
    the cable carries audio, the API answers and the record reaches the log.
    It starts the logger if it is not already running, and stops it again
    afterwards, so a fresh install can be verified with one command.
    """
    cfg = config.load(create=False)
    p = config.ensure_dirs()
    fixture = recording or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "fixtures", "n0call-cq.raw")
    if not os.path.exists(fixture):
        chk("live decode", False, f"fixture missing: {fixture}")
        return 1
    started = _start_logger()
    loops = []
    if recording:
        # Give the replay the cable to itself; mixed with a live feed the
        # frames interfere and nothing decodes.
        loops = list(cfg.get("source_loops") or [])
        if loops:
            try:
                pulse.stop_source_loops()
            except Exception:                         # noqa: BLE001
                pass
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
        _play_file(fixture, pulse.sink_name(1))
        deadline = time.time() + 60
        best, ratio = "", -1.0
        while time.time() < deadline:
            if int(engine.read_status(p).get("decodes") or 0) > before:
                best = _last_text(p, expect)
                ratio = _similarity(expect, best) if expect else 1.0
                if ratio >= 0.5:
                    break
            time.sleep(1)
        if expect:
            chk("live decode", ratio >= 0.5,
                f"{ratio:.0%} of expected: {best[:40]!r}")
            return 0 if ratio >= 0.5 else 1
        if best:
            chk("live decode", True, f"decoded: {best[:40]!r}")
            return 0
        chk("live decode", False,
            "nothing decoded in 30 s (audio path or clock; see --js8call-debug)")
        return 1
    finally:
        for src in loops:
            try:
                pulse.loop_source(src)
            except Exception:                         # noqa: BLE001
                pass
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

    sp = sub.add_parser("restart", help="stop and start again, keeping the dial")
    sp.set_defaults(func=cmd_restart)

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
    sp.add_argument("--monitor", action="store_true",
                    help="play the cable to your speakers (the saved default)")
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

    sp = sub.add_parser("tune", help="change the dial, or measure decoder depth")
    sp.add_argument("--dial", type=int)
    sp.add_argument("--offset", type=int, default=1500)
    sp.add_argument("--channel", type=int, default=1,
                    help="which decoder (default 1)")
    sp.add_argument("--decoder", action="store_true",
                    help="sweep decode-depth profiles, report decodes vs CPU")
    sp.add_argument("--runs", type=int, default=2,
                    help="repetitions per level (default 2)")
    sp.add_argument("--levels", default="0,-14,-18",
                    help="comma-separated attenuation in dB (default 0,-14,-18)")
    sp.add_argument("--apply", action="store_true",
                    help="save the winning profile to config.json")
    sp.add_argument("--dry-run", action="store_true",
                    help="show the plan without starting anything")
    sp.add_argument("--auto", action="store_true",
                    help="remind when a re-sweep is due (decode_auto)")
    sp.add_argument("--no-auto", action="store_true",
                    help="stop reminding about decode-depth re-sweeps")
    sp.set_defaults(func=cmd_tune)

    sp = sub.add_parser("service", help="run the logger as a systemd user service")
    sp.add_argument("action", nargs="?", default="install",
                    choices=["install", "remove", "status"])
    sp.add_argument("--no-start", action="store_true")
    sp.set_defaults(func=cmd_service)

    sp = sub.add_parser("migrate",
                        help="fold legacy GROUP*.txt files into conversations/groups/")
    sp.add_argument("--dry-run", action="store_true", help="say what would move")
    sp.add_argument("--force", action="store_true",
                    help="allow it while the logger is running")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("prune", help="archive or delete logs older than N days")
    sp.add_argument("--days", type=int, help="age in days (default: retention_days)")
    sp.add_argument("--delete", action="store_true",
                    help="remove instead of archiving under <home>/archive")
    sp.add_argument("--dry-run", action="store_true", help="say what would go")
    sp.add_argument("--force", action="store_true",
                    help="allow pruning while the logger is running")
    sp.set_defaults(func=cmd_prune)

    sp = sub.add_parser("sdr", help="use an RTL-SDR as the input (rtl_fm | pacat)")
    sp.add_argument("state", nargs="?", choices=["on", "off"])
    sp.add_argument("--probe", action="store_true", help="ask rtl_test what it sees")
    sp.add_argument("--gain", help="rtl_fm -g, e.g. 49.6")
    sp.add_argument("--ppm", help="rtl_fm -p, frequency correction")
    sp.add_argument("--serial", help="rtl_fm -d, when several sticks are plugged in")
    sp.set_defaults(func=cmd_sdr)

    sp = sub.add_parser("input", help="show, choose or clear the capture device")
    sp.add_argument("device", nargs="?", help="capture source name, e.g. alsa_input.usb-...")
    sp.add_argument("--list", action="store_true", help="list capture sources")
    sp.add_argument("--clear", action="store_true", help="go back to the cable")
    sp.set_defaults(func=cmd_input)

    sp = sub.add_parser("level", help="measure or trim the input level")
    sp.add_argument("--set", action="store_true",
                    help="apply the correction (default: only report it)")
    sp.add_argument("--target", type=float, default=None,
                    help="target level in dBFS (default: config, -14)")
    sp.add_argument("--channel", type=int, default=1,
                    help="cable channel to measure (default: 1)")
    sp.add_argument("--seconds", type=float, default=2.0,
                    help="how long to sample (default: 2)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what would change, change nothing")
    sp.add_argument("--auto", action="store_true",
                    help="let the daemon keep the level trimmed (watchdog)")
    sp.add_argument("--no-auto", action="store_true",
                    help="stop the daemon trimming the level")
    sp.set_defaults(func=cmd_level)

    sp = sub.add_parser("dial", help="show or set the dial stamped on new lines")
    sp.add_argument("freq", nargs="?",
                    help="7.078 (MHz), 7078 (kHz) or 7078000 (Hz); no argument shows it")
    sp.add_argument("--offset", type=int, default=1500,
                    help="audio offset in Hz (default 1500)")
    sp.add_argument("--clear", action="store_true", help="stop stamping a dial")
    sp.set_defaults(func=cmd_dial)

    sp = sub.add_parser("clock", help="report or apply the clock offset")
    sp.add_argument("--set", action="store_true", help="write the system clock")
    sp.add_argument("--bootstrap", action="store_true",
                    help="measure the clock from burst timing, no decode needed")
    sp.add_argument("--seconds", type=float, default=20,
                    help="how long to listen for --bootstrap (default 20)")
    sp.add_argument("--force", action="store_true",
                    help="apply a bootstrap result even with weak evidence")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_clock)

    sp = sub.add_parser("doctor", help="check the environment")
    sp.add_argument("--benchmark", action="store_true",
                    help="re-measure the CPU and refresh the cached profile")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("selftest", help="check the pipeline and the decoder")
    sp.add_argument("--recording", help="replay this wav/raw instead of the fixture")
    sp.add_argument("--expect", help="text the replay should decode to")
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
