#!/bin/sh
# Prove that the HF input (direct sampling) is really wired to the antenna.
#
#   ./tools/hf-input-test.sh
#
# "Enabled direct sampling mode, input 2/Q" only means the software asked the
# RTL2832 to route its ADC to the Q pin. It does not prove that RF from the
# connector reaches that pin - on plenty of dongles it does not. The test that
# does prove it needs a body: a connected input changes level by many dB when
# you touch the centre pin (you are an antenna, and you inject 60 Hz), and a
# disconnected one does not move at all.
#
# It runs twice: 7.078 MHz through direct sampling, then 28.078 MHz through the
# tuner as a control. The control is what makes the answer mean something -
# if the tuner path responds to your hand and the HF path does not, the HF
# input is not connected. The logger is stopped for the duration and started
# again afterwards.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
log=$here/js8call-logger
was_running=0
if [ -x "$log" ] && "$log" status >/dev/null 2>&1; then
    was_running=1
    "$log" stop >/dev/null
fi
trap '[ "$was_running" = 1 ] && "$log" start >/dev/null || true' EXIT

python3 - <<'PY'
import subprocess
import time

import numpy as np

RATE = 48000
BLOCK = int(RATE * 0.25) * 2          # 250 ms of s16 mono


def level(proc):
    raw = proc.stdout.read(BLOCK)
    if len(raw) < BLOCK:
        return None
    d = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    return 20 * np.log10(np.sqrt((d ** 2).mean()) + 1e-12)


def phase(proc, seconds, title):
    vals = []
    print(f"    {title}")
    end = time.time() + seconds
    while time.time() < end:
        v = level(proc)
        if v is not None:
            vals.append(v)
    vals.sort()
    return vals[len(vals) // 2] if vals else None


def probe(freq, label):
    cmd = ["rtl_fm", "-M", "usb", "-f", str(freq), "-s", "240000",
           "-r", str(RATE), "-l", "0"]
    if freq < 24_000_000:
        cmd += ["-E", "direct2"]
    cmd += ["-"]
    print(f"\n{label}: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    before = phase(proc, 5, "baseline - hands off the antenna")
    print("    >>> TOUCH THE CENTRE PIN OF THE ANTENNA CONNECTOR NOW (6 s)")
    during = phase(proc, 6, "touching")
    print("    >>> LET GO")
    after = phase(proc, 4, "released")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(1.5)          # let the device be released before the next run
    if before is None or during is None:
        print("    no audio from rtl_fm - is the stick free?")
        return None
    jump = during - before
    print(f"    {before:.1f} dB before, {during:.1f} dB touching, "
          f"{after:.1f} dB after  ->  {jump:+.1f} dB")
    return jump


hf = probe(7_078_000, "HF path (direct sampling)")
vhf = probe(28_078_000, "control (tuner path)")
print()
if hf is None or vhf is None:
    print("inconclusive: a capture failed")
elif hf > 6 and vhf > 6:
    print("PASS: both inputs respond to your body. The HF path is wired, so a")
    print("      quiet HF band is the antenna or the band, not the hardware.")
elif hf <= 3 and vhf > 6:
    print("FAIL: the tuner path hears your hand and the HF path does not. RF is")
    print("      not reaching the Q branch on this board - direct sampling is")
    print("      software-only here, and HF needs a different receiver or a mod.")
elif hf > 6 and vhf <= 3:
    print("ODD: HF responds and the tuner path does not. Check the antenna is")
    print("      on the connector and that the VHF capture ran (24-1766 MHz).")
else:
    print("INCONCLUSIVE: neither path moved. Either the pin was not touched, or")
    print("      nothing is connected to the antenna port. Try again holding the")
    print("      pin for the whole 6 s.")
PY
