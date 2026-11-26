"""Run one JS8Call per cable channel and feed its decodes into the log.

JS8Call is a Qt GUI program, but it decodes perfectly well with no display:
QT_QPA_PLATFORM=offscreen keeps it invisible while the waterfall, the decoder
and the API all run (verified: a replayed JS8 frame decodes and arrives as
RX.DIRECTED with no X server at all).

Everything JS8Call needs is written into its own ini on every start, so the
program is configured entirely from our config file - no first-run wizard, no
clicking. The one key that matters and is easy to miss is AcceptTCPRequests:
without it the TCP API accepts the connection, reads your commands and answers
nothing at all, with no error anywhere.

Flowgraph per channel N (N = 1 by default, more with `cable up --channels N`):

    <cable N>_src -> JS8Call offscreen (XDG dir N, port 2442+N-1) -> records
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time

from . import api, clock, config, pulse, records

APP = config.APP


class EngineError(Exception):
    pass


def channel_port(cfg: dict, channel: int) -> int:
    return int(cfg["api_port"]) + max(0, channel - 1)


def write_ini(cfg: dict, p: dict, channel: int = 1) -> str:
    """Write channel N's JS8Call.ini; return its path."""
    conf_dir = os.path.join(p["js8call"], f"ch{channel}", "config-home")
    os.makedirs(conf_dir, exist_ok=True)
    dev = pulse.source_name(channel) if channel > 1 else config.input_device(cfg)
    ini = f"""[Configuration]
MyCall={cfg['mycall']}
MyGrid={cfg['mygrid']}
DialFreq={int(cfg['dial_hz'])}
SubMode=0
SoundInName={dev}
SoundInId=@ByteArray({dev})
AudioInputChannel=Mono
AudioOutputChannel=Mono
AllowMonitorInputs=true
TCPEnabled=true
TCPServer={cfg['api_host']}
TCPServerPort={channel_port(cfg, channel)}
TCPMaxConnections=4
AcceptTCPRequests=true
WriteLogs=true
MonitorOFF=false
CheckForUpdates=false
PSKReporter=false
SpotToAPRS=false
HBInterval=0
WaterfallFPS=2

[MultiSettings]
CurrentName=Default
"""
    # JS8Call appends the rig name to its settings file, so a second instance
    # reads "JS8Call - ch2.ini", not JS8Call.ini. Writing the wrong name means
    # the instance starts with defaults (no TCP port) and looks dead.
    name = "JS8Call.ini" if channel == 1 else f"JS8Call - ch{channel}.ini"
    path = os.path.join(conf_dir, name)
    config.write_atomic(path, ini)
    return path


def find_binary(cfg: dict) -> str:
    cand = cfg.get("js8call_binary")
    if not cand:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(here, "bundle", "JS8Call.AppImage")
    if not os.path.exists(cand):
        raise EngineError(f"JS8Call binary not found: {cand}")
    if not os.access(cand, os.X_OK):
        raise EngineError(f"JS8Call binary is not executable: {cand} (chmod +x)")
    return cand


def cable_present(cfg: dict, channel: int = 1) -> tuple[bool, str]:
    """True when this channel's null sink and remap source both exist."""
    st = pulse.cable_status()
    sink, source = pulse.sink_name(channel), pulse.source_name(channel)
    if st["sink"] and st["source"] and channel == 1:
        return True, ""
    have_sink = sink in st["sinks"]
    have_source = source in st["sources"]
    if have_sink and have_source:
        return True, ""
    missing = [n for n, ok in ((sink, have_sink), (source, have_source)) if not ok]
    return False, "missing audio source(s): " + ", ".join(missing)


def ensure_cable(cfg: dict, state_file: str | None = None, channel: int = 1) -> str:
    """Create this channel's cable if missing; returns a note when it did."""
    ok, _ = cable_present(cfg, channel)
    if ok:
        return ""
    done = pulse.ensure_cable(state_file=state_file,
                              monitor=bool(cfg.get("monitor", True)) and channel == 1,
                              rate=int(cfg.get("cable_rate") or 48000),
                              tap=bool(cfg.get("tap", False)) and channel == 1,
                              channel=channel)
    if not done["sink"] and not done["source"]:
        raise EngineError(f"could not create the virtual cable: "
                          f"{pulse.sink_name(channel)} / {pulse.source_name(channel)}")
    return (f"created {pulse.sink_name(channel)} and "
            f"{pulse.source_name(channel)}")


def write_status(p: dict, **fields) -> None:
    path = os.path.join(p["run"], "status.json")
    try:
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    except (OSError, ValueError):
        cur = {}
    cur.update(fields)
    cur["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    config.write_atomic(path, json.dumps(cur, indent=2, sort_keys=True) + "\n")


def read_status(p: dict) -> dict:
    try:
        with open(os.path.join(p["run"], "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_pid(p: dict) -> int | None:
    try:
        with open(os.path.join(p["run"], "pid"), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def stop(p: dict | None = None, timeout: float = 10.0) -> bool:
    """Stop a background daemon and every decoder it owns."""
    p = p or config.paths()
    pid = read_pid(p)
    stopped = False
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline and _alive(pid):
            time.sleep(0.2)
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        stopped = True
    # decoders left behind by a crashed daemon would hold the API ports
    subprocess.run(["pkill", "-x", "JS8Call"], capture_output=True)
    try:
        os.unlink(os.path.join(p["run"], "pid"))
    except OSError:
        pass
    write_status(p, running=False, decoders=[])
    return stopped


class Decoder:
    """One JS8Call process, its API connection and its restart bookkeeping."""

    def __init__(self, cfg: dict, paths: dict, channel: int):
        self.cfg = cfg
        self.p = paths
        self.channel = channel
        self.port = channel_port(cfg, channel)
        self.proc: subprocess.Popen | None = None
        self.api = api.Js8Api(cfg["api_host"], self.port)
        self.last_connect = 0.0
        self.restarts = 0
        self.last_error = ""

    def spawn(self) -> None:
        binary = find_binary(self.cfg)
        root = os.path.join(self.p["js8call"], f"ch{self.channel}")
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = os.path.join(root, "config-home")
        env["XDG_DATA_HOME"] = os.path.join(root, "data")
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("QT_LOGGING_RULES", "")
        # JS8Call exposes its refinement passes as environment switches, not
        # settings keys; `tune --decoder` decides which ones earn their CPU.
        for name, enabled in (self.cfg.get("decode_env") or {}).items():
            key = "JS8_DISABLE_" + name.upper()
            if enabled:
                env.pop(key, None)
            else:
                env[key] = "1"
        os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
        os.makedirs(env["XDG_DATA_HOME"], exist_ok=True)
        write_ini(self.cfg, self.p, self.channel)
        logpath = os.path.join(self.p["run"], f"js8call-{self.channel}.log")
        log = open(logpath, "ab", buffering=0)
        # A second instance needs its own application name, or Qt's single
        # instance socket makes it sit in a modal "already running" box that
        # never closes offscreen - the API port then never opens.
        args = [binary] + (["-r", f"ch{self.channel}"] if self.channel > 1 else [])
        self.proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)

    def wait_api(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise EngineError(
                    f"decoder {self.channel} exited with {self.proc.returncode}; see "
                    f"{os.path.join(self.p['run'], f'js8call-{self.channel}.log')}")
            try:
                self.api.connect(2.0)
                return
            except (OSError, api.ApiError):
                time.sleep(0.5)
        raise EngineError(f"decoder {self.channel} API did not come up within "
                          f"{timeout:.0f}s on port {self.port}")

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def stop(self) -> None:
        self.api.close()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.proc.kill()
                except OSError:
                    pass

    def describe(self) -> dict:
        return {"channel": self.channel, "port": self.port,
                "pid": self.proc.pid if self.proc else None,
                "up": self.alive()}


class Engine:
    def __init__(self, cfg: dict | None = None, paths: dict | None = None):
        self.cfg = cfg or config.load()
        self.p = paths or config.ensure_dirs()
        self.sink = records.Sink(self.p, self.cfg)
        self.deduper = records.Deduper(float(self.cfg.get("dedupe_grace_s", 2.0)))
        self.clock = clock.Tracker(int(self.cfg.get("clock_samples", 15)))
        self.start_time = time.time()
        self.decodes = 0
        self.last_decode: float | None = None
        self.stop_flag = False
        self._moves: dict[str, list[float]] = {}
        self._backoff: dict[str, float] = {}
        self._warned: set[str] = set()
        n = max(1, int(self.cfg.get("channels") or 1))
        self.decoders = [Decoder(self.cfg, self.p, ch) for ch in range(1, n + 1)]

    # -- lifecycle ----------------------------------------------------------
    def _install_signals(self):
        def _stop(_sig, _frm):
            self.stop_flag = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    def run(self) -> int:
        os.makedirs(self.p["run"], exist_ok=True)
        lock_fh = open(os.path.join(self.p["run"], "daemon.lock"), "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise EngineError("another js8call-logger is already running for this home "
                              "(two daemons would fight over the API port)")
        with open(os.path.join(self.p["run"], "pid"), "w") as f:
            f.write(str(os.getpid()))
        self._install_signals()

        self.state_file = os.path.join(self.p["run"], "pulse-modules.json")
        try:
            for dec in self.decoders:
                note = ensure_cable(self.cfg, self.state_file, dec.channel)
                if note:
                    print(note, flush=True)
            self._restore_source_loops()
            for dec in self.decoders:
                dec.spawn()
            for dec in self.decoders:
                dec.wait_api()
                ver = dec.api.version()
                dec.api.set_freq(int(self.cfg["dial_hz"]), 1500)
                print(f"decoder {dec.channel} ready: JS8Call {ver or '?'} on "
                      f"{self.cfg['api_host']}:{dec.port}", flush=True)
            if pulse.cable_status().get("monitor"):
                print("monitoring on: the cable is also playing on your speakers",
                      flush=True)
            write_status(self.p, running=True, started_at=time.time(),
                         channels=len(self.decoders),
                         decoders=[d.describe() for d in self.decoders],
                         decodes=0, restarts=0, clock_offset=0.0)
        except Exception as exc:
            self.shutdown()
            write_status(self.p, running=False, error=str(exc))
            raise

        if self.cfg.get("level_auto"):
            threading.Thread(target=self._level_watch, daemon=True).start()
        last_status = 0.0
        last_route = 0.0
        while not self.stop_flag:
            for dec in self.decoders:
                for frame in self._poll(dec):
                    self._handle(frame, dec.channel)
            for rec in self.deduper.flush():
                self._emit(rec)
            for dec in self.decoders:
                if not dec.alive():
                    self._restart(dec)
            if time.time() - last_route > 3:
                last_route = time.time()
                self._route_watch()
            now = time.time()
            if now - last_status > 5:
                last_status = now
                self._write_runtime_status()
            time.sleep(0.05)
        self.shutdown()
        return 0

    def _restore_source_loops(self) -> None:
        """Re-create `route-audio --source` loops after a reboot.

        The loop modules live in the sound server, so they survive a restart of
        this daemon; they do not survive a reboot. The sources are remembered in
        config for exactly that case.
        """
        for src in list(self.cfg.get("source_loops") or []):
            try:
                created = pulse.loop_source(src, state_file=self.state_file)
                print(("source loop on: " if created else "source loop already on: ")
                      + src, flush=True)
            except pulse.PulseError as exc:
                print(f"could not restore source loop {src}: {exc}", file=sys.stderr)

    def _poll(self, dec: Decoder) -> list[dict]:
        """Read frames, re-establishing the socket when it dies.

        JS8Call drops idle TCP clients (and every decoder restart closes them),
        and a logger that keeps a dead socket looks perfectly healthy while
        logging nothing - decodes 0, "last decode never", decoder up. Retry on
        a short timer rather than in a busy loop.
        """
        if not dec.api.connected:
            now = time.time()
            if now - dec.last_connect < 2.0:
                return []
            dec.last_connect = now
            try:
                dec.api.connect(2.0)
                print(f"api connected: {self.cfg['api_host']}:{dec.port}", flush=True)
            except (OSError, api.ApiError) as exc:
                print(f"api connect failed on port {dec.port}: {exc} - retrying",
                      flush=True)
                return []
        try:
            return dec.api.read(0.05)
        except api.ApiError as exc:
            dec.api.close()
            print(f"api disconnected on port {dec.port}: {exc} - reconnecting",
                  flush=True)
            return []

    def _handle(self, frame: dict, channel: int) -> None:
        rec = records.parse_event(frame, self.clock.offset(),
                                  str(self.cfg.get("eot") or "\u2662"))
        if not rec:
            return
        rec["channel"] = channel
        if len(self.decoders) > 1:
            rec["channels_total"] = len(self.decoders)
        for ready in self.deduper.add(rec):
            self._emit(ready)

    def _emit(self, rec: dict) -> None:
        self.decodes += 1
        self.last_decode = time.time()
        # Sample the clock once per logged transmission, after dedupe: the
        # ACTIVITY/DIRECTED pair carries the same TDRIFT and counting both
        # would report twice the evidence we actually have.
        if isinstance(rec.get("tdrift"), (int, float)):
            self.clock.add(float(rec["tdrift"]))
        mode = str(self.cfg.get("expand_mode") or "gloss")
        if not self.cfg.get("expand", True):
            mode = "off"
        lines = records.render(rec, int(self.cfg.get("line_chars", 150)),
                               expand=mode,
                               expand_min_coh=self.cfg.get("expand_min_coherence"))
        self.sink.write(rec, lines)

    def _route_watch(self) -> None:
        """Keep remembered apps on the cable across reloads.

        Routing is not sticky in PipeWire: a moved stream keeps its destination
        for that stream's lifetime, so reloading the WebSDR tab drops the audio
        back to the speakers and the logger goes deaf. `route-audio` records
        the applications it saw, and this puts their new streams back.
        """
        if not self.cfg.get("route_watch", True):
            return
        # Re-read the list each time: `route-audio` may have been run while the
        # daemon is up, and a stale copy would silently stop watching.
        try:
            disk = config.load(create=False)
            self.cfg["route_apps"] = disk.get("route_apps") or []
            self.cfg["route_watch"] = disk.get("route_watch", True)
        except Exception:
            pass
        apps = self.cfg.get("route_apps") or []
        now = time.time()
        # Yield to an app we have already lost a tug of war over: repeating the
        # move made the audio flap between two cables every few seconds.
        apps = [a for a in apps if self._backoff.get(a, 0) <= now]
        if not apps:
            return
        try:
            moved, self._routed = pulse.route_new_streams(
                apps=apps, known=getattr(self, "_routed", set()))
        except Exception:                    # never kill the loop over routing
            return
        for idx, app in moved:
            print(f"route: {app} started playing (stream {idx}) - "
                  "moved it to the cable", flush=True)
            # Another logger (morse-logger, say) watching the same application
            # moves it back a fraction of a second later. Warn once, then stop
            # competing for a while instead of flapping the audio forever.
            hist = [t for t in self._moves.setdefault(app, []) if now - t <= 60]
            hist.append(now)
            self._moves[app] = hist
            if len(hist) >= 3 and app not in self._warned:
                self._warned.add(app)
                self._backoff[app] = now + 300
                print(f"route: {app} has been put back on the cable {len(hist)} times "
                      "in a minute - another program is routing it too. Sharing the "
                      "audio is the fix: "
                      f"'{config.APP} route-audio --source <other>_cable.monitor', "
                      "or turn that program's own routing watch off. Leaving it "
                      "alone for 5 minutes.", flush=True)

    def _level_watch(self) -> None:
        """Trim the cable's input volume as the source drifts.

        Runs in its own thread because a measurement takes a second of audio,
        and blocking the decode poll for that long would cost a slot.
        """
        target = float(self.cfg.get("level_target_db", -14.0))
        step = float(self.cfg.get("level_max_step_db", 6.0))
        while not self.stop_flag:
            time.sleep(20)
            if self.stop_flag:
                break
            try:
                src = pulse.source_name(1)
                if not pulse.source_exists(src):
                    continue
                db = pulse.peak_dbfs(pulse.capture_samples(1.0, rate=8000, source=src))
                plan = pulse.trim_volume(db, pulse.source_volume(src),
                                         target_db=target, max_step_db=step)
                if plan["action"] == "none":
                    continue
                if pulse.set_source_volume(src, plan["percent"]):
                    print(f"level: {db} dBFS was outside {target:g} dBFS - "
                          f"volume {plan['action']}d to {plan['percent']:.0f}%",
                          flush=True)
            except Exception:                # never kill the loop over a level
                continue

    def _restart(self, dec: Decoder) -> None:
        code = dec.proc.returncode if dec.proc else None
        dec.restarts += 1
        dec.stop()
        time.sleep(min(30, 2 * dec.restarts))
        try:
            dec.spawn()
            dec.wait_api()
            dec.api.set_freq(int(self.cfg["dial_hz"]), 1500)
        except Exception as exc:             # keep trying on the next tick
            dec.last_error = str(exc)
            print(f"restart of decoder {dec.channel} failed: {exc}",
                  file=sys.stderr, flush=True)
        with open(os.path.join(self.p["run"], "restarts.log"), "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} decoder {dec.channel} "
                    f"restart #{dec.restarts} after exit {code}\n")

    def _write_runtime_status(self) -> None:
        write_status(
            self.p, running=True, pid=os.getpid(),
            channels=len(self.decoders),
            decoders=[d.describe() for d in self.decoders],
            decodes=self.decodes,
            restarts=sum(d.restarts for d in self.decoders),
            last_decode_s_ago=(round(time.time() - self.last_decode)
                               if self.last_decode else None),
            clock_offset=round(self.clock.offset(), 2),
            clock_samples=self.clock.count(),
            api_port=int(self.cfg["api_port"]), dial_hz=int(self.cfg["dial_hz"]),
        )

    def shutdown(self) -> None:
        for dec in self.decoders:
            dec.stop()
        self.sink.close()
        write_status(self.p, running=False, decoders=[])
