"""Measure decode depth against CPU cost, and keep the setting that wins.

JS8Call 3.0.3 exposes its refinement passes as environment switches rather than
settings keys:

    JS8_DISABLE_DEEP_SEARCH     extra candidates searched per period
    JS8_DISABLE_LDPC_RESCUE     retry a failed frame with a different LDPC seed
    JS8_DISABLE_COHERENT_DATA   coherent re-demodulation of data frames
    JS8_DISABLE_REDEMOD         aided re-demodulation after a first decode
    JS8_DISABLE_FREQ_TRACKING   follow slow drift between frames
    JS8_DISABLE_TIMING_TRACKING follow symbol timing drift

Each costs CPU and buys sensitivity. On a fast desktop the answer is "leave
them all on"; on a Core 2 Duo the question is which ones actually earn their
keep. This module answers it by playing the same known frame at known levels
through each profile and counting how many decodes come back, with the CPU the
decoder burned while doing it.

The measurement is synthetic by design: one clean fixture, exact attenuation,
no band conditions. That makes it reproducible and good for *ranking* the
profiles, not for predicting real-world copy.
"""

from __future__ import annotations

import array
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time

from . import api, config, engine, pulse

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tests", "fixtures", "n0call-cq.raw")
MARKER = "CQ CQ CQ EM48"          # how we recognise our own frame in the log

PASSES = ("deep_search", "ldpc_rescue", "coherent_data", "redemod",
          "freq_tracking", "timing_tracking")

PROFILES = {
    "default": {},                                   # everything on
    "lean": {"deep_search": False, "ldpc_rescue": False},
    "minimal": {name: False for name in PASSES},
}


def profile_env(decode_env: dict) -> dict:
    """Environment overrides for one profile. True means "leave it enabled"."""
    env = {}
    for name in PASSES:
        enabled = bool((decode_env or {}).get(name, True))
        if not enabled:
            env["JS8_DISABLE_" + name.upper()] = "1"
    return env


def load_fixture() -> array.array:
    samples = array.array("h")
    with open(FIXTURE, "rb") as fh:
        samples.frombytes(fh.read())
    return samples


def attenuate(samples: array.array, db: float) -> array.array:
    """Scale by `db` (negative = quieter), with the same 16-bit clipping as a mixer.

    Note what this does *not* do: lowering a clean signal's amplitude does not
    make it harder to decode, because there is still no noise. For sensitivity
    testing use mix_noise(), which sets a real signal-to-noise ratio.
    """
    if db == 0:
        return samples
    factor = 10 ** (db / 20.0)
    out = array.array("h", bytes(2 * len(samples)))
    for i, value in enumerate(samples):
        out[i] = max(-32768, min(32767, int(value * factor)))
    return out


def mix_noise(samples: array.array, snr_db: float, rate: int = 48000,
              bandwidth: int = 2500, seed: int = 20261125) -> array.array:
    """Add white noise so the in-band SNR is `snr_db`, without clipping.

    A JS8 signal is about 2.5 kHz wide, so the noise power inside that window -
    not across Nyquist - is what the decoder sees. The mix is deterministic for
    a given seed, so repeated runs are comparable.
    """
    import math
    import random

    n = len(samples)
    if not n:
        return samples
    signal_power = sum(v * v for v in samples) / n
    in_band_noise = signal_power / (10 ** (snr_db / 10.0))
    # Spread that across the whole band, since the noise is white.
    total_noise = in_band_noise * (rate / 2.0) / bandwidth
    sd = math.sqrt(total_noise)
    rng = random.Random(seed)
    mixed = [samples[i] + rng.gauss(0.0, sd) for i in range(n)]
    peak = max(abs(v) for v in mixed) or 1.0
    scale = min(1.0, 0.9 * 32767 / peak)
    out = array.array("h", bytes(2 * n))
    for i, v in enumerate(mixed):
        out[i] = max(-32768, min(32767, int(v * scale)))
    return out


def score(results: list[dict], cpu_weight: float = 0.15) -> list[dict]:
    """Rank profiles: sensitivity first, CPU as a tie-breaker.

    Each result gains `rate` (decodes per transmission, averaged over levels)
    and `score`. cpu_weight is how much a full 100 % of one core costs: at the
    default, a profile burning 10 % more CPU must recover ~1.5 % more decodes
    to be worth choosing.
    """
    for row in results:
        runs = max(1, row["runs"])
        row["rate"] = row["decodes"] / runs
        row["score"] = row["rate"] - cpu_weight * (row["cpu_pct"] / 100.0)
    return sorted(results, key=lambda r: (-r["score"], r["cpu_pct"]))


def pick(results: list[dict]) -> dict | None:
    ranked = score(results)
    return ranked[0] if ranked else None


def current_profile(cfg: dict) -> str:
    """Which named profile the current decode_env corresponds to."""
    off = {k for k, v in (cfg.get("decode_env") or {}).items() if not v}
    for name, overrides in PROFILES.items():
        if {k for k, v in overrides.items() if not v} == off:
            return name
    return "custom"


def apply_profile(cfg: dict, name: str) -> dict:
    cfg["decode_env"] = {p: (p not in PROFILES[name]) for p in PASSES}
    return cfg["decode_env"]


def _state_path(home: str) -> str:
    return os.path.join(home, "run", "tune.json")


def read_state(home: str) -> dict:
    try:
        with open(_state_path(home), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(home: str, **fields) -> dict:
    st = read_state(home)
    st.update(fields)
    path = _state_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, path)
    return st


def due(cfg: dict, home: str) -> tuple[bool, str]:
    if not cfg.get("decode_auto"):
        return False, "decode_auto is off"
    days = float(cfg.get("decode_auto_days") or 14)
    last = read_state(home).get("last_sweep")
    if not last:
        return True, "never run"
    age = (time.time() - float(last)) / 86400.0
    return age >= days, f"last run {age:.1f} d ago (every {days:g} d)"


def choose(results: list[dict], current: str, margin: float = 0.10):
    """Best profile, but only if it clearly beats the one in use.

    A 10 % margin is deliberate: the sweep is synthetic, so a hair's-breadth
    "win" is noise, and changing decode behaviour is not free.
    """
    ranked = score(results)
    if not ranked:
        return None, "no results"
    best = ranked[0]
    row = next((r for r in ranked if r["profile"] == current), None)
    if row is None:
        return best, f"{best['profile']} (current profile was not measured)"
    if best["profile"] != current and best["score"] > row["score"] * (1.0 + margin):
        gain = (best["score"] / row["score"] - 1.0) * 100 if row["score"] else 100.0
        return best, f"{best['profile']} beats {current} by {gain:.0f}%"
    return row, f"kept {current} (best was {best['profile']}, not clearly better)"


def auto_sweep(cfg: dict, home: str, log=print):
    """Sweep with the cable to itself, then keep the winner if it is clear.

    Only safe before the decoders start: it plays the fixture into the cable,
    so live audio and other consumers have to be out of the way.
    """
    loops = list(cfg.get("source_loops") or [])
    state_file = os.path.join(home, "run", "pulse-modules.json")
    try:
        pulse.stop_source_loops(state_file=state_file)
    except Exception:
        pass
    try:
        levels = [float(x) for x in
                  str(cfg.get("decode_auto_levels") or "-21").split(",")]
        results = sweep(cfg, levels=tuple(levels), runs=1, log=log)
    finally:
        for src in loops:
            try:
                pulse.loop_source(src, state_file=state_file)
            except Exception:
                pass
    current = current_profile(cfg)
    best, why = choose(results, current)
    write_state(home, last_sweep=time.time(), winner=best["profile"],
                results=[{k: r.get(k) for k in ("profile", "decodes", "runs",
                                                "rate", "cpu_pct", "score")}
                         for r in score(results)],
                applied=best["profile"] != current, note=why)
    if best["profile"] != current:
        apply_profile(cfg, best["profile"])
        config.save(cfg)
    log(f"tune: {why}")
    return best, why


def _free_port(start: int = 2460) -> int:
    for port in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free TCP port for the tuning decoder")


def _cpu_seconds(pid: int) -> float:
    """utime + stime of a process, in seconds."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            fields = fh.read().rsplit(") ", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return 0.0


def _play(samples: array.array) -> None:
    """Play into the cable starting at the next 15 s slot boundary."""
    now = time.time()
    time.sleep(max(0.0, (int(now // 15) + 1) * 15 - now))
    subprocess.run(["pacat", "--device=" + pulse.SINK, "--rate=48000",
                    "--channels=1", "--format=s16le", "--raw"],
                   input=samples.tobytes(), check=False)
    time.sleep(13)          # let the decoder finish the period


def sweep(cfg: dict, levels=(0, -14, -18), runs=2, profiles=None,
          log=print) -> list[dict]:
    """Run every profile at every level and return one row per profile."""
    if not os.path.exists(FIXTURE):
        raise RuntimeError(f"fixture missing: {FIXTURE}")
    base = load_fixture()
    profiles = profiles or PROFILES
    results = []

    for name, overrides in profiles.items():
        decode_env = {p: True for p in PASSES}
        decode_env.update(overrides)
        with tempfile.TemporaryDirectory(prefix="js8tune-") as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            trial = dict(config.DEFAULTS, **cfg)
            trial.update({
                "api_port": _free_port(), "channels": 1,
                "decode_env": decode_env, "mycall": "N0CALL", "mygrid": "EM48",
                "heartbeat_off": True, "write_logs": True,
            })
            dec = engine.Decoder(trial, p, 1)
            dec.spawn()
            try:
                dec.wait_api(60.0)
                log(f"{name}: decoder up on {dec.port}")
                decodes = 0
                cpu0 = _cpu_seconds(dec.proc.pid)
                wall0 = time.time()
                for level in levels:
                    # level <= 0 is a target SNR; 0 means "clean, no noise"
                    audio = base if level == 0 else mix_noise(base, level)
                    for _ in range(runs):
                        before = decodes
                        _play(audio)
                        for frame in dec.api.read(0.5):
                            if frame.get("type") == "RX.DIRECTED" and \
                                    MARKER in str(frame.get("value", "")):
                                decodes += 1
                        log(f"  {level:+.0f} dB SNR: "
                            f"{'decoded' if decodes > before else 'no decode'}")
                cpu = _cpu_seconds(dec.proc.pid) - cpu0
                wall = max(0.001, time.time() - wall0)
                results.append({
                    "profile": name, "decodes": decodes,
                    "runs": len(levels) * runs,
                    "cpu_pct": 100.0 * cpu / wall,
                    "cpu_s_per_period": cpu / max(1.0, wall / 15.0),
                    "passes": {k: bool(decode_env.get(k, True)) for k in PASSES},
                })
            finally:
                dec.stop()
    return results


def table(results: list[dict]) -> str:
    rows = ["profile    decodes/runs  rate   cpu%   s/period  score"]
    for r in score(results):
        rows.append(f"{r['profile']:<10} {r['decodes']:>2}/{r['runs']:<8} "
                    f"{r['rate']:.2f}  {r['cpu_pct']:5.1f}  "
                    f"{r.get('cpu_s_per_period', 0):8.2f}  {r['score']:+.3f}")
    return "\n".join(rows)
