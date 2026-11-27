"""What the decoder is listening to, right now.

`status` answers "is the process healthy"; this answers a different question
that keeps people staring at an empty log: which audio is arriving. The answer
is one of a stick, one or more programs playing into the cable, a hardware
input or another program's cable looped into it, a chosen capture device - or
nothing at all, which is the case worth naming out loud.

Everything here is gathered from the sound server and /proc, never from the
config alone: config says what was asked for, and the point of the command is
what is actually happening.
"""

import os

from . import config, pulse

BROWSERS = ("chrome", "chromium", "firefox", "brave", "vivaldi", "opera",
            "edge", "epiphany", "midori")
PLAYERS = ("vlc", "mpv", "mplayer", "rhythmbox", "audacious", "clementine",
           "strawberry", "deadbeef", "spotify", "parole", "celluloid", "totem",
           "amberol", "elisa", "cmus", "smplayer")


def kind(app: str, stream: str = "") -> str:
    """browser / player / "" - a hint, never a claim about what is playing."""
    hay = f"{app} {stream}".lower()
    if any(b in hay for b in BROWSERS):
        return "browser"
    if any(p in hay for p in PLAYERS):
        return "player"
    return ""


def label(app: str, stream: str = "") -> str:
    what = kind(app, stream)
    return f"{app} ({what})" if what else app


def parse_rtl_args(args) -> dict:
    """Pick the facts out of an rtl_fm argv: -f frequency, -g gain, -p ppm."""
    info: dict = {"freq": None, "gain": None, "ppm": None}
    for i, arg in enumerate(args[:-1]):
        if arg == "-f":
            try:
                info["freq"] = int(args[i + 1])
            except ValueError:
                pass
        elif arg == "-g":
            info["gain"] = args[i + 1]
        elif arg == "-p":
            info["ppm"] = args[i + 1]
    return info


def rtl_processes() -> list[dict]:
    """Every running rtl_fm, with its frequency and gain from its command line.

    /proc rather than `pgrep -f`: no self-matching, no dependence on the
    pattern quoting, and the argv is exactly what we want to show.
    """
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if not args or not os.path.basename(args[0]).startswith("rtl_fm"):
            continue
        out.append({"pid": int(pid), "cmd": args, **parse_rtl_args(args)})
    return sorted(out, key=lambda row: row["pid"])


def _same(name: str | None, cable: str) -> bool:
    return (name or "") == cable


def playing(cable: str) -> list[dict]:
    """Streams whose destination is our cable sink (our own loopback excluded)."""
    return [row for row in pulse.sink_input_details()
            if _same(row.get("sink_name"), cable)
            and not pulse.is_own_loopback(row)]


def loops(cable: str) -> list[tuple[str, str, str]]:
    """(module id, source, sink) loopbacks that end on our cable."""
    return [(mid, src, snk) for mid, src, snk in pulse.loopback_modules()
            if _same(snk, cable)]


def short_device(name: str) -> str:
    """alsa_input.pci-0000_00_1f.3.HiFi__Headset__source -> HiFi__Headset__source"""
    return name.removeprefix("alsa_input.").removeprefix("alsa_output.")


def cable_of(cfg: dict) -> str:
    return cfg.get("cable") or pulse.SINK


def summary(cfg: dict, streams=(), loop_rows=(), rtl=()) -> str:
    """One line: what is feeding the decoder."""
    cable = cable_of(cfg)
    device = cfg.get("input_device")
    if device:
        return f"{short_device(device)} (chosen capture device)"
    if (cfg.get("source") or "cable") == "sdr":
        if not rtl:
            return "RTL-SDR configured, but rtl_fm is not running"
        first = rtl[0]
        where = (f"{first['freq'] / 1e6:.3f} MHz"
                 if first["freq"] else "an unreadable frequency")
        return f"RTL-SDR: rtl_fm (pid {first['pid']}) at {where} USB"
    if streams:
        if len(streams) == 1:
            return f"{label(streams[0].get('app', '?'), streams[0].get('stream', ''))}" \
                   f" -> {cable}"
        return (f"{len(streams)} programs -> {cable} "
                f"({', '.join(sorted({s.get('app', '?') for s in streams}))})")
    if loop_rows:
        src = loop_rows[0][1] or "?"
        return f"{short_device(src)} looped into {cable}"
    return f"{cable}: nothing feeding it"


def details(cfg: dict, streams=(), loop_rows=(), rtl=()) -> list[str]:
    """The lines behind the summary: device, streams, loops, stick."""
    out = [f"device   {config_device(cfg)}"]
    for row in rtl:
        extra = []
        if row.get("gain"):
            extra.append(f"gain {row['gain']}")
        if row.get("ppm"):
            extra.append(f"ppm {row['ppm']}")
        out.append(f"rtl      pid {row['pid']}  {' '.join(row['cmd'])}"
                   + (f"  ({', '.join(extra)})" if extra else ""))
    for row in streams:
        app = row.get("app", "?")
        media = row.get("stream") or ""
        hint = kind(app, media)
        out.append(f"stream   {row.get('index')}  {app}"
                   + (f" ({hint})" if hint else "")
                   + (f'  "{media}"' if media and media != "Playback Stream" else "")
                   + f"  -> {row.get('sink_name')}")
    for mid, src, snk in loop_rows:
        out.append(f"loop     module {mid}  {src} -> {snk}")
    for name in cfg.get("source_loops") or []:
        if not any(src == name for _mid, src, _snk in loop_rows):
            why = ("no such capture source" if not pulse.source_exists(name)
                   else "source exists but nothing loops it into the cable")
            out.append(f"missing  {name} (configured, {why})")
    return out


def config_device(cfg: dict) -> str:
    """The device the decoder opens, and where that decision came from."""
    name = config.input_device(cfg)
    return name if cfg.get("input_device") else f"{name} (the cable)"


def describe(cfg: dict) -> tuple[str, list[str]]:
    """Gather from the system and return (summary, detail lines)."""
    cable = cable_of(cfg)
    streams = playing(cable) if not cfg.get("input_device") else []
    if (cfg.get("source") or "cable") == "sdr":
        # rtl_fm is piped into the cable by our own `pacat`; it is the feed
        # itself, described by the rtl lines, not a second program.
        streams = [row for row in streams if row.get("app") != "pacat"]
    loop_rows = loops(cable)
    rtl = rtl_processes() if (cfg.get("source") or "cable") == "sdr" else []
    return (summary(cfg, streams, loop_rows, rtl),
            details(cfg, streams, loop_rows, rtl))
