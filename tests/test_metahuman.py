import base64
import io
import json
import os
import sqlite3
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from urllib.request import urlopen

from app import metahuman_bridge as bridge
from app import realtime_dialogue as dialogue
from metahuman.host_bridge import handler, parse_wav, Media


def wav_bytes(channels=1):
    result = io.BytesIO()
    with wave.open(result, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"\0\0" * 480 * channels)
    return result.getvalue()


class FakeMedia:
    unavailable = False

    def frame(self):
        if self.unavailable:
            raise RuntimeError("stale")
        return b"\xff\xd8test"

    def play(self, data):
        parse_wav(data)
        return {"accepted": True}


class MetaHumanTests(unittest.TestCase):
    def setUp(self):
        self.media = FakeMedia()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.media, "t" * 32))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.env = patch.dict(os.environ, {"METAHUMAN_BRIDGE_URL": self.url,
                                           "METAHUMAN_BRIDGE_TOKEN": "t" * 32})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_transport_and_audio(self):
        self.assertTrue(bridge.status()["ready"])
        self.assertEqual(base64.b64decode(bridge.frame()), self.media.frame())
        self.assertTrue(bridge.audio(base64.b64encode(wav_bytes()).decode())["accepted"])

    def test_missing_token_and_stale_source_are_not_ready(self):
        with patch.dict(os.environ, {"METAHUMAN_BRIDGE_TOKEN": ""}):
            self.assertFalse(bridge.status()["ready"])
        self.media.unavailable = True
        self.assertFalse(bridge.status()["ready"])
        with self.assertRaises(HTTPError):
            bridge.frame()

    def test_unauthenticated_request_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/frame", timeout=2)
        self.assertEqual(error.exception.code, 401)

    def test_invalid_audio_rejected(self):
        for data in (b"invalid", wav_bytes(2), wav_bytes()[:-2]):
            with self.assertRaises((ValueError, wave.Error, EOFError)):
                parse_wav(data)

    def test_first_audio_opens_stream_and_overlapping_audio_is_rejected(self):
        media = Media.__new__(Media)
        media.frame = Mock(return_value=b"jpeg")
        media.audio_lock = threading.Lock()
        media.audio_device = 3
        media.playback = None
        media.sd = Mock()
        media.sd.get_stream.return_value.active = True
        with patch.dict("sys.modules", {"numpy": Mock()}):
            self.assertTrue(media.play(wav_bytes())["accepted"])
            with self.assertRaises(RuntimeError):
                media.play(wav_bytes())
        media.sd.play.assert_called_once()

    def test_natural_mode_without_musetalk(self):
        with sqlite3.connect(":memory:") as con:
            con.execute("create table settings(key text,value text)")
            con.execute("insert into settings values('operation_mode','realtime_metahuman')")
            self.assertTrue(dialogue.is_realtime(con))
            self.assertTrue(dialogue.is_natural(con))
            self.assertFalse(dialogue.uses_gpu(con))

    def test_settings_do_not_change_when_unavailable(self):
        from app.main import save_settings
        from fastapi import HTTPException
        with patch("app.metahuman_bridge.status", return_value={"ready": False}), patch("app.main.connect") as connect:
            with self.assertRaises(HTTPException) as error:
                save_settings("", "bot", "", 1, "realtime_metahuman")
            self.assertEqual(error.exception.status_code, 409)
            connect.assert_not_called()
