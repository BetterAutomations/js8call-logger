"""Expand the shorthand that actually appears in JS8 traffic.

Unlike CW, JS8 traffic is mostly plain English - measured over 512 records from
a live 40/20 m log, the frequent short tokens were protocol words (SNR, CQ,
MSG, ACK, @ALLCALL, DE) and ordinary English, with only the occasional 73 or
TNX. So this glossary is protocol-first: JS8's own keywords, the Q-signals, and
the classic abbreviations that still show up, and nothing else.

The annotation is ``meaning[TOKEN]``, the same notation morse-logger uses: the
readable text first, the token that was actually sent in brackets, so a wrong
expansion is visible right beside it. ``mode="replace"`` gives clean prose with
no brackets for people who prefer that.
"""

from __future__ import annotations

import re

TRAIL = ".,?!;:)\"'"
WORD_RE = re.compile(r"[A-Za-z0-9@/]+")

# --- JS8 protocol vocabulary ------------------------------------------------
PROTOCOL = {
    "@ALLCALL": "all stations",
    "@HB": "heartbeat",
    "HEARTBEAT": "heartbeat",
    "CQ": "calling all stations",
    "MSG": "message",
    "MSGS": "messages",
    "ACK": "acknowledged",
    "NACK": "not acknowledged",
    "SNR": "signal report",
    "GRID": "locator",
    "DE": "from",              # callsign separator, as in "KN4IIG DE WD4U"
}

# --- Q-signals --------------------------------------------------------------
Q = {
    "QSL": "confirm", "QTH": "location", "QSY": "change frequency",
    "QRM": "interference", "QRN": "static", "QSB": "fading",
    "QRP": "low power", "QRO": "high power", "QRZ": "who is calling",
    "QRT": "going off air", "QRV": "ready", "QRX": "stand by",
    "QSP": "relay", "QTC": "traffic", "QTR": "time", "QRG": "frequency",
    "QSO": "contact",
}

# --- classic abbreviations still used in JS8 --------------------------------
CW = {
    "73": "best regards", "88": "love and kisses",
    "TNX": "thanks", "TKS": "thanks", "TU": "thank you",
    "PSE": "please", "PLS": "please", "SRI": "sorry",
    "AGN": "again", "FER": "for", "ES": "and",
    "HW": "how", "HR": "here", "UR": "your", "U": "you",
    "RIG": "equipment", "PWR": "power", "ANT": "antenna",
    "WX": "weather", "CONDX": "conditions", "GUD": "good",
    "OM": "old man", "YL": "young lady", "XYL": "wife",
    "GL": "good luck", "GB": "goodbye", "B4": "before",
    "CU": "see you", "CUAGN": "see you again", "BCNU": "be seeing you",
    "TMW": "tomorrow", "MNI": "many", "MNY": "many", "NW": "now",
    "VY": "very", "WID": "with", "WKG": "working", "WKD": "worked",
    "RPT": "repeat", "CPY": "copy", "CFM": "confirm",
    "MUF": "maximum usable frequency", "PRED": "prediction",
    "BURO": "bureau", "TFC": "traffic", "ADR": "address",
}

GLOSSARY = dict(PROTOCOL)
GLOSSARY.update(Q)
GLOSSARY.update(CW)

# Tokens we deliberately do not touch: group names other than @ALLCALL/@HB
# (@GHOSTNET, @APRSIS, @TTP...), callsigns, numbers, and plain English words
# that merely look short ("NO", "TO", "IN", "ID", "ON").

CALL_RE = re.compile(
    r"^(?:[A-Z]{1,2}\d[A-Z]{1,4}|\d[A-Z]\d?[A-Z]{1,4}|[A-Z]\d[A-Z]{1,4})(?:/[A-Z0-9]{1,4})?$")


def is_callsign(tok: str) -> bool:
    return bool(CALL_RE.match(tok.upper()))


def gloss_token(tok: str):
    """(expansion, uncertain) for one token, or None to leave it alone."""
    core = tok.strip(TRAIL)
    if not core:
        return None
    if core.startswith("@") and core.upper() not in ("@ALLCALL", "@HB"):
        return None                      # other group names are not shorthand
    if is_callsign(core):
        return None                      # an explicit entry would already match
    expansion = GLOSSARY.get(core.upper())
    if expansion is None:
        return None
    return (expansion, False)


def translate_units(text: str, mode: str = "gloss", min_coherence: int | None = None,
                    coherence_fn=None):
    """Return (units, n_expansions).

    A unit is one unbreakable chunk of the rendered line: ``all stations[CQ]``
    stays together, because breaking between a meaning and the token it came
    from defeats the annotation. Expand a token repeated in a run only once -
    ``CQ CQ CQ`` is one call, and three expansions bury the copy.
    """
    if not text:
        return [], 0
    if mode == "off":
        return text.split(" "), 0
    if min_coherence is not None and coherence_fn is not None and \
            coherence_fn(text) is not None and coherence_fn(text) < min_coherence:
        return text.split(" "), 0

    out: list[str] = []
    count = 0
    prev = None
    for tok in text.split(" "):
        got = gloss_token(tok)
        if got is None:
            out.append(tok)
            prev = None
            continue
        expansion, uncertain = got
        core = tok.strip(TRAIL).upper()
        if prev is not None and core == prev:
            out.append(tok)
            continue
        prev = core
        count += 1
        tail = tok[len(tok.rstrip(TRAIL)):]
        core_raw = tok[:len(tok) - len(tail)] if tail else tok
        if mode == "replace":
            out.append(f"{expansion}{tail}")
        else:
            out.append(f"{expansion}[{core_raw}{'?' if uncertain else ''}]{tail}")
    return out, count


def translate(text: str, **kw) -> tuple[str, int]:
    units, n = translate_units(text, **kw)
    return " ".join(units), n


def legend() -> str:
    """One line written at the top of each new daily log."""
    return ("meaning[TOKEN] = shorthand expanded, TOKEN unchanged; "
            "... at line start = wrapped, \u2026 at line end = one frame of a longer message")
