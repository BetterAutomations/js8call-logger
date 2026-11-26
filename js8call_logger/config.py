"""Configuration, paths and defaults for js8call-logger.

Everything the program owns lives under one visible, non-dotted directory
(default ~/js8call-logger) so a user can find their logs a year later:

    <home>/config.json          settings (created on first run)
    <home>/logs/                daily text + the JSONL stream + the SQLite file
    <home>/conversations/       one file per correspondent
    <home>/run/                 pid, lock, status, dial (atomic writes)
    <home>/js8call/             JS8Call's own config/data dirs (XDG_*)
    <home>/js8call/JS8Call.ini  generated on every start
"""

from __future__ import annotations

import json
import os

APP = "js8call-logger"

DEFAULTS = {
    # audio
    "cable": APP + "_cable",          # null sink; source is <cable>_src
    "input_device": None,             # None -> "<cable>_src"
    "source": "cable",                # cable | sdr (rtl_fm -> pacat -> cable)
    "rtl_gain": None,                 # rtl_fm -g (tenths of dB, "49.6")
    "rtl_ppm": None,                  # rtl_fm -p
    "rtl_serial": None,               # rtl_fm -d, when several sticks
    "rtl_bias_tee": False,            # 12 V on the antenna port (rtl_biast)
    "rtl_sample_rate": 240000,        # rtl_fm -s, decimated to -r cable_rate
    "cable_rate": 48000,
    "monitor": True,                  # hear the cable on the speakers
    "tap": False,                     # copy the speakers into the cable instead
    "channels": 1,                    # decoders = cables (cable up --channels N)
    "source_loops": [],               # capture sources looped into the cable
    "route_apps": [],                 # apps route-audio should keep on the cable
    "route_watch": True,              # re-route those apps when they reload
    # decoder / API
    "api_host": "127.0.0.1",
    "api_port": 2442,
    "js8call_binary": None,           # None -> <repo>/bundle/JS8Call.AppImage
    "mycall": "N0CALL",               # JS8Call needs one; not used to transmit
    "mygrid": "EM48",
    "dial_hz": 7078000,               # metadata + what we ask JS8Call for
    # log
    "line_chars": 150,                # hard cap, rendered line, prefix included
    "heartbeats_in_text": False,      # text file keeps @HB only when True
    "eot": "\u2662",                  # JS8Call's end-of-transmission character
    "expand": True,                   # annotate shorthand in the text log
    "expand_mode": "gloss",           # gloss = meaning[TOKEN], replace = prose
    "expand_min_coherence": 45,       # do not gloss a garbled decode
    "snr_min_text": None,             # drop weaker lines from the text file
    "dedupe_grace_s": 2.0,            # window to collapse ACTIVITY/DIRECTED
    "retention_days": 0,              # 0 = keep everything; see `prune`
    # input level
    "level_target_db": -14.0,         # aim here; JS8 wants -20..-10 dBFS
    "level_auto": False,              # daemon trims the source volume as it drifts
    "level_auto_interval_s": 20,      # how often the level watchdog measures
    "level_max_step_db": 6.0,         # each adjustment, so repeated calls converge
    # watchdog: automatic repair while the daemon runs
    "watchdog": True,                 # restart the decoder if audio flows but
    "watchdog_silence_s": 300,        # nothing decodes for this long
    "watchdog_cooldown_s": 600,       # do not restart again sooner than this
    "watchdog_max_restarts": 4,       # give up after this many in a row
    "cable_watch": True,              # re-create the cable/loops if they vanish
    "cable_watch_interval_s": 30,
    # intervals, all stretched by the measured CPU profile (see cpu.py)
    "poll_interval_s": 0.05,
    "route_interval_s": 3,
    "status_interval_s": 5,
    "notify": True,                   # desktop notification for repair events
    "prune_auto": False,              # run prune at start when due
    "prune_interval_h": 24,           # ... at most this often
    "decode_auto": False,             # re-sweep decode depth when due (see tune)
    "decode_auto_days": 14,           # ... at most this often
    "decode_auto_levels": "-21",      # SNR(s) the automatic sweep uses
    # Refinement passes; false sets JS8_DISABLE_<NAME> for the decoder.
    "decode_env": {"deep_search": True, "ldpc_rescue": True,
                   "coherent_data": True, "redemod": True,
                   "freq_tracking": True, "timing_tracking": True},
    # clock
    "clock_apply": "log",             # "log" (A) or "system" (B, needs root)
    "clock_samples": 15,              # decodes to median before acting
}


def home() -> str:
    return os.path.abspath(
        os.environ.get("JS8CALL_LOGGER_HOME") or os.path.expanduser("~/" + APP)
    )


def paths(base: str | None = None) -> dict:
    h = base or home()
    return {
        "home": h,
        "config": os.path.join(h, "config.json"),
        "logs": os.path.join(h, "logs"),
        "conversations": os.path.join(h, "conversations"),
        "run": os.path.join(h, "run"),
        "js8call": os.path.join(h, "js8call"),
    }


def ensure_dirs(p: dict | None = None) -> dict:
    p = p or paths()
    for key in ("home", "logs", "conversations", "run", "js8call"):
        os.makedirs(p[key], exist_ok=True)
    return p


def load(create: bool = True) -> dict:
    p = ensure_dirs()
    cfg = dict(DEFAULTS)
    if os.path.exists(p["config"]):
        with open(p["config"], encoding="utf-8") as f:
            cfg.update(json.load(f))
    elif create:
        save(cfg, p)
    # Measure the machine once, then scale the intervals - never the other way
    # round. The scaled values are computed at read time (see scaled()) and are
    # deliberately not written back, so a user who saves the config again does
    # not multiply the scale into the stored numbers.
    from . import cpu as _cpu
    score, profile = _cpu.detect(p["home"])
    cfg["_cpu_score"] = score
    cfg["_cpu_profile"] = profile
    cfg["_cpu_scale"] = _cpu.SCALES[profile]
    return cfg


def scaled(cfg: dict, key: str, default):
    """Effective value of an interval, stretched for a slow machine."""
    return float(cfg.get(key) if cfg.get(key) is not None else default) * \
        float(cfg.get("_cpu_scale") or 1.0)


def save(cfg: dict, p: dict | None = None) -> None:
    """Write the user's settings; runtime fields (leading _) never land here."""
    p = p or ensure_dirs()
    tmp = p["config"] + ".tmp"
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p["config"])


def input_device(cfg: dict, channel: int = 1) -> str:
    """Where the decoder really listens: a chosen device, else the cable."""
    if channel > 1:
        return f"{cfg['cable']}_src_{channel}"
    return cfg.get("input_device") or (cfg["cable"] + "_src")


def write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
