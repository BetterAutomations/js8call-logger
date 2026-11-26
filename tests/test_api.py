"""The API client while another thread closes the socket underneath it."""

import socket
import unittest
from unittest import mock

from js8call_logger.api import ApiError, Js8Api


class ClosedUnderneathTest(unittest.TestCase):
    def test_close_during_read_is_not_an_attribute_error(self):
        left, right = socket.socketpair()
        right.send(b'{"type":"x"}\n')
        api = Js8Api("127.0.0.1", 1)
        api.sock = left

        def fake_select(r, w, x, t):
            api.close()          # the watchdog restarted the decoder
            return (r, [], [])   # and select had already said "ready"
        with mock.patch("select.select", fake_select):
            # a clean ApiError the daemon can reconnect from, not an
            # AttributeError that kills the process
            with self.assertRaises(ApiError):
                api.read(0.01)

    def test_read_when_closed_raises_apierror(self):
        api = Js8Api("127.0.0.1", 1)
        with self.assertRaises(ApiError):
            api.read(0.01)

    def test_send_when_closed_raises_apierror(self):
        api = Js8Api("127.0.0.1", 1)
        with self.assertRaises(ApiError):
            api.send("RIG.GET_FREQ")
