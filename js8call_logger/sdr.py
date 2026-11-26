"""RTL-SDR as an input: rtl_fm -> pacat -> the cable.

The tuner is a child process with two pipes; both must be drained or it blocks
and the audio stops. Its stderr is where "No supported devices found" appears,
which on most distros means the DVB kernel driver is holding the stick.
"""

import os
import shutil
import subprocess
import threading
import time


class SdrError(RuntimeError):
    pass


DVB_HINT = ("no supported device: the kernel DVB driver is probably holding it - "
            "sudo modprobe -r dvb_usb_rtl28xxu, and blacklist it to keep it away")


def binary() -> str | None:
    return shutil.which("rtl_fm")


def probe() -> str:
    """What rtl_test says, or why it could not run."""
    exe = shutil.which("rtl_test")
    if not exe:
        return "rtl_test not installed (rtl-sdr package)"
    try:
        p = subprocess.run([exe, "-t"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"rtl_test failed: {exc}"
    out = (p.stdout + p.stderr).strip()
    return out or f"rtl_test said nothing (exit {p.returncode})"


def bias_tee(device: str | None, on: bool) -> tuple[bool, str]:
    """Power the antenna port (12 V), or stop. Needs rtl_biast."""
    exe = shutil.which("rtl_biast")
    if not exe:
        return False, "rtl_biast is not installed (it ships with rtl-sdr)"
    cmd = [exe]
    if device:
        cmd += ["-d", str(device)]
    cmd += ["-b", "1" if on else "0"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"rtl_biast failed: {exc}"
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (lines[-1] if lines else "rtl_biast failed")
    return True, ("bias tee on" if on else "bias tee off")


def fm_command(cfg: dict) -> list[str]:
    """The demodulator command line for the configured dial and stick."""
    hz = int(cfg.get("dial_hz") or 0)
    if not hz:
        raise SdrError("no dial set - run: js8call-logger dial 7.078")
    rate = int(cfg.get("rtl_sample_rate") or 240000)
    audio = int(cfg.get("cable_rate") or 48000)
    cmd = [binary() or "rtl_fm",
           "-M", "usb",                 # USB: JS8 is upper sideband
           "-f", str(hz),
           "-s", str(rate),
           "-r", str(audio),
           "-l", "0"]                   # no squelch: JS8 is below the noise
    if hz < 24000000:
        cmd += ["-E", "direct2"]         # HF needs direct sampling, Q branch
    if cfg.get("rtl_gain"):
        cmd += ["-g", str(cfg["rtl_gain"])]
    if cfg.get("rtl_ppm"):
        cmd += ["-p", str(cfg["rtl_ppm"])]
    if cfg.get("rtl_serial"):
        cmd += ["-d", str(cfg["rtl_serial"])]
    cmd.append("-")
    return cmd


class RtlSource:
    """Keep rtl_fm | pacat alive, and say why when it is not."""

    def __init__(self, cfg: dict, sink: str, log=print):
        self.cfg = cfg
        self.sink = sink
        self.log = log
        self.tuner: subprocess.Popen | None = None
        self.player: subprocess.Popen | None = None
        self.started_at = 0.0
        self.restarts = 0
        self.last_error = ""

    def running(self) -> bool:
        return bool(self.tuner and self.player
                    and self.tuner.poll() is None and self.player.poll() is None)

    def start(self) -> None:
        if not binary():
            raise SdrError("rtl_fm is not installed (rtl-sdr package)")
        self.stop()
        if self.cfg.get("rtl_bias_tee"):
            ok, msg = bias_tee(self.cfg.get("rtl_serial"), True)
            self.log(f"sdr: {msg}" if ok else f"sdr: bias tee failed - {msg}")
        rate = int(self.cfg.get("cable_rate") or 48000)
        cmd = fm_command(self.cfg)
        self.log(f"sdr: {' '.join(cmd)}")
        self.tuner = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        player_cmd = ["pacat", f"--device={self.sink}", f"--rate={rate}",
                      "--channels=1", "--format=s16le", "--latency-msec=50"]
        self.player = subprocess.Popen(player_cmd, stdin=self.tuner.stdout,
                                       stderr=subprocess.PIPE)
        self.tuner.stdout.close()          # so the player owns the read end
        self.started_at = time.time()
        threading.Thread(target=self._drain, args=(self.tuner.stderr, True),
                         daemon=True).start()
        threading.Thread(target=self._drain, args=(self.player.stderr, False),
                         daemon=True).start()

    def _drain(self, pipe, is_tuner: bool) -> None:
        """Read stderr forever: an unread pipe stops the tuner."""
        for raw in iter(pipe.readline, b""):
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if is_tuner and "No supported devices" in line:
                self.last_error = DVB_HINT
            elif is_tuner and "Failed to open" in line:
                self.last_error = line
            if is_tuner or "error" in line.lower():
                self.log(f"sdr: {line}")

    def check(self) -> str | None:
        """None while healthy, otherwise why it stopped."""
        if self.running():
            return None
        if self.last_error:
            return self.last_error
        code = None
        for proc in (self.tuner, self.player):
            if proc is not None and proc.poll() is not None:
                code = proc.returncode
                break
        return f"stopped (exit {code})" if code is not None else "not started"

    def stop(self) -> None:
        for proc in (self.player, self.tuner):
            if proc is None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        self.tuner = self.player = None
