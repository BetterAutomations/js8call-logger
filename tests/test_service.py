"""The systemd unit: what goes in it, and what gets called to install it."""

import os
import tempfile
import unittest
from unittest import mock

from js8call_logger import service


class UnitTest(unittest.TestCase):
    def test_execstart_and_restart(self):
        txt = service.unit_text("/opt/app/js8call-logger")
        self.assertIn("ExecStart=/opt/app/js8call-logger start --foreground", txt)
        self.assertIn("Restart=on-failure", txt)
        self.assertIn("WantedBy=default.target", txt)
        self.assertNotIn("Environment=", txt)


    def test_non_default_home_is_exported(self):
        txt = service.unit_text("/x/js8call-logger", home="/data/logs")
        self.assertIn("Environment=JS8CALL_LOGGER_HOME=/data/logs", txt)

    def test_unit_path_follows_xdg(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
                self.assertEqual(service.unit_path(),
                                 os.path.join(d, "systemd", "user", service.NAME))


class InstallTest(unittest.TestCase):
    def test_install_writes_and_enables(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def fake_run(*args):
                calls.append(args)
                return True, ""
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                 mock.patch.object(service, "_run", side_effect=fake_run):
                ok, _ = service.install("/x/js8call-logger", start=True)
                self.assertTrue(ok)
                self.assertTrue(os.path.exists(service.unit_path()))
                flat = [c[0] for c in calls]
                self.assertEqual(flat, ["daemon-reload", "enable", "restart"])

    def test_no_start_skips_restart(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                 mock.patch.object(service, "_run",
                                   side_effect=lambda *a: (calls.append(a[0]), (True, ""))[1]):
                ok, out = service.install("/x/js8call-logger", start=False)
                self.assertTrue(ok)
                self.assertEqual(calls, ["daemon-reload", "enable"])
                self.assertEqual(out, "enabled")

    def test_remove_deletes_the_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                 mock.patch.object(service, "_run", return_value=(True, "")):
                service.install("/x/js8call-logger")
                ok, out = service.remove()
                self.assertTrue(ok)
                self.assertEqual(out, "removed")
                self.assertFalse(os.path.exists(service.unit_path()))

    def test_available_without_systemctl(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(service.available())

    def test_linger_parsing(self):
        r = mock.Mock(stdout="Linger=yes\n", returncode=0)
        with mock.patch("subprocess.run", return_value=r):
            self.assertTrue(service.linger_enabled())
        r = mock.Mock(stdout="Linger=no\n", returncode=0)
        with mock.patch("subprocess.run", return_value=r):
            self.assertFalse(service.linger_enabled())


    def test_space_in_launcher_is_quoted(self):
        txt = service.unit_text("/cygdrive/c/Amateur radio/app/js8call-logger")
        self.assertIn(
            'ExecStart="/cygdrive/c/Amateur radio/app/js8call-logger" '
            'start --foreground', txt)

    def test_documentation_skipped_when_path_has_space(self):
        txt = service.unit_text("/x/js8call-logger",
                                doc="/cygdrive/c/Amateur radio/README.md")
        self.assertNotIn("Documentation=", txt)
        txt = service.unit_text("/x/js8call-logger", doc="/opt/app/README.md")
        self.assertIn("Documentation=file:/opt/app/README.md", txt)
