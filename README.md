js8call-logger — JS8 logger
===========================

* Lightweight command-line wrapper around JS8Call for unattended recording of
  JS8 traffic.
* Listens to an audio input, decodes every transmission in the passband and
  files it into timestamped logs.
* Supports any audio source — WebSDR, a receiver's line out, an RTL-SDR
  (generic DVB-T dongle or RTL-SDR Blog V3) driven by `rtl_fm`
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
onto and nothing to retune in the logger. `dial` (or `tune --dial`) only changes the
frequency stamped on new lines.

Everything lives in `~/js8call-logger` (config, `logs/`, `conversations/`,
`run/`) unless the installer was told otherwise. `JS8CALL_LOGGER_HOME`
overrides it.

### Quick start with an RTL-SDR

A generic DVB-T dongle and an RTL-SDR Blog V3 are the same chipset (RTL2832U
plus an R820T2 tuner) and are driven identically by `rtl_fm`; nothing in the
logger changes between them. What differs is the connector, whether there is a
bias tee, and how good the reference crystal is - see the table below.

    # 1. The utilities, unless rtl_test, pactl and parec already run
    sudo dpkg -i bundle/debs/resolute/*.deb      # Xubuntu 26.04
    sudo dpkg -i bundle/debs/stonking/*.deb      # Lubuntu 26.10

    # 2. Take the stick back from the DVB TV driver that claims it at boot
    sudo modprobe -r dvb_usb_rtl28xxu
    echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtl-sdr.conf

    # 3. Does the stick answer?
    js8call-logger sdr --probe        # "Found 1 device(s)" ... Found Rafael R820T tuner

    # 4. Tune it, feed the cable, log
    js8call-logger dial 28.078        # 10 m JS8 calling frequency
    js8call-logger sdr on --gain 40.2
    js8call-logger start
    js8call-logger level              # aim for -20 to -10 dBFS on a band with signals
    js8call-logger tail -f            # Ctrl-C to stop watching, logging continues
    js8call-logger stop

Set the dial *before* `sdr on`: `rtl_fm` is started for whatever dial is
configured, and changing it later restarts the tuner. `--gain`, `--ppm` and
`--serial` are saved in `config.json` and applied at the next start.

`level` on an empty band reads -40 to -25 dBFS and reports `[no signal in the
passband]`, not `[too quiet]`: behind `rtl_fm` there is no volume to be down, so
a low peak means nothing is on frequency. Measured here at 144.178 MHz with a
quiet band, gain against reading:

    gain  0.0  -40.5 dBFS        gain 33.8  -35.5 dBFS
    gain 16.6  -40.3             gain 40.2  -31.7   <- the example above
    gain 28.0  -38.5             gain 49.6  -28.1   <- maximum

-14 dBFS is a target for a feed that has a volume control: a WebSDR or a
line-in. On a stick the receive gain is the only knob, it moves the reading by
a few dB rather than fourteen, and on an empty band no setting reaches the
target. Do not chase the number by pushing the cable's source volume past 100%:
that lifts the reading and changes nothing about decoding, because the
signal-to-noise ratio was fixed at the ADC. `level --set` refuses to go past
100% for that reason.

Watch the level only when something is transmitting, keep it under -10 dBFS so
the tones do not flatten, and prove the front end on a band that has a carrier:
tune a repeater output and the reading jumps to -20..-10 dBFS.

| | generic DVB-T dongle | RTL-SDR Blog V3 |
|---|---|---|
| antenna connector | MCX or IEC, needs an adapter | SMA |
| tuning range | 24-1766 MHz by tuner, below that by direct sampling | same |
| reference crystal | plain crystal, often 20-50 ppm off | 1 ppm TCXO |
| frequency correction | measure, then `--ppm N` | usually leave unset |
| bias tee | none | 4.5 V on the SMA, `--bias-tee on` |
| gain | 0-49.6 dB, start at 40.2 | same values |

**Frequency error.** `rtl_test -p` for five minutes compares the stick with the
system clock and prints a cumulative ppm; that is the number for `--ppm`. At
28 MHz, 1 ppm is 28 Hz, so up to about 5 ppm needs no correction - the JS8
passband is 2.5 kHz and a constant offset only moves a station's position in
it. A 30 ppm dongle is 840 Hz off at 28 MHz and 13 kHz off at 70 cm, which does
matter there. The generic stick used for testing here measured -3 ppm, so
`--ppm` stayed unset.

**Above and below 24 MHz.** 28 MHz sits above the tuner's direct-sampling
threshold, so `--gain` works and nothing extra is passed. Below 24 MHz the
logger adds `-E direct2` (Q branch) by itself; the tuner is then out of the
circuit, so `--gain` has no effect and sensitivity is poor on both sticks - a
long wire on 40 m works, a 2 m whip does not.

**Is the HF input really wired?** `Enabled direct sampling mode, input 2/Q` is a
register write: it says the software asked, not that RF from the connector
reaches the ADC. Plenty of dongles are software-capable and electrically not.
This settles it, using your hand as the signal generator and the tuner path as
a control:

    $ ./tools/hf-input-test.sh
    HF path (direct sampling)  -42.2 dB before, -42.2 dB touching  (+0.0)
    control (tuner path)       -24.3 dB before, -10.8 dB touching  (+13.5)

    PASS          both paths jump: the HF input is wired, so a quiet band is
                  the antenna or the band
    FAIL          only the tuner jumps: the Q branch is not connected on this
                  board, and no antenna will help below 24 MHz
    INCONCLUSIVE  neither jumps: the pin was not touched, or the antenna port
                  is not connected to anything

The logger is stopped for the ~50 seconds it runs and started again after.

**Bias tee.** Only the Blog V3 (and similar boards) has one, and it is 4.5 V,
enough for a mast-head LNA - not a 12 V transverter. Use it only when you know
the board has one: a stick without a tee cannot report that it is missing, so
`--bias-tee on` may succeed and power nothing. `rtl_biast` comes from the same
`rtl-sdr` package.

**Clock.** The stick does not help here: JS8 still needs the computer clock
within about two seconds, so check `timedatectl` before blaming the antenna.

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
gain off. On a stick there is no volume to back off and a quiet reading means
an empty band rather than a fault - see the RTL-SDR quick start for the gain
sweep and why the -14 dBFS target does not apply there.

**No AGC, no noise reduction, no squelch.** AGC pumps between transmissions
and noise reduction is tuned for speech; squelch cuts the quiet frames that
still decode. Turn all three off in the WebSDR or the receiver.

### Signs of trouble

**Normal sounds echo or sound doubled.** A loopback is playing a cable's
monitor into your speakers while the app is already audible there. Check with
`pactl list modules | grep loopback`; `doctor` names it and `cable up
--no-monitor` removes it.

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

A receiver on a USB sound card needs no cable at all — point the decoder at the
card's capture device:

    $ js8call-logger input --list
      alsa_output.pci-0000_00_1f.3.HiFi__Headphones__sink.monitor
    * js8call-logger_cable_src
      alsa_input.pci-0000_00_1f.3.HiFi__Mic1__source

    $ js8call-logger input alsa_input.pci-0000_00_1f.3.HiFi__Mic1__source
    input set to alsa_input.pci-0000_00_1f.3.HiFi__Mic1__source
    restarting so the decoder opens it
    restarted

    $ js8call-logger level
    level: -9.8 dBFS  volume 100%  target -14 dBFS  [clipping]

Everything that measures or trims — `level`, `doctor`, `clock --bootstrap` —
follows the chosen device, so you get the same verdicts as on the cable.
`input --clear` goes back to the cable, which is what the WebSDR and RTL-SDR
paths use. This was exercised against real ALSA capture devices on this machine
(mic reported −90 dBFS digital silence, as expected with nothing plugged in);
an actual receiver feeding it remains untested.

### What is it listening to

`input` with no argument answers the question that an empty log provokes, and
`status` carries the same one-liner:

    $ js8call-logger input
    input    Google Chrome (browser) -> js8call-logger_cable
    device   js8call-logger_cable_src (the cable)
    stream   42  Google Chrome (browser)  "NA5B WebSDR"  -> js8call-logger_cable

    $ js8call-logger input
    input    RTL-SDR: rtl_fm (pid 390769) at 144.178 MHz USB
    device   js8call-logger_cable_src (the cable)
    rtl      pid 390769  /usr/bin/rtl_fm -M usb -f 144178000 -s 240000 -r 48000 -l 0 -g 40.2 -  (gain 40.2)

The five answers it can give, all read from the sound server and `/proc`
rather than from config.json — config says what was asked for, this says what
is happening:

    Google Chrome (browser) -> cable      a WebSDR tab, or any browser audio
    VLC media player (player) -> cable    a file playing into the cable
    HiFi__Line__source looped into cable  a hardware input via a loopback
    RTL-SDR: rtl_fm (pid N) at X MHz USB  the stick, with where it is really tuned
    cable: nothing feeding it             the case worth naming out loud

Runs of several streams are counted rather than listed in the one-liner, and
`doctor`'s "Audio into the cable" section measures the level of whatever this
reports. A configured loop that is listed in `source_loops` but no longer
exists is called out as `missing`, which is what a loop from another logger's
cable looks like after that logger was removed.

### RTL-SDR

A dongle needs nothing installed beyond the `rtl-sdr` package:

    $ js8call-logger sdr --probe
    No supported devices found.

    $ js8call-logger sdr on --gain 49.6 --ppm 12
    source: sdr  gain 49.6  ppm 12
    restarting

With the dongle selected, `scan` answers "which band is open" the only way that
means anything for JS8: it retunes, waits, and counts what the decoder copied.

    $ js8call-logger scan --dwell 60
    surveying 8 frequencies, 60s each (~8 min)
      3.578 MHz     0 decodes    0.0/min  snr  --
      7.078 MHz    11 decodes   11.0/min  snr -12
    best  7.078 MHz  11.0 decodes/min

A strong signal the decoder cannot read scores zero, which is the point: the
question is whether stations are readable, not whether the SDR sees energy.
`--freqs 7.078,14.078` narrows it, and `--apply` leaves the dial on the winner.

`--bias-tee on` powers an LNA through the antenna port (via `rtl_biast`) and is
remembered for the next start; `--bias-tee off` removes it. That is 4.5 V on an
RTL-SDR Blog V3; a generic DVB-T dongle has no bias tee at all, and the stick
cannot tell you so.

    $ js8call-logger sdr
    source       sdr
    rtl_fm       /usr/bin/rtl_fm
    command      /usr/bin/rtl_fm -M usb -f 14078000 -s 240000 -r 48000 -l 0 -E direct2 -g 49.6 -

`-E direct2` is added automatically below 24 MHz (HF needs direct sampling on
the Q branch); above it the command is plain VHF/UHF FM style. The demodulator
pipes into the cable, so `dial`, `level` and everything else keep working
unchanged. It is supervised: if `rtl_fm` dies the daemon restarts it with a
backoff, and the first failure is announced as an event.

The one failure everyone hits is `No supported devices found` while the stick
is plugged in. That is the kernel's DVB driver holding it, and the event says so:

    # sdr stopped (no supported device: the kernel DVB driver is probably
      holding it - sudo modprobe -r dvb_usb_rtl28xxu, and blacklist it ...)

`--serial` picks a stick when several are plugged in. `scan` retunes it across
the calling frequencies and counts what decodes, which is the only "is this
band open" answer that means anything for JS8 (see above).

Install (offline)
-----------------

    sudo ./install.sh                  # install; asks where logs should live
    ./install.sh --prefix ~/.local     # user-local, no sudo

`install.sh` copies the program, unpacks the bundled JS8Call AppImage with
`--appimage-extract` (so no libfuse is needed), and writes a first-run config
pointing at the unpacked decoder. There is exactly one launcher, at
`/opt/js8call-logger/js8call-logger`, with a **symlink** to it in
`/usr/local/bin` (or `<prefix>/bin` for a `--prefix` install) — nothing is
copied into two places. Nothing is
downloaded. It ends by telling you if the command directory is not on your
`PATH`.

The bundle is one file: `bundle/JS8Call.AppImage` (~59 MB, 3.0.3, checksum in
`bundle/JS8Call.AppImage.sha256`). Refresh it on a connected machine with:

    ./refresh-bundle.sh /path/to/JS8Call-v3.0.3-x86_64.AppImage

The program itself needs nothing else, but a stock flavour does not install the
audio and SDR command-line tools, so they are carried too, one directory per
Ubuntu release (they are built against that release's libc and will not mix):

    bundle/debs/resolute/   21 packages, 5.7 MB - Ubuntu 26.04, so Xubuntu 26.04
    bundle/debs/stonking/   21 packages, 5.7 MB - Ubuntu 26.10, so Lubuntu 26.10

    sudo dpkg -i bundle/debs/resolute/*.deb      # on the air-gapped machine

That is `pulseaudio-utils` (pactl, pacat, parec), `rtl-sdr` (rtl_fm, rtl_test,
rtl_biast, rtl_power) and the non-base libraries they pull in (`libpulse0`,
`librtlsdr0`, `libusb-1.0-0`, `libsndfile1` and friends). Packages every
install already has - libc6, libgcc, libsystemd and the other required or
important ones - are deliberately left out, so nothing can try to replace the
running libc offline. `MANIFEST.txt` inside each directory lists every package
with its version and priority; `SHA256SUMS` covers the debs.

Refresh both directories on a connected machine with:

    ./tools/fetch-offline-packages.sh resolute    # 26.04
    ./tools/fetch-offline-packages.sh stonking    # 26.10

The two names are Ubuntu's codenames; `lsb_release -cs` on the target prints
the one it needs. `notify-send` is not in the bundle: it is optional, and the
`libnotify-bin` package would drag in most of GTK for a one-line toast.

`tests/fixtures/live-2026-11-26T1622Z-30s.wav` is 30 s of the live feed,
captured while the log was filling and verified by replay (93 % of the expected
text: `js8call-logger selftest --recording tests/fixtures/live-2026-11-26T1622Z-30s.wav
--expect "KD2UWR HEARTBEAT SNR"`).

`tests/fixtures/n0call-cq.raw` is a recorded JS8 frame that `selftest` plays
into the cable, so a fresh install can prove itself with no radio attached.
`tests/fixtures/websdr-2026-11-25T1453Z-30s.wav` is 30 s of real off-air audio
from a busy band; it is what proves `clock --bootstrap` refuses when there is no
clean slot structure to measure.

`selftest --recording FILE --expect TEXT` replays any wav/raw instead of the
built-in frame, scores the decode against the expected text as a similarity
ratio (morse-logger's measure) and passes at 50 % or better; it pauses source
loops first so the recording has the cable to itself.

What is in the repository
-------------------------

    js8call_logger/      the program (standard library only - no pip, no venv)
    js8call-logger       launcher, for running from a checkout
    tests/               unit tests, the replay fixture, 30 s of real audio
    tools/audit_verbs.py every verb documented, no stale docs
    tools/fetch-offline-packages.sh  downloads bundle/debs/<suite> for a release
    docs/DESIGN.md       decoder facts, API notes, why the pipeline looks like this
    docs/STATUS.md       what is built, and the evidence for each claim
    docs/HARDWARE-TESTS.md    the checklist for when a radio/dongle arrives
    FIXME.txt            features knowingly not built yet
    install.sh           offline installer (prompts for the data directory)
    refresh-bundle.sh    copies an AppImage into bundle/ and checksums it
    bundle/              NOT in git: the 59 MB AppImage and bundle/debs/
                         (their .sha256 / SHA256SUMS are)

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

**Two programs must not both route the same app.** If another tool watches the
same application (morse-logger does, if it is running), the two watchdogs move
the stream back and forth every few seconds and the audio flaps. The logger
notices after three moves in a minute, says so in `run/daemon.log`, and leaves
that application alone for five minutes rather than churning. The real fix is
to share instead of compete: loop the other program's cable into ours and clear
`route_apps`, so only one program claims the stream.

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
| `js8call-logger status [--json]` | service, decoders (one per channel), decodes, last decode, restarts, clock offset, and what is feeding it |
| `js8call-logger tail [-n N] [-f]` | read the text log; `-f` follows it |
| `js8call-logger tail --json` | JSONL view with every field |
| `js8call-logger tail --js8call-debug` | raw frames from the decoder's API, unfiltered |
| `js8call-logger tail --all` | include heartbeats |
| `js8call-logger tail --min-snr -15` | hide weaker decodes (JSONL view) |
| `js8call-logger tail --station K1ABC` | only that correspondent |
| `js8call-logger dial [FREQ] [--offset HZ] [--clear]` | show or set the dial stamped on new lines; `7078`, `7078.00`, `7.078` and `7.078MHz` all work |
| `js8call-logger tune [--dial HZ] [--channel N]` | lower-level: dial and channel settings straight to one decoder |
| `js8call-logger prune [--days N] [--dry-run] [--delete]` | archive (or delete) logs older than N days |
| `js8call-logger migrate [--dry-run]` | fold legacy flat `GROUP*.txt` conversation files into `conversations/groups/` |
| `js8call-logger sdr [on\|off] [--probe] [--gain G] [--ppm N] [--serial S] [--bias-tee on\|off]` | use an RTL-SDR as the input; `--probe` asks `rtl_test` what it sees |
| `js8call-logger scan [--dwell S] [--freqs LIST] [--apply] [--list]` | survey the JS8 calling frequencies and say which one is open |
| `js8call-logger input [NAME] [--list] [--clear]` | with no argument, what the decoder is listening to right now (stick, browser tab, player, looped line-in, nothing); with a NAME, choose the capture device |
| `js8call-logger level [--set] [--target DB] [--channel N] [--dry-run]` | measure the input level and trim the source volume toward the target |
| `js8call-logger clock [--set] [--dry-run]` | report the measured offset, or move the system clock (needs root) |
| `js8call-logger service [install\|remove\|status]` | systemd user unit so it starts at boot |

Two channels are proven by a script, not a unit test, since it needs a running
decoder on each cable:

    python3 tools/two-channel-check.py

It brings up two cables, plays the known frame into both decoders at once,
checks that one record lands on ch1 and one on ch2, then puts the install back
the way it found it.

## Starting at boot

    js8call-logger service install     # systemd --user unit, enabled and started
    js8call-logger service status      # systemctl --user status --no-pager
    js8call-logger service remove      # disable and delete it

The unit is `~/.config/systemd/user/js8call-logger.service`, run as
`<launcher> start --foreground` with `Restart=on-failure`, so a crashed daemon
returns by itself. `install.sh --service` does the same during installation.

A *user* unit only runs while that user has a session. For a laptop that logs
unattended after boot, allow lingering once:

    sudo loginctl enable-linger $USER

`service` refuses with a message when there is no user systemd to talk to
(a container, a CI runner) rather than writing a unit nothing will read.

## Automatic adjustment

Some things are repaired without being asked, because the failure is silent
and the fix is safe. These run in the daemon's own threads and re-read
config.json each pass, so a change takes effect without a restart.

| what | when | setting |
|---|---|---|
| level trim | the cable's peak leaves -20..-10 dBFS | `level_auto` (off), `level_auto_interval_s` 20 |
| decoder restart | audio is flowing but nothing decodes for 300 s | `watchdog` (on), `watchdog_silence_s`, `watchdog_cooldown_s` 600, `watchdog_max_restarts` 4 |
| cable rebuild | the sink or source disappears (PipeWire restarted) | `cable_watch` (on), `cable_watch_interval_s` 30 |
| dial restore | the decoder's frequency drifts from the configured dial | `dial_hz` set |
| prune | `retention_days` old files, at most every `prune_interval_h` | `prune_auto` (off), `prune_interval_h` 24 |
| decode re-sweep | a reminder only, after `decode_auto_days` | `decode_auto` (off), `decode_auto_days` 14 |

Turn each on or off from the command line:

    js8call-logger level --auto          # keep the level trimmed
    js8call-logger level --no-auto
    js8call-logger tune --auto           # remind when a decode sweep is due
    js8call-logger tune --no-auto

`status` and `doctor` print which of these are armed, and `doctor` warns when a
decode-depth re-sweep is due.

When the logger repairs itself it says so, three times over, because a silent
fault is worse than a noisy one:

    2026-11-26 12:00:02  # dial watch: decoder was at 7.078 MHz, restored 14.078 MHz

- a `#` line in the daily text file (never mistakable for a transmission, and
  the only line in that file that does not follow the fixed-width format)
- the same line in `run/daemon.log`
- a desktop notification through `notify-send`, when one can be delivered
- `status` keeps the last one with its age: `event  dial watch: ... (23s ago)`

Identical events within ten minutes are not repeated. `"notify": false` drops
the desktop part only.

One deliberate omission. Band scan/hop is **not** automatic; it needs an SDR
that can retune, which the cable and WebSDR sources cannot.

The decode-depth sweep *is* automated — with `decode_auto` on it runs at the
next start when one is due (every `decode_auto_days`, default 14). It runs
before the decoders exist, pauses any source loops, and keeps the winner only
if it beats the profile in use by 10 %, because the measurement is synthetic
and a narrow win is noise. It skips itself when the input is a sound card
rather than the cable, since it has no way to inject the fixture.

Slow machines
-------------

The intervals are not fixed: the CPU is measured once and the watchdogs stretch
to fit it.

    $ js8call-logger status | grep cpu
    cpu          fast (0.0282 s) intervals x1

- The measurement is a fixed integer loop, best of three, cached in
  `run/cpu.json`. Model names lie - a throttled laptop or a VM reports the
  rating of a CPU it is not actually running at - so the work is what gets
  timed.
- Under 0.055 s is `fast`; up to 0.100 s is `medium` (x2); slower is `slow`
  (x4, where a 1.2 GHz Core 2 Duo SU9300 lands).
- The scale stretches the watchdog silence and cooldown, the cable watch, the
  route watch, level trimming, the status write and the decode poll. Nothing
  else changes: the decoder is the expensive part, and it is not ours.
- `js8call-logger doctor --benchmark` re-measures if the machine changed (a VM
  moved host, a laptop started throttling).
- The scale is applied when values are *read*, never written back, so saving
  config.json cannot multiply it in twice. The stored numbers stay yours.

Input level
-----------

JS8 wants peaks between -20 and -10 dBFS. `doctor` warns when the cable is
clipping or far too quiet, and `level` measures and fixes it:

    js8call-logger level                 # measure and report, change nothing
    js8call-logger level --set           # trim the source volume toward the target
    js8call-logger level --set --dry-run # say what would change

It reads the cable's peak level and the source's current volume, then works out
the percentage change in dB, capped at 6 dB per call (`level_max_step_db`) so a
noisy measurement cannot overshoot. It never amplifies past 100% and never
touches digital silence, where raising the volume would only amplify noise.
The target is `level_target_db` in config.json (-14 dBFS by default).

The trim is applied to the cable's *source* volume, which is the last stage
before the decoder, so it is the right place to correct a hot feed and the
wrong place to fix a quiet one: if the source application is already at 100%,
`level` says so and you have to raise the volume there. With an RTL-SDR feed
there is no source application - only the tuner gain changes the level - so
a low reading is reported as `[no signal in the passband]` and the advice names
`rtl_gain` (`sdr --gain`), not a nonexistent volume. Set `"level_auto": true`
to have the daemon re-check every 20 seconds; it is off by default because
changing a volume the user set is intrusive.

Decode depth
------------

JS8Call's refinement passes are environment switches rather than settings:

    JS8_DISABLE_DEEP_SEARCH      search extra candidates each period
    JS8_DISABLE_LDPC_RESCUE      retry a failed frame with another LDPC seed
    JS8_DISABLE_COHERENT_DATA    coherent re-demodulation of data frames
    JS8_DISABLE_REDEMOD          aided re-demodulation after a first decode
    JS8_DISABLE_FREQ_TRACKING    follow slow frequency drift
    JS8_DISABLE_TIMING_TRACKING  follow symbol timing drift

`tune --decoder` measures them instead of guessing:

    js8call-logger tune --decoder --dry-run          # what would be tested
    js8call-logger tune --decoder --levels -20,-23   # measure, do not save
    js8call-logger tune --decoder --apply            # keep the winner

It plays the known fixture at real signal-to-noise ratios — white noise mixed
in, not merely a lower volume, because a quiet clean signal is still an easy
decode — counts the decodes per transmission and the CPU the decoder burned,
and ranks profiles on decodes first, CPU second. The logger is stopped and any
source loops paused for the run, then restored.

Measured here (2026-11-25): at −20 dB SNR every profile decoded, at −23 dB
none did, and CPU was 0.83–0.85 s per 15 s period for all three profiles. So
no default changed: all passes stay on. Nothing measurable disappeared by
turning them off, and they matter in conditions one clean frame cannot
reproduce. On a slow machine the same command tells you what *your* CPU gives
up — `decode_env` in config.json takes the answer.

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

    2026-11-25 08:31:57  [  7.078MHz snr= +33 coh=100  ch2 1500Hz N0CALL   ]  ...

Route each receiver onto its own cable (`route-audio --index N --to 2`), and
set the dial per channel with `tune --dial 14078000 --channel 2`. `cable down`
removes the cables; set `"channels": 1` in config.json to go back to one
decoder.

Logs
----

`~/js8call-logger/logs/` (override with `JS8CALL_LOGGER_HOME`):

* `js8call-logger-YYYY-MM-DD.txt` — one line per transmission, in fixed-width
  fields so the text starts in the same column (74) on every line:

      2026-11-24 22:37:41  [  7.078MHz snr= +33 coh=100      1500Hz N0CALL   ]  @ALLCALL CQ N0CALL: @ALLCALL CQ CQ CQ EM48

  | field | width | meaning |
  |---|---|---|
  | timestamp | 19 | when the transmission started, corrected by the clock offset |
  | dial | 10 | frequency from `dial` or config, `---` if unset |
  | `snr=` | 8 | the decoder's signal report in dB, `--` when it reported none |
  | `coh=` | 7 | 0–100, how much of the text looks like real traffic |
  | channel | 4 | `ch2` when several decoders run, blank otherwise |
  | tone | 6 | audio offset inside the passband, `-Hz` when unknown |
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

  A line ending in **` …`** is one frame of a longer transmission, not the
  whole message. A JS8 frame carries about a dozen characters, so a sender's
  sentence arrives as several frames; when only one of them decodes, this log
  says so instead of leaving what looks like a complete short message. The
  trailing marker is our addition — the wrap `...` always starts a line, the
  fragment `…` always ends one, so the two cannot be confused. Frame types 1
  and 2 (compound message header and body) are always marked; type 0 is marked
  when its text fills a frame, which is what free-text chunks look like, while
  a complete "73" or a heartbeat is not. `frame` and `fragment` are stored as
  columns so you can filter (`where fragment = 1`).

* `js8call-logger.jsonl` — same records with every API field (UTC, cmd, grid,
  speed, tdrift, utc_ms), one JSON object per line.
* `js8call-logger.db` — SQLite table `records`:

      sqlite3 js8call-logger.db "select ts, src, text from records where snr > 0"

* `~/js8call-logger/conversations/CALL.txt` — that station's traffic only.
* `~/js8call-logger/conversations/groups/GROUP.txt` — traffic addressed to a
  group (`@ALLCALL`, `@SITREP`, ...). A record can appear in both the sender's
  file and the group file, because "who said it" and "where it was said" are
  different questions; heartbeats appear in neither. Group names are
  uppercased and stripped of punctuation, so `@GHOST-NET` becomes
  `groups/GHOSTNET.txt`. Version 0.2 wrote one flat `GROUPALLCALL.txt`
  instead; `js8call-logger migrate` folds those into `groups/` in date order,
  drops duplicates, and removes the old file once the new one is written. Stop
  the logger first.

Heartbeats (`@HB`, `HB FN42`) are most of JS8 traffic. They are left out of
the text files and of `tail -f` by default, and always kept in the JSONL and
SQLite. Set `"heartbeats_in_text": true` in config.json, or use `tail --all`,
to keep them.

`"snr_min_text": N` hides weaker decodes from the text file only. Nothing is
ever dropped from the JSONL or the database.

One transmission arrives twice from JS8Call, as `RX.ACTIVITY` and as
`RX.DIRECTED`, both carrying the same UTC millisecond. The logger keeps the
richer directed record and drops the twin.

Shorthand
---------

JS8 operators mostly type plain English, so the glossary is deliberately
short: JS8's own keywords (`@ALLCALL`, `HEARTBEAT`, `CQ`, `MSG`, `ACK`, `SNR`,
`GRID`, `DE`), the Q-signals, and the classic abbreviations that still turn up
(`73`, `TNX`, `PSE`, `FER`, `GL`, `QSL`, `QTH`, `BURO`...). Ordinary short
words — NO, TO, IN, ID, ON — are left alone, as are callsigns and group names
other than `@ALLCALL` and `@HB`.

Expansion is annotated, never overwritten: the readable text first, the token
that was actually sent in brackets.

    @ALLCALL CQ N0CALL: @ALLCALL CQ CQ CQ EM48 ♢
    all stations[@ALLCALL] calling all stations[CQ] N0CALL: ...

That way a wrong expansion is visible right next to the token it came from, and
the raw decode is what stays in the JSONL (`text`) with the annotated version
beside it (`text_xlated`). A token repeated in a run is expanded once, so
`CQ CQ CQ` does not become three glosses. Lines below the coherence threshold
(45 by default) are never annotated — glossing a garbled decode only makes it
harder to read.

In config.json:

    "expand": true,                # annotate the text log
    "expand_mode": "gloss",        # or "replace" for clean prose, no brackets
    "expand_min_coherence": 45

Each new daily file starts with a one-line legend, because `meaning[TOKEN]` is
not self-evident a month later.

Retention
---------

Nothing is ever pruned unless you ask: `retention_days` defaults to 0, which
means keep everything.

    js8call-logger prune --days 90 --dry-run     # what would go
    js8call-logger prune --days 90               # archive it

The default is to **archive**, not delete: old daily files and conversation
files are gzipped into `~/js8call-logger/archive/`, old JSONL lines are
archived the same way, and matching SQLite rows are removed and the database
vacuumed. `--delete` removes instead of archiving. `--days` overrides
`retention_days` in config.json, so a one-off cleanup does not have to change
your policy.

Stop the logger first. A running daemon holds the JSONL open, so rewriting it
underneath would lose whatever it writes next; `prune` refuses while it is
running unless you pass `--force` and know why.

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

Bootstrapping without a decode
-------------------------------

TDRIFT needs a decode, and a decode needs the clock within about 2 s - so a
clock 30 s out is a deadlock: audio flows, nothing decodes, no offset is ever
measured. `clock --bootstrap` breaks it by using the frame grid itself:

    $ js8call-logger clock --bootstrap --seconds 45
    windows      156 in 45s
    phase        +1.43s off the 15 s boundaries
    structure    3587.00x frame-vs-gap
    correction   -1.43s to the system clock
    note         includes roughly a second of audio pipeline delay
    note         add --set to apply it (needs root)

It tracks the strongest 31 Hz bin per 128 ms window and correlates that series
with the 12.6 s frame against each of the 150 possible phases. Broadband level
cannot do this: a live −20 dB signal moves the RMS by 0.5 %, but a single bin
sees about −1 dB, and averaging a hundred windows finds it.

Two limits, both measured rather than assumed:

- **It needs one strong, isolated signal.** With the test fixture alone on the
  cable the structure ratio is thousands to one; on a busy 20 m band it is
  1.14x, and the command refuses (exit 2) rather than inventing a correction.
- **About a second of the phase is audio pipeline delay**, not clock error. It
  varies by a few tenths between runs, which is fine: the point is to get
  inside the 2 s window, and the TDRIFT median refines the offset from there.

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
