# Testing on real hardware

Software paths are covered by tests and virtual sinks. These are what only a
radio, dongle, sound card or the target laptop can settle.

## RTL-SDR

- [ ] `sudo modprobe -r dvb_usb_rtl28xxu`, blacklist it, then `sdr --probe`
      lists one device.
- [ ] `sdr on --gain 40` feeds the cable: `level` between -20 and -10 dBFS.
- [ ] HF needs direct sampling: `dial 7.078` then `sdr on`; check the logged
      command contains `-E direct2`, and that decodes arrive.
- [ ] Pull the stick mid-run: an event appears, restarts back off 30 s, 60 s,
      120 s... and plugging it back recovers without a restart.
- [ ] PPM: with a steady carrier, `js8call-logger level` shows drift-free copy
      after `sdr --ppm N` is set; note the value that fixed it.

## USB sound card / line input

- [ ] `input --list` shows the card; `input <name>` restarts and `doctor` says
      `[ok] input device - <name>`.
- [ ] `level` lands in -20..-10 dBFS with the radio at a normal volume; if it
      says clipping, `level --set` fixes it in one step.
- [ ] Unplug and replug the card: its name gains a suffix (e.g. `.2`), so
      `input` must be re-run; confirm `doctor` flags the stale name first.

## WebSDR / browser tap (operator-driven)

- [ ] Chrome playing the WebSDR, `route-audio --source morse-logger_cable.monitor`
      shares it without the two loggers fighting (no flap warnings).
- [ ] The WebSDR's own buffering is visible in `clock --bootstrap` as extra
      phase: measure it, because it is larger than on a line input.

## Clock

- [ ] `clock` agrees in sign with `timedatectl`/`chronyc tracking`: a clock
      that is 2 s fast must show a negative correction, not positive.
- [ ] Park the clock 30 s off (`sudo date -s`), start the logger: nothing
      decodes. `clock --bootstrap` with a strong station reports a structure
      ratio over 2 and a correction within ~1 s of the real error. Then
      `--set` and confirm decodes begin.
- [ ] Note the residual: the reported phase includes the audio pipeline delay
      (~1.1-1.4 s measured here, 0.3 s spread). Real hardware will differ;
      record the value for the line input and the WebSDR separately.

## The target laptop (Core 2 Duo SU9300, Lubuntu 26.10)

- [ ] `doctor --benchmark` reports `slow`; `status` shows `intervals x4`.
- [ ] Idle CPU of the decoder under 50 % of one core; note `cpu s/period`
      from `tune --decoder` (0.84 s here on a fast core, so ~6-7 s expected).
- [ ] Decode rate on a known band is not worse than on the fast machine by
      more than the band's own variation.
- [ ] Full offline install from `bundle/` on a disconnected machine, then
      `doctor` passes with no network.

## Resilience

- [ ] `systemctl --user restart pipewire`: within 30 s the cable and source
      loops are rebuilt, an event is logged, and the browser keeps playing.
- [ ] Kill the decoder (`pkill -x JS8Call`): restarted within ~10 s, an event
      is logged, decodes resume.
- [ ] Two channels with two real signals: `cable up --channels 2`, `ch2` in
      the bracket, and both conversations files written.
- [ ] `tune --decoder --apply` on real signals, then compare decodes per hour
      over a day; revert `decode_env` if it is worse.
- [ ] `prune --days 7` on a real log tree; the archives open with `zcat` and
      the JSONL rewrite keeps appending cleanly afterwards.

## Record what you find

Write the numbers back into `docs/DESIGN.md` (and, for the CPU, into the
"measured" table there), so the next round starts from evidence rather than
from these placeholders. Anything that fails without an obvious cause belongs
in `FIXME.txt` with the command and the exact message.
