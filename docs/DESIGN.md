Design notes
============

Not needed to use the program. Recorded because every point here cost time to
establish, and several are invisible from the outside.

Why JS8Call and not a decoder of our own
----------------------------------------

JS8 is 8-FSK with LDPC coding on a 15 second grid, and JS8Call decodes every
offset in a ~2.5 kHz passband and every speed (Normal, Fast, Slow, JS8 40,
JS8 60) at once, with a multi-decoder for repeats. Writing that again would be
weeks of work to reach parity with a decoder that already exists, is actively
maintained (JS8Call-improved, development resumed late 2024) and ships as one
self-contained AppImage.

So this project is a supervisor plus a log writer. Everything below is about
driving JS8Call correctly.

Running a GUI program headless
------------------------------

`QT_QPA_PLATFORM=offscreen` is enough: the waterfall, the decoder and the API
all run with no display server. Verified by decoding a replayed frame with no X
server running at all. An Xvfb display also works but is an extra dependency.

Configuration is entirely ours
------------------------------

JS8Call shows a first-run wizard only when its ini is absent, so we write a
complete ini on every start. It is read from `$XDG_CONFIG_HOME/JS8Call.ini`, so
the child is given `XDG_CONFIG_HOME` and `XDG_DATA_HOME` inside our data
directory and the ini is written there — writing it beside that directory is
silently ignored.

Keys that matter:

| key | why |
|---|---|
| `MyCall`, `MyGrid` | JS8Call needs a callsign; we never transmit |
| `DialFreq` | initial dial (metadata; `RIG.SET_FREQ` changes it live) |
| `SoundInName`, `SoundInId`, `AudioInputChannel` | Qt6 multimedia device by name |
| `AllowMonitorInputs=true` | without it monitor sources are not offered |
| `TCPEnabled`, `TCPServer`, `TCPServerPort`, `AcceptTCPRequests` | the API (see below) |
| `WriteLogs`, `MonitorOFF` | ALL.TXT and "start with RX on" |
| `PSKReporter`, `SpotToAPRS`, `CheckForUpdates` | an offline logger must not phone home |
| `WaterfallFPS=2` | cheap CPU saving; the waterfall still feeds the decoder |

The API
-------

TCP on 127.0.0.1:2442. The 2242 in the documentation is the UDP port; both
exist. `docs/API.md` in JS8Call-improved/JS8Call-improved is the reference.

**`AcceptTCPRequests` defaults to false, and when it is off the server accepts
the connection, reads and logs the command, and never answers.** No error is
raised anywhere. That single key cost an hour.

Receptions arrive as `RX.ACTIVITY` (every decode, `value` is the rendered
line) and `RX.DIRECTED` (parsed messages with FROM/TO/CMD/TEXT/GRID/SNR/OFFSET/
DIAL/FREQ/SPEED/TDRIFT/UTC). The same transmission produces both, with an
identical `UTC` millisecond, so dedupe is keyed on `(utc_ms, dial, offset)` and
the directed record wins. Records are held for two seconds so the pair can
merge, and recently written keys are remembered so a late twin does not become
a duplicate line.

`TDRIFT` is the decoder's own measurement of clock error for that frame. On a
frame replayed ~1.1 s off it reported −1.155 s. The median over a handful of
decodes is the correction we apply to log timestamps.

ALL.TXT, which JS8Call writes itself, is WSJT-X format:

    2026-11-25 02:38:00 33 -1.2 1500 A 2rziZsEuvljW 3 N0CALL: @ALLCALL CQ CQ CQ EM48

(date, time, SNR, DT, audio Hz, submode, message id, bits, text). It is useful
for cross-checking, but the API carries more fields, so the logger reads the
API.

Log shape
---------

150 characters is a hard cap on the *rendered* line, prefix included. Wrapping
on the raw text overshoots — morse-logger produced 188-character lines doing
that. Continuation lines repeat the timestamp and carry a `...` marker so they
cannot be mistaken for a new transmission; a token that does not fit moves
whole to the next line and is truncated only when it is longer than a line.

Fields are fixed width so the text begins in the same column everywhere,
missing values are printed as `?` rather than omitted, and heartbeats are
filtered from the text view while every record survives in JSONL and SQLite.

Measurements (Ubuntu 26.04, Core Ultra 9 185H, JS8Call 3.0.3)
-------------------------------------------------------------

| what | value |
|---|---|
| idle CPU, waterfall running, no signal | ~7 % of one core |
| peak RSS | ~160 MB |
| full decode of 31 submodes | ~43 ms |
| API port | 2442/tcp |
| AppImage size | 59 MB, glibc 2.38 required |
| decode from offscreen, no X | verified |

The CPU number scales to roughly half a core on a 2008-era Core 2 Duo, which is
workable but is why `WaterfallFPS` is turned down.

Bundle construction
-------------------

`bundle/JS8Call.AppImage` is built by `refresh-bundle.sh` on a connected
machine and carried over; `install.sh` unpacks it with `--appimage-extract` so
no libfuse and no network are needed at install time. Python is standard
library only, so there are no wheels to bundle.

`tests/fixtures/n0call-cq.raw` is a captured frame (48 kHz mono s16le) replayed
by `selftest --live` at a 15 second boundary; it is what proves a fresh install
end to end without a radio.
