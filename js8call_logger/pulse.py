r"""Virtual audio cable management via pactl/parec, ported from morse-logger.

The cable is a null sink plus a remapped source: any program can play into the
sink, and the decoder captures from the source, without touching the system
default devices.

    program --play--> [ js8call-logger_cable ] --monitor--> [ ..._src ] --> decoder
                                  \--> loopback --> speakers (optional)

Two loops must never both exist. "monitor" carries cable -> speakers so you
still hear what is being decoded; "tap" carries speakers -> cable so nothing has
to be rerouted. Together they are one loop wired twice and run away to 0 dBFS,
which looks to the decoder like a strong signal that never resolves.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

SINK = "js8call-logger_cable"
SOURCE = SINK + "_src"
SINK_DESC = "JS8 logger virtual cable"
SOURCE_DESC = "JS8 logger cable source"


class PulseError(RuntimeError):
    pass


def sink_name(channel: int = 1) -> str:
    return SINK if channel <= 1 else f"{SINK}_{channel}"


def source_name(channel: int = 1) -> str:
    return SOURCE if channel <= 1 else f"{SINK}_src_{channel}"


def available() -> bool:
    return bool(shutil.which("pactl")) and bool(shutil.which("parec"))


def server_info() -> dict:
    p = _run(["pactl", "info"], check=False)
    info = {}
    for line in p.stdout.decode(errors="replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def capture_samples(seconds: float = 1.0, rate: int = 8000,
                    source: str | None = None) -> bytes:
    """Capture raw s16le mono PCM from the cable (silence detection, levels)."""
    src = source or SOURCE
    if not source_exists(src) and not src.endswith(".monitor"):
        src = f"{SINK}.monitor"
    args = ["parec", f"--device={src}", f"--rate={rate}", "--channels=1",
            "--format=s16le", "--raw", "--latency-msec=20"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        data = p.stdout.read(int(rate * 2 * seconds))
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    return data or b""


def peak_dbfs(data: bytes) -> float:
    """Peak level of s16le audio in dBFS (0 = full scale)."""
    if not data:
        return -120.0
    import array
    import math
    a = array.array("h")
    a.frombytes(data[: len(data) // 2 * 2])
    peak = max((abs(v) for v in a), default=0)
    return round(20 * math.log10(max(1, peak) / 32768.0), 1)


def _run(args, timeout=10, check=True):
    exe = shutil.which(args[0])
    if not exe:
        raise PulseError(f"'{args[0]}' not found. Install pulseaudio-utils / pipewire-pulse.")
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PulseError(f"timeout running: {' '.join(args)}") from exc
    if check and p.returncode != 0:
        raise PulseError(f"{' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
    return p


def _rows(kind: str):
    p = _run(["pactl", "list", "short", kind], check=False)
    return [line.split("\t") for line in p.stdout.decode(errors="replace").splitlines()]


def sinks():
    return _rows("sinks")


def sources():
    return _rows("sources")


def sink_inputs():
    return _rows("sink-inputs")


def sink_exists(name: str = SINK) -> bool:
    return any(len(r) > 1 and r[1] == name for r in sinks())


def source_exists(name: str = SOURCE) -> bool:
    return any(len(r) > 1 and r[1] == name for r in sources())


def default_sink_name() -> str:
    p = _run(["pactl", "get-default-sink"], check=False)
    return p.stdout.decode().strip()


def cable_channels() -> list[int]:
    names = {r[1] for r in sinks() if len(r) > 1}
    return [ch for ch in range(1, 9) if sink_name(ch) in names]


def _load_module(args, state_file=None) -> str:
    mid = _run(["pactl", "load-module", *args]).stdout.decode().strip()
    if state_file:
        data = {}
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
        data.setdefault("modules", []).append(mid)
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)
    return mid


def _forget_modules(ids, state_file):
    if not state_file or not os.path.exists(state_file):
        return
    try:
        with open(state_file) as f:
            data = json.load(f)
        data["modules"] = [m for m in data.get("modules", []) if m not in ids]
        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)
    except (OSError, ValueError):
        pass


def _module_blocks():
    p = _run(["pactl", "list", "modules"], check=False)
    out = []
    for block in p.stdout.decode(errors="replace").split("\nModule #")[1:]:
        out.append((block.split("\n", 1)[0].strip(), block))
    return out


def loopback_modules():
    """[(module id, source, sink)] for every loaded module-loopback.

    Reads the Argument line, which is where `pactl load-module` puts the
    source and sink; a block without them (older module syntax) comes back with
    None for that side rather than being guessed at.
    """
    out = []
    for mid, block in _module_blocks():
        if "module-loopback" not in block:
            continue
        src = sink = None
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("Argument:"):
                continue
            for pair in line.split(":", 1)[1].split():
                key, _, val = pair.partition("=")
                if key == "source":
                    src = val
                elif key == "sink":
                    sink = val
        out.append((mid, src, sink))
    return out


def _monitor_active() -> bool:
    return any("module-loopback" in b and f"{SINK}.monitor" in b for _m, b in _module_blocks())


def _is_tap_block(block: str) -> bool:
    """True only for the speakers->cable copy loop.

    Not every loopback into the cable is the tap: a hardware input loop, or a
    loop from another program's cable, is also "loopback -> our sink". The tap
    is specifically the default sink's monitor being copied into the cable, so
    that is what gets matched; without a known default sink fall back to the
    old, looser test.
    """
    if "module-loopback" not in block:
        return False
    if f"{SINK}.monitor" in block:          # cable -> speakers
        return False
    if not (f"sink={SINK}" in block or f"sink_name={SINK}" in block):
        return False
    m = re.search(r"(?:^|\s)source=([^\s]+)", block)
    src = m.group(1) if m else ""
    try:
        dst = default_sink_name()
    except PulseError:
        dst = ""
    if dst:
        return src == f"{dst}.monitor"
    return src.endswith(".monitor")


def _tap_active() -> bool:
    return any(_is_tap_block(b) for _m, b in _module_blocks())


def ensure_cable(state_file=None, monitor=False, rate=48000, tap=False, channel=1) -> dict:
    """Create the cable if missing. Returns what was done."""
    sink, source = sink_name(channel), source_name(channel)
    done = {"sink": False, "source": False, "monitor": False, "tap": False,
            "monitor_skipped": False, "channel": channel}
    if monitor and (tap or _tap_active()):
        monitor = False
        done["monitor_skipped"] = True
    if not sink_exists(sink):
        _load_module(["module-null-sink", f"sink_name={sink}",
                      f'sink_properties=device.description="{SINK_DESC}"',
                      f"rate={rate}"], state_file)
        done["sink"] = True
        for _ in range(20):
            if sink_exists(sink):
                break
            time.sleep(0.1)
    if not source_exists(source):
        try:
            _load_module(["module-remap-source", f"master={sink}.monitor",
                          f"source_name={source}",
                          f'source_properties=device.description="{SOURCE_DESC}"'],
                         state_file)
            done["source"] = True
        except PulseError:
            done["source"] = False       # the .monitor source still works
    if tap and not _tap_active():
        dst = default_sink_name()
        if dst and dst != sink:
            _load_module(["module-loopback", f"source={dst}.monitor", f"sink={sink}",
                          "latency_msec=40", "source_dont_move=true",
                          "sink_dont_move=true"], state_file)
            done["tap"] = True
    if monitor and not _monitor_active():
        dst = default_sink_name()
        if dst and dst != sink:
            _load_module(["module-loopback", f"source={sink}.monitor", f"sink={dst}",
                          "latency_msec=60"], state_file)
            done["monitor"] = True
    return done


def stop_monitor(state_file=None):
    killed = [m for m, b in _module_blocks()
              if "module-loopback" in b and f"{SINK}.monitor" in b]
    for mid in killed:
        _run(["pactl", "unload-module", mid], check=False)
    _forget_modules(killed, state_file)
    return killed


def stop_tap(state_file=None):
    killed = [m for m, b in _module_blocks() if _is_tap_block(b)]
    for mid in killed:
        _run(["pactl", "unload-module", mid], check=False)
    _forget_modules(killed, state_file)
    return killed


def modules_for_channel(channel: int):
    names = [sink_name(channel), sink_name(channel) + ".monitor", source_name(channel)]
    out = []
    for mid, block in _module_blocks():
        for name in names:
            if re.search(r"(?:sink_name|source_name|master|sink|source)=%s(?:\s|$)"
                         % re.escape(name), block):
                out.append(mid)
                break
    return out


def cable_down(state_file=None, channel=None):
    removed = stop_monitor(state_file) + stop_tap(state_file)
    targets = [channel] if channel else (cable_channels() or [1])
    for ch in targets:
        for mid in modules_for_channel(ch):
            _run(["pactl", "unload-module", mid], check=False)
            removed.append(mid)
    _forget_modules(removed, state_file)
    if state_file and os.path.exists(state_file):
        try:
            os.unlink(state_file)
        except OSError:
            pass
    return removed


def source_names():
    """Hardware capture devices worth looping: not monitors, not our cable."""
    out = []
    for r in sources():
        if len(r) < 2:
            continue
        name = r[1].strip()
        if (not name or name.endswith(".monitor") or name == SOURCE
                or name == SINK + ".monitor" or "cable" in name.lower()):
            continue
        out.append(name)
    return out


def monitor_sources():
    """Other programs' outputs (monitors), excluding our own cable.

    Looping one of these into our cable is how two decoders share one audio
    stream without either of them stealing it from the other.
    """
    out = []
    for r in sources():
        if len(r) < 2:
            continue
        name = r[1].strip()
        if not name.endswith(".monitor"):
            continue
        if name in (SINK + ".monitor", SOURCE) or "js8call-logger" in name:
            continue
        out.append(name)
    return out


def loop_source(name: str, state_file=None, latency_ms=40) -> bool:
    if name not in source_names() and name not in monitor_sources():
        raise PulseError(f"no such capture source: {name}\n"
                         "see 'js8call-logger route-audio --list-sources'")
    for _mid, block in _module_blocks():
        if (f"source={name}" in block and f"sink={SINK}" in block
                and "module-loopback" in block):
            return False
    _load_module(["module-loopback", f"source={name}", f"sink={SINK}",
                  f"latency_msec={latency_ms}", "source_dont_move=true",
                  "sink_dont_move=true"], state_file)
    return True


def stop_source_loops(state_file=None):
    killed = []
    for mid, block in _module_blocks():
        if "module-loopback" not in block or f"sink={SINK}" not in block:
            continue
        m = re.search(r"(?:^|\s)source=([^\s]+)", block)
        if not m:
            continue
        try:
            dst = default_sink_name()
        except PulseError:
            dst = ""
        if dst and m.group(1) == f"{dst}.monitor":
            continue                     # the speakers tap, not a source loop
        _run(["pactl", "unload-module", mid], check=False)
        killed.append(mid)
    _forget_modules(killed, state_file)
    return killed


def parse_sink_inputs(text: str, sink_names: dict | None = None) -> list[dict]:
    """Parse `pactl list sink-inputs` into [{index, sink, sink_name, app, stream}]."""
    sink_names = sink_names or {}
    out = []
    for block in text.split("Sink Input #")[1:]:
        idx = block.split("\n", 1)[0].strip()
        info = {"index": int(idx) if idx.isdigit() else idx, "sink": "?",
                "sink_name": "?", "app": "?", "stream": ""}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("Sink:"):
                info["sink"] = line.split(":", 1)[1].strip()
                info["sink_name"] = sink_names.get(info["sink"], info["sink"])
            elif line.startswith("application.name ="):
                info["app"] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("media.name ="):
                info["stream"] = line.split("=", 1)[1].strip().strip('"')
        out.append(info)
    return out


def sink_input_details():
    p = _run(["pactl", "list", "sink-inputs"], check=False)
    return parse_sink_inputs(p.stdout.decode(errors="replace"),
                             {r[0]: r[1] for r in sinks() if len(r) > 1})


def is_own_loopback(row) -> bool:
    """Our own loopback streams must never be moved into the cable."""
    return (row.get("stream") or "").startswith("loopback-")


def route_sink_inputs(target: str = SINK, indices=None):
    moved, skipped = [], []
    for row in sink_input_details():
        idx = str(row["index"])
        label = row["app"] if row["app"] != "?" else row["stream"]
        if indices and idx not in [str(i) for i in indices]:
            continue
        if is_own_loopback(row):
            skipped.append((idx, label, "js8call-logger's own monitor loopback"))
            continue
        if row.get("sink_name") == target:
            skipped.append((idx, label, "already on the cable"))
            continue
        p = _run(["pactl", "move-sink-input", idx, target], check=False)
        if p.returncode == 0:
            moved.append((idx, label))
    return moved, skipped


def route_new_streams(target: str = SINK, apps=(), known=()):
    """Re-route streams from remembered apps.

    Deliberately re-asserts on every poll rather than remembering what was
    already moved: the destination is not sticky, and another program (or the
    user's own mixer) can move a stream away at any time. `known` is returned
    for callers that want to report first sightings, but it must not suppress
    the move - doing that once here meant a stream moved back by a second
    logger was never recovered, and `route-audio --all` looked like it did not
    stick.
    """
    moved = []
    apps = {a for a in apps if a}
    for row in sink_input_details():
        idx = str(row["index"])
        if row.get("sink_name") == target or is_own_loopback(row):
            continue
        app = row["app"] if row["app"] != "?" else row["stream"]
        if app not in apps:
            continue
        p = _run(["pactl", "move-sink-input", idx, target], check=False)
        if p.returncode == 0:
            moved.append((idx, app))
            known.add(idx)
    return moved, known


def source_volume(name: str = SOURCE) -> float | None:
    """Current source volume as a percentage, or None if it cannot be read."""
    p = _run(["pactl", "get-source-volume", name], check=False)
    if p.returncode != 0:
        return None
    m = re.search(r"front-left: \d+ / (\d+)%", p.stdout.decode(errors="replace"))
    if not m:
        m = re.search(r"/ +(\d+)%", p.stdout.decode(errors="replace"))
    return float(m.group(1)) if m else None


def set_source_volume(name: str, percent: float) -> bool:
    """Set a source's volume; clamped to 0-100% so we never amplify past unity."""
    pct = max(0, min(100, int(round(percent))))
    p = _run(["pactl", "set-source-volume", name, f"{pct}%"], check=False)
    return p.returncode == 0


def level_verdict(db: float, sdr: bool = False) -> str:
    """One word for the measured level.

    A low reading means two different things. Behind a WebSDR or a line-in it
    means the source volume is down, which the user can fix. Behind `rtl_fm`
    there is no volume: the demodulator's output is proportional to what is in
    the passband at a fixed gain, so a low peak is simply nothing being there -
    an empty band, not a setting to correct.
    """
    if db < -20:
        return "no signal in the passband" if sdr else "too quiet"
    if db > -10:
        return "clipping"
    return "ok"


def trim_volume(measured_db: float | None, current_percent: float | None,
                target_db: float = -14.0, max_step_db: float = 6.0,
                deadband_db: float = 2.0,
                at_full_hint: str = "raise the volume in the source application") -> dict:
    """Work out a new volume percentage to move a measured level toward target.

    Pure arithmetic, so it can be tested without a sound server. The measured
    level already includes the current volume, so the correction is a plain
    ratio in dB, capped at max_step_db so repeated calls converge instead of
    overshooting on a noisy measurement.

    `at_full_hint` names the knob to turn when the volume is already at 100%:
    there is no source application behind an rtl_fm feed, so the caller that
    knows the source supplies the right advice.
    """
    if measured_db is None or current_percent is None:
        return {"action": "none", "percent": None, "delta_db": 0.0,
                "reason": "level or volume unknown"}
    if measured_db <= -80.0:
        return {"action": "none", "percent": current_percent, "delta_db": 0.0,
                "reason": "digital silence - raising the volume would just "
                          "amplify noise"}
    delta = target_db - measured_db
    if abs(delta) <= deadband_db:
        return {"action": "none", "percent": current_percent, "delta_db": round(delta, 1),
                "reason": "within tolerance"}
    step = max(-max_step_db, min(max_step_db, delta))
    percent = current_percent * (10 ** (step / 20.0))
    clamped = max(0.0, min(100.0, percent))
    if abs(clamped - current_percent) < 1.0:
        return {"action": "none", "percent": current_percent, "delta_db": round(delta, 1),
                "reason": (f"already at 100% - {at_full_hint}" if step > 0
                           else "already at 0%")}
    return {"action": "raise" if step > 0 else "lower",
            "percent": round(clamped, 1), "delta_db": round(delta, 1),
            "reason": "outside tolerance"}


def cable_status() -> dict:
    return {"sink": sink_exists(), "source": source_exists(),
            "monitor": _monitor_active(), "tap": _tap_active(),
            "channels": cable_channels(),
            "sinks": [r[1] for r in sinks() if len(r) > 1],
            "sources": [r[1] for r in sources() if len(r) > 1]}


def monitor_loops_to_default() -> list[int]:
    """Loopbacks playing a cable monitor into the default sink.

    The echo trap: audio reaches the speakers twice, ~100 ms apart.
    """
    default = default_sink_name()
    if not default:
        return []
    names = {sink_name(ch) + ".monitor" for ch in (cable_channels() or [1])}
    out = []

    for mid, block in _module_blocks():
        if "module-loopback" not in block:
            continue
        src = re.search(r"source=(\S+)", block)
        snk = re.search(r"sink=(\S+)", block)
        if not src or not snk:
            continue
        if src.group(1) not in names:
            continue
        if snk.group(1) == default:
            out.append(mid)
    return out
