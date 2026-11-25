js8call-logger - record every JS8 conversation you can hear
==========================================================

Headless logger for the JS8 mode. It runs JS8Call's own decoder with no
display, listens to a virtual audio cable (a WebSDR tab, a USB sound card, or
later an RTL-SDR), and writes every transmission it decodes to a daily text
log, a JSONL stream, a SQLite database and one file per correspondent.

Requires: Linux with PipeWire or PulseAudio, python3 >= 3.10, and glibc 2.38+
(Ubuntu 23.10 / Debian 13 or newer). The bundled JS8Call is a 59 MB AppImage
and needs nothing else from the network.


Quick start
-----------

1. Install (offline):

       ./install.sh                 # to /opt/js8call-logger, needs sudo
       ./install.sh --prefix ~/.local   # user-local, no sudo

2. Point some audio at the cable. For a WebSDR, play the page in your browser
   and move the stream onto the cable:

       pactl list sink-inputs            # find the index
       pactl move-sink-input <idx> js8call-logger_cable

   The first `start` creates the cable if it is missing.

3. Tune the receiver (the WebSDR page, or your radio) to a JS8 frequency and
   put it in USB, roughly 3 kHz wide, with the JS8 signal inside the passband.

4. Start and watch:

       js8call-logger start
       js8call-logger status
       js8call-logger tail -f

Stop with `js8call-logger stop`.


Commands
--------

    start [-f]              start the logger (background; -f for foreground)
    stop                    stop the logger and the decoder it owns
    status                  service, decoder, decodes, restarts, clock offset
    tail [-f] [-n N]        show the text log; -f follows it
    tail --json             JSONL view (all fields)
    tail --js8call-debug    raw frames from the JS8Call API, unfiltered
    tail --all              include heartbeats
    tail --min-snr -15      hide weaker decodes (JSONL view)
    tail --station K1ABC    only that correspondent
    tune [--dial HZ]        show or change the dial frequency
    clock [--set]           report the measured clock offset; --set moves the
                            system clock (needs root)
    doctor                  check python, cable, decoder, API port
    selftest                offline checks of the log pipeline


What gets written
-----------------

    ~/js8call-logger/logs/js8call-logger-YYYY-MM-DD.txt   human-readable
    ~/js8call-logger/logs/js8call-logger.jsonl            every record
    ~/js8call-logger/logs/js8call-logger.db               SQLite (same records)
    ~/js8call-logger/conversations/K1ABC.txt              per correspondent
    ~/js8call-logger/conversations/GROUP-ALLCALL.txt      per group

Text lines look like:

    2026-11-24 22:34:26   7.078MHz +33dB 1500Hz N0CALL>@ALLCALL @ALLCALL CQ ...

Lines are capped at 150 characters including the timestamp and metadata. A
transmission that does not fit continues on a line carrying the same
timestamp and a "..." marker, so a continuation can never be mistaken for a
new transmission.

Heartbeats (@HB / "HB FN42") are the bulk of JS8 traffic. They are left out of
the text logs and of `tail -f` by default, and always kept in the JSONL and
database. Set `"heartbeats_in_text": true` in config.json to keep them in the
text file, or use `tail --all`.

One transmission normally arrives twice from JS8Call (as RX.ACTIVITY and as
RX.DIRECTED); the logger keeps the richer directed record and drops the twin.


Clock
-----

Every decode carries JS8Call's own measurement of how far the computer clock
sits from the sender's frame timing. `status` reports the median once it has
three decodes. By default this only corrects the log timestamps: nothing else
on the system is touched, and no privileges are needed.

`js8call-logger clock --set` writes the measured offset into the system clock
instead (one sudo, or a sudoers entry for `date`). Use it when the laptop's
clock has drifted and you want other programs to benefit too.

What this cannot do: JS8 frames start on 15 second boundaries, so they carry
sub-15-second alignment, not a date. A clock that is hours or days wrong
cannot be repaired from the air; set the date once by hand.


Troubleshooting
---------------

- `status` says running but decodes stay at 0: nothing is arriving on the
  cable, or the receiver is not tuned to JS8. `tail --js8call-debug` shows
  whether the decoder is receiving anything at all.
- `doctor` fails on "api port free": something (probably an older run) holds
  port 2442. `js8call-logger stop` clears it.
- Decoding needs the clock within about 2 seconds. If the log is empty while
  audio is clearly present, check `timedatectl`.
- The decoder is a GUI program wearing a headless hat. If it ever stops
  answering, the supervisor restarts it and logs the restart in
  `~/js8call-logger/run/restarts.log`.


Not built yet
-------------

See FIXME.txt: RTL-SDR input, band scanning, input-level trimming, clock
bootstrap for badly wrong clocks, glossary expansion.
