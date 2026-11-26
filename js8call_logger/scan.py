"""Survey the JS8 calling frequencies and score what is open.

Unlike a CW band scan there is nothing to detect on the waterfall: JS8 answers
the question itself, by decoding. So a survey is dial -> dwell -> count decodes,
and the winner is the band where the decoder actually copied stations.
"""
import json
import os
import statistics
import time

# Dial (USB) frequencies where JS8 operators gather, lowest first.
CALLING_HZ = [3578000, 7078000, 10130000, 14078000,
              18104000, 21078000, 24922000, 28078000]


def label(hz: int) -> str:
    return f"{hz / 1e6:.3f} MHz"


def parse_freqs(text: str) -> list[int]:
    """'7.078,14.078' or '7078 14078' (kHz) -> Hz, in the order given."""
    out = []
    for part in text.replace(",", " ").split():
        v = float(part)
        out.append(int(round(v * 1e6 if v < 100 else v * 1000)))
    return out


def sample_row(hz: int, records: list[dict], seconds: float) -> dict:
    """One band's result: how many decodes, and how well they read."""
    snrs = [r["snr"] for r in records if r.get("snr") is not None]
    n = len(records)
    return {
        "dial": hz,
        "label": label(hz),
        "decodes": n,
        "seconds": seconds,
        "per_min": (n * 60.0 / seconds) if seconds else 0.0,
        "snr": statistics.median(snrs) if snrs else None,
        "best_snr": max(snrs) if snrs else None,
    }


def rank(rows: list[dict]) -> list[dict]:
    """Most decodes first; a tie goes to the better median SNR, then lower band."""
    return sorted(rows, key=lambda r: (-r["per_min"],
                                       -(r["snr"] if r["snr"] is not None else -99),
                                       r["dial"]))


def records_since(jsonl: str, hz: int, since: float) -> list[dict]:
    """Decodes logged at this dial after `since` (a wall clock time)."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    out = []
    try:
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("ts") or "") < stamp:
                    continue
                dial = r.get("dial")
                if dial is not None and abs(int(dial) - hz) > 1000:
                    continue
                out.append(r)
    except OSError:
        pass
    return out


def row_line(r: dict) -> str:
    snr = "--" if r["snr"] is None else f"{r['snr']:+.0f}"
    return (f"  {r['label']:<11} {r['decodes']:>3} decodes"
            f"  {r['per_min']:>5.1f}/min  snr {snr:>3}")


def survey(api, reader, dwell_s: float, freqs: list[int],
           log=print, sleep=time.sleep) -> list[dict]:
    """Retune each frequency, dwell, count what the decoder copied."""
    rows = []
    log(f"surveying {len(freqs)} frequencies, {dwell_s:.0f}s each"
        f" (~{len(freqs) * dwell_s / 60:.0f} min)")
    for hz in freqs:
        try:
            api.set_freq(hz, 1500)
        except Exception as exc:                     # noqa: BLE001
            log(f"  {label(hz):<11} could not retune: {exc}")
            continue
        t0 = time.time()
        sleep(dwell_s)
        row = sample_row(hz, reader(hz, t0), dwell_s)
        rows.append(row)
        log(row_line(row))
    return rows
