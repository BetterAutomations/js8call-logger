# What is built, and how it was proven

`FIXME.txt` holds only what is open. This file records what already works and
the evidence, so closing an item does not mean losing the reasoning. What must
be proven on real hardware lives in `HARDWARE-TESTS.md`.

## Sources

**RTL-SDR** — built. `sdr on|off`, `sdr --probe`, `--gain/--ppm/--serial`;
`rtl_fm | pacat` into the cable, `-E direct2` below 24 MHz, both pipes
drained, supervised with 30 s doubling backoff, and the DVB-driver case
explained in a `#` event.
Proven: the real `rtl_fm` was executed with no dongle attached ("No supported
devices found" became an actionable event) and `sdr off` restored the cable.
Stub `rtl_fm`/`pacat` prove audio flow, death detection and restart.

**Sound card / line input** — built. `input NAME`, `input --list`, `--clear`;
`level`, `doctor`, `clock --bootstrap` and both watchdogs resolve the device
through `config.input_device()` instead of assuming the cable.
Proven: switching to a real ALSA mic with nothing plugged in reported digital
silence (-90 dBFS) and doctor named the device; clearing restored the cable.

## Tuning

**Band survey** — built (`scan`). Steps the eight JS8 calling frequencies,
dwells on each, counts decodes out of the JSONL, and ranks by decodes per minute
then median SNR. Proven live: a 25 s dwell on 14.078 counted 2 decodes at
3.0/min (median +33) when the known frame was played into the cable; the
refusal without an SDR and `--list` are verified too. Still needs real bands.

**Bias tee** — built (`sdr --bias-tee on|off`, persisted, applied at start
through `rtl_biast`). The failure path is proven against the real `rtl_biast`
with no dongle attached; whether an LNA actually powers up needs hardware.

**Decode depth** — built and measured; the answer was "no difference".
`tune --decoder` sweeps profiles through the `JS8_DISABLE_*` switches, counts
decodes and CPU per 15 s period, and applies a winner only above a 10 % margin.
Measured 2026-11-25 on this machine: -20 dB SNR decoded with every profile,
-23 dB decoded with none, CPU 0.83-0.85 s/period throughout. Defaults left
alone: all refinement passes on.
`decode_auto` runs the sweep at start (14 days, one level, source loops paused).

## Clock

**`clock --set`** — built (needs root). Not yet exercised against a clock that
is actually wrong.

**Bootstrap** — built. Spectral-peak correlation against the 15 s frame grid;
refuses below 1.2x frame-vs-gap structure.
Measured: the known fixture alone scores ~3500x and recovers to +1.1..1.4 s
(that residue is audio pipeline delay); a busy 20 m band scores 1.14x and is
refused with an explanation instead of a bogus correction.

**Continuous slew** — deliberately not built: it fights systemd-timesyncd, and
moving the clock under a running JS8Call upsets its slot scheduler.

## Plumbing

- Watchdogs, all exercised live: level trim (6 dB steps, never amplifies
  digital silence), decoder restart when audio flows but nothing decodes
  (proven by parking the dial on an empty band), cable rebuild after the
  modules were removed, dial restore after JS8Call reloaded its own frequency,
  prune at start, decode sweep at start.
- Events: repairs are a `#` line in the daily text, a daemon-log line, a
  best-effort notify-send and a status field; identical events collapse for
  ten minutes. Proven live with a forced dial mismatch.

- API client holds a lock and a socket snapshot, so a watchdog restart can no
  longer take the daemon down with `NoneType.recv` (fcaf72e).
- `service install` writes a systemd user unit; enabled, and `Restart=on-failure`
  proven by SIGKILLing the daemon. Executable paths containing spaces are
  quoted (a spaced launcher path previously crash-looped the unit).
- `cable up` honours the saved `monitor` setting; `doctor` reports
  `[ok] no monitor loopback into your speakers` or names the echo trap.
  The doubled-audio cause was a monitor loopback into the default sink.

## Data

- Text, JSONL, SQLite and per-conversation files; 60 s weighted dedupe;
  fragment marker from JS8Call's own frame opinion; protocol-first shorthand
  with `meaning[TOKEN]`; conversations by station and by group
  (`conversations/groups/`), with `migrate` folding the old flat GROUP files.
- Retention: `prune` archives (gzip) by default, never deletes unless asked,
  refuses while the daemon holds the JSONL, and can run automatically at start.

## Multi-channel

Two channels proven end to end by `tools/two-channel-check.py`: two cables, the
same known frame played into both on a slot boundary, one record per channel in
the log, then the install restored (including back to systemd). Two *different*
signals at once still needs a second receiver.

## Fixtures and tests

- `tests/fixtures/live-2026-11-26T1622Z-30s.wav` — 30 s captured off the live
  cable while the log was filling, chosen from the records themselves, verified
  by replay (93 % of expected text).
- `tests/fixtures/websdr-2026-11-25T1453Z-30s.wav` — real busy-band audio; no
  decodable JS8 in it (verified with a dedicated decoder on a silent cable),
  kept as the case where clock bootstrap must refuse (ratio 1.07).
- `tests/fixtures/n0call-cq.raw` — the synthetic frame `selftest` replays.
- 214 unit tests; `tools/audit_verbs.py` keeps the README and the CLI in step.
