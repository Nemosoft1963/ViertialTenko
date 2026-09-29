import base64
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.realtime_browser import MEDIA_BRIDGE_SCRIPT
from metahuman.host_bridge import parse_wav


class MetaHumanBrowserTests(unittest.TestCase):
    def test_external_video_audio_and_disconnect_without_portrait_fallback(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            try:
                context = browser.new_context(permissions=["camera", "microphone"])
                page = context.new_page()
                page.route("http://localhost:9876/**", lambda r: r.fulfill(body="<html></html>", content_type="text/html"))
                page.goto("http://localhost:9876/")
                jpeg = page.evaluate("()=>{const c=document.createElement('canvas');c.width=1280;c.height=720;const x=c.getContext('2d');x.fillStyle='green';x.fillRect(0,0,1280,720);return c.toDataURL('image/jpeg').split(',')[1];}")
                ready = [True]
                def frame():
                    if not ready[0]:
                        raise RuntimeError("renderer disconnected")
                    return jpeg
                received = []
                page.expose_function("__tenkoMetaHumanFrame", frame)
                page.expose_function("__tenkoMetaHumanAudio", lambda data: received.append(parse_wav(base64.b64decode(data))))
                page.add_script_tag(content=(Path(__file__).resolve().parents[1] / "app/metahuman.js").read_text())
                page.evaluate(MEDIA_BRIDGE_SCRIPT)
                page.evaluate("localStorage.setItem('tenkoOperationMode','realtime_metahuman')")
                page.evaluate("async()=>{window.s=await navigator.mediaDevices.getUserMedia({video:true,audio:true});}")
                self.assertTrue(page.evaluate("window.__tenkoMetaHumanReady"))
                self.assertEqual(page.evaluate("[s.getVideoTracks()[0].getSettings().width,s.getVideoTracks()[0].getSettings().height]"), [1280,720])
                page.evaluate("async()=>{const b=__tenkoAudioContext.createBuffer(1,4800,48000);await __tenkoPlayAudio(tenkoPCMToWav(b));}")
                self.assertEqual(len(received), 1)
                ready[0] = False
                page.wait_for_function("window.__tenkoMetaHumanReady === false")
                with self.assertRaises(Exception):
                    page.evaluate("navigator.mediaDevices.getUserMedia({video:true})")
                page.evaluate("s.getTracks().forEach(t=>t.stop())")
            finally:
                browser.close()
