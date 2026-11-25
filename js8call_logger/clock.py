"""Clock offset from JS8 decodes.

Every decoded frame carries TDRIFT: JS8Call's own measurement of how far our
computer clock sits from the sender's frame timing. No NTP, no network, no
privileges needed to *measure* it - a few decodes give a usable median.

What this can and cannot do:
  * It gives sub-15-second alignment. JS8 frames start on 15 s boundaries, so
    the information is only phase, not date: a clock off by hours cannot be
    repaired from the air, and the timestamps stay wrong until someone sets
    the date once.
  * Decoding itself needs ~2 s of accuracy. Beyond that JS8Call hears audio
    and decodes nothing, which is why the daemon reports "decoding, no
    decodes" rather than silently logging nothing.

Two policies, both here:
  A. "log"    - leave the system clock alone, correct the log timestamps.
  B. "system" - optionally write the measured offset into the system clock
                (needs root: sudo date -s, or a sudoers entry for `date`).
"""

from __future__ import annotations

import shutil
import statistics
import subprocess
import time


class Tracker:
    def __init__(self, samples: int = 15):
        self.want = max(3, int(samples))
        self._vals: list[float] = []

    def add(self, tdrift: float) -> None:
        # Reject nonsense: a real offset is small, and TDRIFT of a borderline
        # decode can be wild. Keeping junk would poison the median.
        if tdrift is None or abs(tdrift) > 5.0:
            return
        self._vals.append(float(tdrift))
        if len(self._vals) > self.want * 4:
            del self._vals[:-self.want * 4]

    def count(self) -> int:
        return len(self._vals)

    def offset(self) -> float:
        """Seconds to add to the local clock to get the transmitted time."""
        if len(self._vals) < 3:
            return 0.0
        return round(statistics.median(self._vals[-self.want * 2:]), 3)

    def confident(self) -> bool:
        return len(self._vals) >= self.want

    def spread(self) -> float:
        if len(self._vals) < 3:
            return 0.0
        recent = self._vals[-self.want:]
        return round(max(recent) - min(recent), 3)


def apply_system(offset: float, dry_run: bool = False) -> tuple[bool, str]:
    """Move the system clock by `offset` seconds. Needs root."""
    if abs(offset) < 0.2:
        return True, "offset too small to be worth setting"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + offset))
    if dry_run:
        return True, f"would set system clock to {stamp} (offset {offset:+.2f}s)"
    if shutil.which("date") is None:
        return False, "date(1) not found"
    r = subprocess.run(["date", "-s", stamp], capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return True, f"system clock set to {stamp} (offset {offset:+.2f}s)"
