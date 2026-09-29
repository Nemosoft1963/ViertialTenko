import unittest
import base64
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.realtime_browser import MEDIA_BRIDGE_SCRIPT

class Avatar3DBrowserTests(unittest.TestCase):
    def test_real_webgl_renderer_and_media_bridge(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox","--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream","--enable-unsafe-swiftshader"])
            try:
                context=browser.new_context(permissions=["camera","microphone"])
                directory=Path(__file__).resolve().parents[1]/"app"
                script=";\n".join((directory/n).read_text(encoding="utf-8") for n in ("three.min.js","avatar3d.js"))
                portrait=base64.b64encode((directory/"avatar-portrait.png").read_bytes()).decode("ascii")
                script="window.__tenkoPortraitData='data:image/png;base64,"+portrait+"';"+script
                context.add_init_script(script=script+";\n"+MEDIA_BRIDGE_SCRIPT)
                page=context.new_page()
                page.route("http://localhost:9876/**",lambda route:route.fulfill(body="<html><body>test</body></html>",content_type="text/html"))
                page.goto("http://localhost:9876/")
                page.evaluate("localStorage.setItem('tenkoOperationMode','realtime_3d')")
                page.evaluate("async()=>{window.testStream=await Promise.race([navigator.mediaDevices.getUserMedia({video:true,audio:true}),new Promise((_,reject)=>setTimeout(()=>reject(new Error('media bridge timed out')),12000))]);}")
                self.assertEqual(page.evaluate("window.__tenkoAvatar3DStatus"),"ready")
                size=page.evaluate("()=>{let s=testStream.getVideoTracks()[0].getSettings();return [s.width,s.height]}")
                self.assertEqual(size,[960,540])
                page.evaluate("async()=>{const c=document.createElement('canvas');document.body.append(c);window.testAvatar=createTenkoAvatar3D(c);await testAvatar.ready;testAvatar.render(0,0,false);}")
                before=page.locator("canvas").screenshot()
                page.evaluate("testAvatar.render(1,1,true)")
                after=page.locator("canvas").screenshot()
                self.assertNotEqual(before,after)
                page.locator("canvas").screenshot(path="/tmp/tenko-avatar3d-preview.png")
            finally:
                browser.close()
