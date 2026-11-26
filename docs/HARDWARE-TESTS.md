# Testing on real hardware

Software paths are covered by tests and virtual sinks. These are what only a
radio, dongle, sound card or the target laptop can settle.

## Install and upgrade

- [ ] Fresh Ubuntu: `sudo ./install.sh` installs to `/opt/js8call-logger`,
      unpacks the bundled AppImage without FUSE, and puts `js8call-logger` on
      `PATH`.
- [ ] User-local: `./install.sh --prefix ~/.local` needs no sudo and the
      command still resolves.
- [ ] Truly offline:
      `docker run --rm --network none -v "$PWD/bundle:/bundle:ro" ubuntu:26.04 bash -c '/bundle/install.sh'`
      completes, then `js8call-logger --version` and `doctor` work.
- [ ] Custom root: `JS8CALL_LOGGER_HOME=/srv/stations ./install.sh`; the
      wrapper exports that path and `status` reports it.
- [ ] Missing AppImage: delete `bundle/JS8Call.AppImage`, run `install.sh`: it
      warns and names `refresh-bundle.sh` instead of installing half a tree.
- [ ] `selftest --offline` passes with no cable and no decoder; the full
      `selftest` decodes the fixture.
- [ ] Legacy upgrade: a tree with flat `GROUP*.txt` files, `migrate --dry-run`
      lists what would move, then `migrate` folds them into
      `conversations/groups/` in date order with no lines lost.

## RTL-SDR

- [ ] `sudo modprobe -r dvb_usb_rtl28xxu`, blacklist it, then `sdr --probe`
      lists one device.
- [ ] `sdr on --gain 40` feeds the cable: `level` between -20 and -10 dBFS.
- [ ] Gain sweep 0–49.6: note where the noise floor rises without the signal
      rising (overload), and where decodes begin to arrive.
- [ ] HF needs direct sampling: `dial 7.078` then `sdr on`; check the logged
      command contains `-E direct2`, and that decodes arrive.
- [ ] Two sticks plugged at once: `sdr on --serial <serial>` picks the named
      one and the other carries no traffic, including after a replug (indexes
      move, serials do not).
- [ ] Pull the stick mid-run: an event appears, restarts back off 30 s, 60 s,
      120 s... and plugging it back recovers without a restart.
- [ ] Ten minutes on a long cable or a hub: no `-5` or `-9` errors in `dmesg`.
- [ ] PPM: with a steady carrier, `js8call-logger level` shows drift-free copy
      after `sdr --ppm N` is set; note the value that fixed it.
- [ ] Bias tee: `sdr --bias-tee on` reports ok and an LNA on the port powers up
      (watch the current draw, or the noise floor rising); `sdr` shows
      `bias tee on`, and it is still on after `restart`. `--bias-tee off`
      removes the 12 V — never leave it on into a shorted or unpowered feeder.
- [ ] Band survey: `scan --dwell 60` over all eight frequencies at a time when
      you can hear activity; the winner must be a band where you can also see
      decodes in `tail -f`, and `scan --apply` must leave you there.
- [ ] Survey honesty: point the receiver at a dead band and run `scan`; it must
      report 0 decodes rather than ranking a strong carrier first.

## USB sound card / line input

- [ ] `input --list` shows the card; `input <name>` restarts and `doctor` says
      `[ok] input device - <name>`; `input --clear` goes back to the cable.
- [ ] `level` lands in -20..-10 dBFS with the radio at a normal volume; if it
      says clipping, `level --set` fixes it in one step.
- [ ] `level --auto` holds the level through a QSB fade: one trimmed step per
      interval, and digital silence (muted stream) is never amplified.
- [ ] Unplug and replug the card: its name gains a suffix (e.g. `.2`), so
      `input` must be re-run; confirm `doctor` flags the stale name first.
- [ ] `route-audio --source <alsa_input...>` also reaches the cable, and
      `--stop-source` removes it (a line-in can feed the cable without
      `input`).

## WebSDR / browser tap (operator-driven)

- [ ] Chrome playing the WebSDR, `route-audio --source morse-logger_cable.monitor`
      shares it without the two loggers fighting (no flap warnings).
- [ ] The WebSDR's own buffering is visible in `clock --bootstrap` as extra
      phase: measure it, because it is larger than on a line input.
- [ ] Rebuild the cable while the tab is playing: Chrome sends digital
      silence, `doctor` says so, and only a **full browser restart** fixes it
      (a tab reload does not).
- [ ] Two WebSDR tabs: confirm they produce one mixed Chrome stream, since
      that defeats per-tab routing.
- [ ] Mute the tab: the stream stays open carrying zeros and `doctor` flags
      `digital silence`.

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

## Virtual cable plumbing (real PulseAudio / PipeWire)

- [ ] `cable up` creates one sink `js8call-logger_cable` and one source
      `js8call-logger_cable_src`; a second run changes nothing and duplicates
      no modules.
- [ ] `cable up --no-monitor` **removes** the existing monitor loopback, it
      does not merely skip creating one.
- [ ] `cable up --no-monitor`, then a bare `cable up`: monitoring stays off.
      No loopback is rebuilt and `config.json` still says `"monitor": false`.
- [ ] `doctor` with monitoring on: `[ok] one monitor loopback into your
      speakers`. Add a second cable→speakers loopback by hand: it becomes a
      `[!!]` naming both modules, and `cable up --no-monitor` clears them.
- [ ] `route-audio --copy` copies what the speakers play into the cable
      (muting or changing the speaker volume is followed); `--stop-copy`
      removes the loop.
- [ ] With `--copy` running, `cable up` (monitor) is refused with exit 2 and
      an explanation of the 0 dBFS feedback loop.
- [ ] `cable up --channels 2` adds `js8call-logger_cable_2` and
      `js8call-logger_cable_src_2`; `cable down --channels 2` removes channel
      2 and leaves channel 1 untouched.
- [ ] Reboot with the daemon or `service` enabled: the cable and the saved
      source loops are rebuilt, and the browser's stream is where you left it
      (or `route-audio --all` restores it).

## The target laptop (Core 2 Duo SU9300, Lubuntu 26.10)

- [ ] `doctor --benchmark` reports `slow`; `status` shows `intervals x4`.
- [ ] Idle CPU of the decoder under 50 % of one core; note `cpu s/period`
      from `tune --decoder` (0.84 s here on a fast core, so ~6-7 s expected).
- [ ] Decode rate on a known band is not worse than on the fast machine by
      more than the band's own variation.
- [ ] Full offline install from `bundle/` on a disconnected machine, then
      `doctor` passes with no network.
- [ ] Thermal: 30 minutes at continuous decode, `sensors`, note any clock
      throttling.
- [ ] RAM headroom: total free with browser plus decoder at the end of a day.
- [ ] Two channels: does it keep up? record CPU per decoder and xruns
      (`pactl list sink-inputs` → "Underrun").

## Resilience

- [ ] `systemctl --user restart pipewire`: within 30 s the cable and source
      loops are rebuilt, an event is logged, and the browser keeps playing.
- [ ] Kill the decoder (`pkill -x JS8Call`): restarted within ~10 s, an event
      is logged, decodes resume.
- [ ] Start twice for the same home: the second daemon refuses, naming the
      lock holder; it must not fight over the API port.
- [ ] Leave a stray `JS8Call` holding port 2442, then start: it is reaped (or
      refused with a message naming the port), not left in a modal
      "already running" box.
- [ ] Two channels with two real signals: `cable up --channels 2`, `ch2` in
      the bracket, and both conversations files written.
- [ ] Group traffic: a directed group message lands in
      `conversations/groups/<GROUP>.txt`; an all-call does not.
- [ ] `systemctl --user`/`service`: install, log out and back in, then reboot:
      the logger comes back with the cable and its dial.
- [ ] 24 h soak: no restart loop, RSS flat, no fd growth
      (`ls /proc/PID/fd | wc -l`).
- [ ] `tune --decoder --apply` on real signals, then compare decodes per hour
      over a day; revert `decode_env` if it is worse.
- [ ] `prune --days 7` on a real log tree; the archives open with `zcat` and
      the JSONL rewrite keeps appending cleanly afterwards.

## Logs

- [ ] Run across 00:00: a new daily file starts, with no lost and no
      duplicated records on either side.
- [ ] `tail -f` across midnight keeps following the new file.
- [ ] `sqlite3 js8call-logger.db "pragma integrity_check"` says `ok` after a
      day of records.
- [ ] Line count in the `.txt` matches `js8call-logger.jsonl` for the same
      day (same number of records).
- [ ] `migrate` after the log has grown: no conversation line is dropped or
      reordered.

## Record what you find

Write the numbers back into `docs/DESIGN.md` (and, for the CPU, into the
"measured" table there), so the next round starts from evidence rather than
from these placeholders. Anything that fails without an obvious cause belongs
in `FIXME.txt` with the command and the exact message.
