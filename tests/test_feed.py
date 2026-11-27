"""What is feeding the decoder: the classifier and the wording.

None of these call pactl; they take the gathered rows, so the rules can be
tested without a sound server. The gathering itself is feed.describe().
"""

import unittest

from js8call_logger import config, feed


def cfg(**kw):
    base = dict(config.DEFAULTS, cable="js8call-logger_cable")
    base.update(kw)
    return base


def stream(app, media="Playback Stream", index=1, sink="js8call-logger_cable"):
    return {"index": index, "app": app, "stream": media, "sink_name": sink}


class KindTest(unittest.TestCase):
    def test_browsers(self):
        for app in ("Google Chrome", "Firefox", "Chromium", "Brave"):
            self.assertEqual(feed.kind(app), "browser", app)

    def test_players(self):
        for app in ("VLC media player", "mpv", "Rhythmbox", "Spotify"):
            self.assertEqual(feed.kind(app), "player", app)

    def test_unknown_app_gets_no_claim(self):
        self.assertEqual(feed.kind("some-dsp-tool"), "")

    def test_a_browser_tab_named_in_the_stream_still_counts(self):
        # Chrome often reports application.name as the binary; the media name
        # is the tab title, which is the only place "WebSDR" appears.
        self.assertEqual(feed.kind("Chromium", "NA5B WebSDR"), "browser")


class SummaryTest(unittest.TestCase):
    def test_chosen_device_wins(self):
        text = feed.summary(cfg(input_device="alsa_input.usb-X"))
        self.assertIn("usb-X", text)
        self.assertIn("chosen capture device", text)

    def test_running_stick_reports_where_it_is_tuned(self):
        rtl = [{"pid": 42, "freq": 144178000, "gain": "40.2", "ppm": None}]
        self.assertEqual(feed.summary(cfg(source="sdr"), rtl=rtl),
                         "RTL-SDR: rtl_fm (pid 42) at 144.178 MHz USB")

    def test_configured_but_dead_stick_is_named_as_such(self):
        self.assertIn("not running", feed.summary(cfg(source="sdr"), rtl=[]))

    def test_one_browser_stream(self):
        text = feed.summary(cfg(), streams=[stream("Google Chrome")])
        self.assertIn("Google Chrome (browser)", text)
        self.assertIn("-> js8call-logger_cable", text)

    def test_several_streams_are_counted_not_listed(self):
        rows = [stream("Google Chrome"), stream("VLC media player", index=2)]
        text = feed.summary(cfg(), streams=rows)
        self.assertIn("2 programs", text)
        self.assertIn("Google Chrome", text)

    def test_hardware_loop_is_named(self):
        rows = [("12", "alsa_input.pci-0000_00_1f.3.HiFi__Line__source",
                 "js8call-logger_cable")]
        text = feed.summary(cfg(), loop_rows=rows)
        self.assertIn("HiFi__Line__source", text)
        self.assertIn("looped into", text)

    def test_nothing_feeding_it_says_so(self):
        self.assertIn("nothing feeding it", feed.summary(cfg()))


class DetailsTest(unittest.TestCase):
    def test_device_line_names_what_the_decoder_opens(self):
        lines = feed.details(cfg())
        self.assertEqual(lines[0], "device   js8call-logger_cable_src (the cable)")

    def test_stream_line_carries_the_tab_title_when_there_is_one(self):
        lines = feed.details(cfg(), streams=[
            stream("Chromium", "NA5B WebSDR - 7.078", index=17)])
        line = next(l for l in lines if l.startswith("stream"))
        self.assertIn("17", line)
        self.assertIn("(browser)", line)
        self.assertIn('"NA5B WebSDR - 7.078"', line)

    def test_stick_line_is_the_real_command(self):
        rtl = [{"pid": 7, "freq": 28078000, "gain": "40.2", "ppm": None,
                "cmd": ["rtl_fm", "-M", "usb", "-f", "28078000"]}]
        line = next(l for l in feed.details(cfg(source="sdr"), rtl=rtl)
                    if l.startswith("rtl"))
        self.assertIn("pid 7", line)
        self.assertIn("rtl_fm -M usb -f 28078000", line)
        self.assertIn("gain 40.2", line)

    def test_a_configured_loop_that_is_missing_is_reported(self):
        cfg_ = cfg(source_loops=["morse-logger_cable.monitor"])
        line = next(l for l in feed.details(cfg_, loop_rows=[])
                    if l.startswith("missing"))
        self.assertIn("morse-logger_cable.monitor", line)
        self.assertIn("configured", line)

    def test_a_present_loop_is_not_reported_missing(self):
        rows = [("3", "alsa_input.usb-X", "js8call-logger_cable")]
        cfg_ = cfg(source_loops=["alsa_input.usb-X"])
        self.assertFalse([l for l in feed.details(cfg_, loop_rows=rows)
                          if l.startswith("missing")])


class RtlArgsTest(unittest.TestCase):
    def test_frequency_gain_and_ppm_are_read_from_the_argv(self):
        info = feed.parse_rtl_args(["rtl_fm", "-M", "usb", "-f", "7078000",
                                    "-s", "240000", "-g", "49.6", "-p", "3"])
        self.assertEqual(info["freq"], 7078000)
        self.assertEqual(info["gain"], "49.6")
        self.assertEqual(info["ppm"], "3")

    def test_no_options_is_not_an_error(self):
        self.assertEqual(feed.parse_rtl_args(["rtl_fm"])["freq"], None)

    def test_a_non_numeric_frequency_is_ignored(self):
        self.assertIsNone(feed.parse_rtl_args(["rtl_fm", "-f", "auto"])["freq"])


if __name__ == "__main__":
    unittest.main()
