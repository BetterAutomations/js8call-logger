js8call-logger — JS8 logger
===========================

* Lightweight command-line wrapper around JS8Call for unattended recording of
  JS8 traffic.
* Listens to an audio input, decodes every transmission in the passband and
  files it into timestamped logs.
* Supports any audio source — WebSDR, a receiver's line out (RTL-SDR planned)
* No GUI required: the decoder runs offscreen, on a machine with no display
* Records every station at once — JS8Call decodes all offsets and all speeds
  simultaneously, so one cable hears the whole passband
* Writes a searchable record: timestamped text, JSONL, SQLite and one file per
  correspondent
* Measures the clock offset from the decodes themselves — no NTP, no network
* Tells you why it isn't working instead of logging silence quietly
* Fully offline installer: the decoder ships as one AppImage, python needs
  nothing beyond the standard library
* Small enough for old hardware: ~7 % of one core and 160 MB idle on a modern
  CPU, decoding proven offscreen

Requires Linux with PipeWire or PulseAudio, python3 >= 3.10 and glibc 2.38+
(Ubuntu 23.10 / Debian 13 or newer; tested on Ubuntu 26.04, target Lubuntu
26.10). The decoder needs no FUSE and no network.

Built with AI, so there might be bugs.

Quick start
-----------

    # Have clear JS8 audio playing: a WebSDR page, or a receiver on line-in.
    sudo ./install.sh                 # Asks where the data directory goes
    js8call-logger route-audio --all  # Send whatever is playing to the cable
    js8call-logger start              # Create the cable if needed, start decoding
    js8call-logger tail -f            # Watch it decode
    js8call-logger status             # Service, decoder, decodes, clock offset
    js8call-logger stop

That is the whole WebSDR workflow. The receiver stays yours to tune: JS8Call
decodes whatever lands in its ~2.5 kHz passband, so there is no tone to lock
onto and nothing to retune in the logger. `tune --dial` only changes the
frequency stamped on new lines.

Everything lives in `~/js8call-logger` (config, `logs/`, `conversations/`,
`run/`) unless the installer was told otherwise. `JS8CALL_LOGGER_HOME`
overrides it.

Getting good copy (decoding)
----------------------------

The decoder only sees what reaches the audio. Almost every "it is running but
the log is empty" case is one of these four things.

**Clock: within about two seconds.** This is the one that fails silently. JS8
frames start on 15 second boundaries; if the clock is further off, JS8Call
hears the audio and decodes nothing at all. Check `timedatectl`, and see
[Clock](#clock) for measuring the error from the air.

**Mode and filter.** USB, roughly 3 kHz wide, JS8 signal somewhere inside the
passband. A 500 Hz CW filter will hide most of it; a 33 Hz filter hides all of
it. Unlike CW there is no "correct" audio pitch — the decoder searches the
whole passband, and several stations at different offsets decode at once.

**Level: aim for −20 to −10 dBFS.** JS8 tolerates a lot, but at 0 dBFS the
tones flatten and the copy degrades. Back the WebSDR volume or the sound card
gain off.

**No AGC, no noise reduction, no squelch.** AGC pumps between transmissions
and noise reduction is tuned for speech; squelch cuts the quiet frames that
still decode. Turn all three off in the WebSDR or the receiver.

### Signs of trouble

| symptom | meaning | fix |
|---|---|---|
| `status` running, `decodes 0`, audio present | clock off, wrong mode, or pure noise | `timedatectl`; check USB and passband |
| `decodes 0` and the cable is silent | tab muted/paused, stream never moved, or routed into another tool's cable | `doctor` names the sink it is really on; `route-audio --all` or `--index N` |
| `tail --js8call-debug` prints nothing | decoder is not receiving at all | `doctor`, then the restarts log |
| text arrives but garbled, SNR positive | clipping or an overdriven WebSDR | lower the source volume |
| only one or two stations ever appear | filter too narrow, or the band is quiet | widen to ~3 kHz; try another band |
| log file grows but `tail -f` is silent | heartbeats and SNR filter are hiding it | `tail --all` |
| copy stops after a browser reload | the reloaded stream went back to the speakers | move it onto the cable again |

Start with `js8call-logger doctor`. It reports one line per fact in sections —
data, decoder, audio server, audio into the cable (including the measured dBFS
level and digital silence), logger, installation — marking each `[ok]`,
`[!!]` for a warning, `[--]` for information and `[FAIL]` for something that
must be fixed. It exits non-zero only when something actually failed.

`js8call-logger tail --js8call-debug` shows the raw frames arriving from the
decoder, which separates "the decoder is struggling" from "our log is
filtering something".

### Hardware

A receiver on a USB sound card needs nothing but `"input_device"` in
config.json set to the card's name (`pactl list short sources` shows them).

An RTL-SDR is not built yet (see FIXME.txt): the plan is `rtl_fm -M usb |
pacat` into the cable, the same pattern morse-logger uses, plus a later band
scan that steps the JS8 calling frequencies.

Install (offline)
-----------------

    sudo ./install.sh                  # install; asks where logs should live
    ./install.sh --prefix ~/.local     # user-local, no sudo

`install.sh` copies the program, unpacks the bundled JS8Call AppImage with
`--appimage-extract` (so no libfuse is needed), writes `js8call-logger` into
`/usr/local/bin` for a system install (`<prefix>/bin` for a user-local one),
and writes a first-run config pointing at the unpacked decoder. Nothing is
downloaded. It ends by telling you if the command directory is not on your
`PATH`.

The bundle is one file: `bundle/JS8Call.AppImage` (~59 MB, 3.0.3, checksum in
`bundle/JS8Call.AppImage.sha256`). Refresh it on a connected machine with:

    ./refresh-bundle.sh /path/to/JS8Call-v3.0.3-x86_64.AppImage

`tests/fixtures/n0call-cq.raw` is a recorded JS8 frame that `selftest` plays
into the cable, so a fresh install can prove itself with no radio attached.

What is in the repository
-------------------------

    js8call_logger/      the program (standard library only - no pip, no venv)
    js8call-logger       launcher, for running from a checkout
    tests/               unit tests and the replay fixture
    docs/DESIGN.md       decoder facts, API notes, why the pipeline looks like this
    FIXME.txt            features knowingly not built yet
    install.sh           offline installer (prompts for the data directory)
    refresh-bundle.sh    copies an AppImage into bundle/ and checksums it
    bundle/              NOT in git: the 59 MB AppImage (the .sha256 is)

Running from a checkout with no install needs nothing extra:

    ./js8call-logger selftest

Routing audio into the cable
----------------------------

The logger drives `pactl` for you; nothing needs to be typed by hand.
`route-audio --all` is the usual choice: it moves every playing stream onto the
cable, which is right when the browser is the only thing making noise. Four
ways, and they are alternatives:

| command | what happens | when |
|---|---|---|
| `js8call-logger route-audio --copy` | copies whatever plays on your speakers into the cable | nothing is rerouted, no app has to be remembered, but it copies *everything* and follows your speaker volume |
| `js8call-logger route-audio --all` | moves every playing stream to the cable | the WebSDR is the only thing playing |
| `js8call-logger route-audio --list` then `--index N` | moves that one stream | you want the browser only and music left on the speakers |
| `js8call-logger route-audio --source NAME` | loops a **capture source** into the cable: a hardware input, or another program's output | a receiver on the sound card, or sharing audio with another logger — see `--list-sources` |

`cable up` (also run by `start`) creates the null sink and its remap source and
turns monitoring on, so you still hear the WebSDR through the speakers;
`cable up --no-monitor` leaves your speakers alone and `cable down` removes the
whole cable. `cable status` lists the sinks and sources it found.

Monitoring (cable → speakers) and the copy loop (speakers → cable) are the same
loop wired twice: together they run away to 0 dBFS and the decoder hears
nothing but clipping. The logger refuses that combination and tells you which
one to turn off instead of picking for you.

**Two loggers, one receiver.** If another program already owns the audio — say
morse-logger listening to the same browser tab — do not move the stream back
and forth; loop that program's output into our cable instead. Nothing is
stolen, and the other tool's own routing watch keeps working:

    js8call-logger cable up --no-monitor              # it already plays it once
    js8call-logger route-audio --list-sources          # shows other outputs too
    js8call-logger route-audio --source morse-logger_cable.monitor

Stop it with `route-audio --stop-source`. `doctor` is the quickest way to see
which of the two cables a stream is actually on.

**Routing sticks after the first time.** `route-audio` remembers which
applications it saw, and while the logger runs it moves those applications'
new streams back onto the cable every few seconds — so reloading the WebSDR tab
no longer means going deaf. Set `"route_watch": false` in config.json to stop
that.

**What survives what.** Restarting the service (`stop` then `start`) keeps
everything: the cable, its monitor loopback, the copy loop and any source loops
are sound-server modules, not children of the daemon, so they keep running
while the logger is down, and remembered apps are re-routed from config.json.
A reboot or a PipeWire restart loses the modules; `start` rebuilds the cable,
the monitor loopback, the copy loop and the `--source` loops from config.json,
and route-watch moves remembered applications back when they next play. Every
routing verb records what it did: `cable up [--no-monitor]`,
`route-audio --copy|--stop-copy|--source|--stop-source`.

`route-audio` never moves the logger's own monitor loopback: that would feed the
cable into itself and pin the input at 0 dBFS. Browsers remain awkward even so —
Chrome ignores `PULSE_SINK`, and a full browser restart is sometimes the only
way to get a new stream at all.

Commands
--------

| command | meaning |
|---|---|
| `js8call-logger doctor` | full report: data dir, decoder, audio server, cable, what is playing into it and at what level, logger state, bundle |
| `js8call-logger selftest [--offline]` | check the log pipeline **and** decode a known frame into the cable (starts the logger if needed, stops it again); `--offline` skips the decode |
| `js8call-logger cable up\|down\|status` | manage the virtual audio cable (`--no-monitor`, `--channels N` runs N decoders) |
| `js8call-logger route-audio [--list\|--all\|--index N\|--copy\|--stop-copy\|--source NAME\|--list-sources\|--stop-source] [--to N]` | get audio onto the cable |
| `js8call-logger start [-f]` | start in the background (`-f` stays in the foreground) |
| `js8call-logger stop` | stop the logger and the decoder it owns |
| `js8call-logger restart` | stop and start again, keeping the dial and settings |
| `js8call-logger status [--json]` | service, decoders (one per channel), decodes, last decode, restarts, clock offset |
| `js8call-logger tail [-n N] [-f]` | read the text log; `-f` follows it |
| `js8call-logger tail --json` | JSONL view with every field |
| `js8call-logger tail --js8call-debug` | raw frames from the decoder's API, unfiltered |
| `js8call-logger tail --all` | include heartbeats |
| `js8call-logger tail --min-snr -15` | hide weaker decodes (JSONL view) |
| `js8call-logger tail --station K1ABC` | only that correspondent |
| `js8call-logger tune [--dial HZ] [--channel N]` | show or change the dial stamped on new lines |
| `js8call-logger clock [--set] [--dry-run]` | report the measured offset, or move the system clock (needs root) |

Many stations at once
---------------------

Two different things, and they are not the same problem.

**One receiver, many stations.** JS8Call decodes every signal in its passband,
so a busy 40 m evening arrives as many interleaved records from different
stations — no second cable, no second decoder. The logger keeps the log in
transmission order and also writes one file per correspondent, plus
`GROUP-<NAME>.txt` for group traffic (`@ALLCALL`, `@SITREP`, ...), so a single
conversation can be read on its own.

**Several receivers or bands.** `cable up --channels 2` creates a second cable
pair (`js8call-logger_cable_2` and `..._src_2`), records `channels: 2` in
config.json, and the next `start` runs one decoder per channel: channel 1 on
port 2442, channel 2 on 2443, and so on. Each decoder has its own JS8Call
config, its own log and its own waterfall. Records carry `channel`, and the
text line gains a `ch2` tag so two bands can be told apart at a glance:

    2026-11-25 08:31:57  [  7.078MHz snr= +33 coh=100  ch2 N0CALL   ]  ...

Route each receiver onto its own cable (`route-audio --index N --to 2`), and
set the dial per channel with `tune --dial 14078000 --channel 2`. `cable down`
removes the cables; set `"channels": 1` in config.json to go back to one
decoder.

Logs
----

`~/js8call-logger/logs/` (override with `JS8CALL_LOGGER_HOME`):

* `js8call-logger-YYYY-MM-DD.txt` — one line per transmission, in fixed-width
  fields so the text starts in the same column (67) on every line:

      2026-11-24 22:37:41  [  7.078MHz snr= +33 coh=100      N0CALL   ]  @ALLCALL CQ N0CALL: @ALLCALL CQ CQ CQ EM48

  | field | width | meaning |
  |---|---|---|
  | timestamp | 19 | when the transmission started, corrected by the clock offset |
  | dial | 10 | frequency from config or `tune --dial`, `---` if unset |
  | `snr=` | 8 | the decoder's signal report in dB, `--` when it reported none |
  | `coh=` | 7 | 0–100, how much of the text looks like real traffic |
  | channel | 4 | `ch2` when several decoders run, blank otherwise |
  | callsign | 9, left | sender, truncated past nine characters |

  `snr=` measures the **signal** and `coh=` the **text**: a strong carrier with
  two stations overlapping scores high on SNR and low on coherence, which is
  exactly the case worth noticing. Coherence counts tokens that look like a
  callsign, grid locator, short number, JS8 keyword/group or ordinary English
  word, so a mis-decode (`hq tz bv kw`) scores 0 and a clean CQ scores 100.

  Lines are capped at **150 characters including the prefix**. A transmission
  that does not fit continues on a line with the same timestamp and a `...`
  marker; a word too long for a line moves whole to the next one and is only
  truncated if it exceeds a whole line.

* `js8call-logger.jsonl` — same records with every API field (UTC, cmd, grid,
  speed, tdrift, utc_ms), one JSON object per line.
* `js8call-logger.db` — SQLite table `records`:

      sqlite3 js8call-logger.db "select ts, src, text from records where snr > 0"

* `~/js8call-logger/conversations/CALL.txt` — that station's traffic only.

Heartbeats (`@HB`, `HB FN42`) are most of JS8 traffic. They are left out of
the text files and of `tail -f` by default, and always kept in the JSONL and
SQLite. Set `"heartbeats_in_text": true` in config.json, or use `tail --all`,
to keep them.

`"snr_min_text": N` hides weaker decodes from the text file only. Nothing is
ever dropped from the JSONL or the database.

One transmission arrives twice from JS8Call, as `RX.ACTIVITY` and as
`RX.DIRECTED`, both carrying the same UTC millisecond. The logger keeps the
richer directed record and drops the twin.

Clock
-----

Every decode carries JS8Call's own measurement of how far the computer clock
sits from the sender's frame timing. `status` reports the median once it has
three decodes; by default that only corrects the log timestamps. Nothing else
on the system is touched and no privileges are needed.

`js8call-logger clock --set` writes the measured offset into the system clock
instead, so other programs benefit too (needs root: `sudo`, or a sudoers entry
for `date`). `--dry-run` shows what it would do.

What this cannot do: JS8 frames start on 15 second boundaries, so they carry
sub-15-second alignment, not a date. A clock that is hours or days wrong cannot
be repaired from the air; set the date once by hand.

What `start` does by itself
---------------------------

1. Checks and, if necessary, creates the null sink and its remap source, with
   monitoring on so you still hear the receiver through the speakers.
2. Writes `js8call/JS8Call.ini` from config.json — callsign, grid, audio
   device, dial, TCP API on 127.0.0.1:2442, reporting and update checks off.
3. Launches one decoder per channel with `QT_QPA_PLATFORM=offscreen`, each
   with its own `XDG_CONFIG_HOME`/`XDG_DATA_HOME` under
   `~/js8call-logger/js8call/chN/` and its own API port.
4. Waits for the API, confirms the version, sets the dial and offset.
5. Reads every frame, dedupes, renders, and writes text, JSONL, SQLite and the
   correspondent files.
6. Restarts the decoder if it dies, logging each restart in
   `run/restarts.log`, and keeps `run/status.json` current for `status`.
7. Every few seconds, puts the streams of applications `route-audio` saw back
   onto the cable, so a browser reload does not leave the decoder deaf.

Only one daemon can run per data directory: it holds a lock, because two would
fight over port 2442 and silently log nothing.

Licence
-------

GPL-3.0. See `LICENSE`.
