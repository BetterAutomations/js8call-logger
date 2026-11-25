"""Run JS8Call headless and feed its decodes into the log.

JS8Call is a Qt GUI program, but it decodes perfectly well with no display:
QT_QPA_PLATFORM=offscreen keeps it invisible while the waterfall, the decoder
and the API all run (verified: a replayed JS8 frame decodes and arrives as
RX.DIRECTED with no X server at all).

Everything JS8Call needs is written into its own ini on every start, so the
program is configured entirely from our config file - no first-run wizard, no
clicking. The one key that matters and is easy to miss is AcceptTCPRequests:
without it the TCP API accepts the connection, reads your commands and answers
nothing at all, with no error anywhere.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time

from . import api, clock, config, pulse, records

APP = config.APP


class EngineError(Exception):
    pass


def write_ini(cfg: dict, p: dict) -> str:
    """Write JS8Call.ini from our config; return its path."""
    conf_dir = os.path.join(p["js8call"], "config-home")
    os.makedirs(conf_dir, exist_ok=True)
    dev = config.input_device(cfg)
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
TCPServerPort={int(cfg['api_port'])}
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
    # JS8Call reads $XDG_CONFIG_HOME/JS8Call.ini, so the ini must live in the
    # same directory spawn() points the child at - not in a sibling folder.
    path = os.path.join(conf_dir, "JS8Call.ini")
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


def cable_present(cfg: dict) -> tuple[bool, str]:
    """True when the null sink and its remap source both exist."""
    st = pulse.cable_status()
    if st["sink"] and st["source"]:
        return True, ""
    missing = []
    if not st["sink"]:
        missing.append(pulse.SINK)
    if not st["source"]:
        missing.append(pulse.SOURCE)
    return False, "missing audio source(s): " + ", ".join(missing)


def ensure_cable(cfg: dict, state_file: str | None = None) -> str:
    """Create the cable if missing; returns a short note when it did."""
    st = pulse.cable_status()
    if st["sink"] and st["source"]:
        return ""
    done = pulse.ensure_cable(state_file=state_file,
                              monitor=bool(cfg.get("monitor", True)),
                              rate=int(cfg.get("cable_rate") or 48000),
                              tap=bool(cfg.get("tap", False)))
    if not done["sink"] and not done["source"]:
        raise EngineError("could not create the virtual cable: "
                          f"{pulse.SINK} / {pulse.SOURCE}")
    return f"created {pulse.SINK} and {pulse.SOURCE}"


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
    """Stop a background daemon and the decoder it owns."""
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
    # a decoder left behind by a crashed daemon would hold the API port
    subprocess.run(["pkill", "-x", "JS8Call"], capture_output=True)
    try:
        os.unlink(os.path.join(p["run"], "pid"))
    except OSError:
        pass
    write_status(p, running=False, decoder_pid=None)
    return stopped


class Engine:
    def __init__(self, cfg: dict | None = None, paths: dict | None = None):
        self.cfg = cfg or config.load()
        self.p = paths or config.ensure_dirs()
        self.proc: subprocess.Popen | None = None
        self.api = api.Js8Api(self.cfg["api_host"], int(self.cfg["api_port"]))
        self.sink = records.Sink(self.p, self.cfg)
        self.deduper = records.Deduper(float(self.cfg.get("dedupe_grace_s", 2.0)))
        self.clock = clock.Tracker(int(self.cfg.get("clock_samples", 15)))
        self.start_time = time.time()
        self.restarts = 0
        self.decodes = 0
        self.last_decode: float | None = None
        self.stop_flag = False

    # -- lifecycle ----------------------------------------------------------
    def _install_signals(self):
        def _stop(_sig, _frm):
            self.stop_flag = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    def spawn(self) -> None:
        binary = find_binary(self.cfg)
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = os.path.join(self.p["js8call"], "config-home")
        env["XDG_DATA_HOME"] = os.path.join(self.p["js8call"], "data")
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("QT_LOGGING_RULES", "")
        os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
        os.makedirs(env["XDG_DATA_HOME"], exist_ok=True)
        write_ini(self.cfg, self.p)
        log = open(os.path.join(self.p["run"], "js8call.log"), "ab", buffering=0)
        self.proc = subprocess.Popen([binary], env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)

    def wait_api(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise EngineError(
                    f"JS8Call exited with {self.proc.returncode}; see "
                    f"{os.path.join(self.p['run'], 'js8call.log')}")
            try:
                self.api.connect(2.0)
                return
            except (OSError, api.ApiError):
                time.sleep(0.5)
        raise EngineError("JS8Call API did not come up within "
                          f"{timeout:.0f}s on {self.cfg['api_host']}:{self.cfg['api_port']}")

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
        note = ensure_cable(self.cfg, self.state_file)
        if note:
            print(note, flush=True)
        if pulse.cable_status().get("monitor"):
            print("monitoring on: the cable is also playing on your speakers", flush=True)
        try:
            self.spawn()
            self.wait_api()
            ver = self.api.version()
            self.api.set_freq(int(self.cfg["dial_hz"]), 1500)
            print(f"decoder ready: JS8Call {ver or '?'} on "
                  f"{self.cfg['api_host']}:{self.cfg['api_port']}", flush=True)
            write_status(self.p, running=True, decoder_pid=self.proc.pid,
                         version=ver, decodes=0, restarts=0, clock_offset=0.0)
        except Exception:
            self.shutdown()
            raise

        last_status = 0.0
        last_route = 0.0
        while not self.stop_flag:
            for frame in self._poll():
                self._handle(frame)
            if time.time() - last_route > 3:
                last_route = time.time()
                self._route_watch()
            for rec in self.deduper.flush():
                self._emit(rec)
            if self.proc.poll() is not None:
                self._restart()
            now = time.time()
            if now - last_status > 5:
                last_status = now
                self._write_runtime_status()
            time.sleep(0.05)
        self.shutdown()
        return 0

    def _poll(self) -> list[dict]:
        try:
            return self.api.read(0.4)
        except api.ApiError:
            self.api.close()
            return []

    def _handle(self, frame: dict) -> None:
        rec = records.parse_event(frame, self.clock.offset())
        if not rec:
            return
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
        self.sink.write(rec, records.render(rec, int(self.cfg.get("line_chars", 150))))

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

    def _restart(self) -> None:
        code = self.proc.returncode if self.proc else None
        self.restarts += 1
        self.api.close()
        time.sleep(min(30, 2 * self.restarts))
        self.spawn()
        try:
            self.wait_api()
            self.api.set_freq(int(self.cfg["dial_hz"]), 1500)
        except Exception as exc:                     # keep trying on the next tick
            print(f"restart failed: {exc}", file=sys.stderr, flush=True)
        with open(os.path.join(self.p["run"], "restarts.log"), "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} restart #{self.restarts} "
                    f"after exit {code}\n")

    def _write_runtime_status(self) -> None:
        write_status(
            self.p, running=True, decoder_pid=self.proc.pid if self.proc else None,
            pid=os.getpid(), decodes=self.decodes, restarts=self.restarts,
            last_decode_s_ago=(round(time.time() - self.last_decode)
                               if self.last_decode else None),
            clock_offset=round(self.clock.offset(), 2),
            clock_samples=self.clock.count(),
            api_port=int(self.cfg["api_port"]), dial_hz=int(self.cfg["dial_hz"]),
        )

    def shutdown(self) -> None:
        self.api.close()
        self.sink.close()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        write_status(self.p, running=False, decoder_pid=None)
