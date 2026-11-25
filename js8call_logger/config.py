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
    "cable_rate": 48000,
    "monitor": True,                  # hear the cable on the speakers
    "tap": False,                     # copy the speakers into the cable instead
    "channels": 1,                    # decoders = cables (cable up --channels N)
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
    "snr_min_text": None,             # drop weaker lines from the text file
    "dedupe_grace_s": 2.0,            # window to collapse ACTIVITY/DIRECTED
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
    return cfg


def save(cfg: dict, p: dict | None = None) -> None:
    p = p or ensure_dirs()
    tmp = p["config"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p["config"])


def input_device(cfg: dict) -> str:
    return cfg.get("input_device") or (cfg["cable"] + "_src")


def write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
