"""Windows adapter: OBS virtual camera -> Meet, TTS -> Live Link audio device.

This is NOT a renderer. A running Unreal MetaHuman scene is a prerequisite.
"""
import argparse
import hmac
import io
import json
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_AUDIO = 18_000_000


def parse_wav(data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate, frames = wav.getframerate(), wav.getnframes()
        if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or
                not 8000 <= rate <= 96000 or not 0 < frames <= rate * 180):
            raise ValueError("Expected mono PCM16 WAV, at most 180 seconds")
        pcm = wav.readframes(frames)
        if len(pcm) != frames * 2:
            raise ValueError("Truncated WAV")
        return rate, pcm


class Media:
    def __init__(self, camera, audio_device):
        import cv2
        import sounddevice as sd
        self.cv2, self.sd = cv2, sd
        self.audio_device = audio_device
        sd.check_output_settings(device=audio_device, channels=1, dtype="int16", samplerate=48000)
        self.camera = cv2.VideoCapture(camera, cv2.CAP_DSHOW)
        if not self.camera.isOpened():
            raise RuntimeError("Configured MetaHuman virtual camera could not be opened")
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.lock = threading.Lock()
        self.audio_lock = threading.Lock()
        self.playback = None
        self.jpeg, self.updated = b"", 0
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.capture, daemon=True)
        self.thread.start()

    def capture(self):
        while not self.closed.is_set():
            ok, frame = self.camera.read()
            if ok:
                # Bound network and browser memory even if source is 4K.
                frame = self.cv2.resize(frame, (1280, 720))
                ok, encoded = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    with self.lock:
                        self.jpeg, self.updated = encoded.tobytes(), time.monotonic()
            self.closed.wait(1 / 30)

    def frame(self):
        with self.lock:
            if time.monotonic() - self.updated > 1.5 or not self.jpeg:
                raise RuntimeError("Virtual camera frame is stale")
            return self.jpeg

    def play(self, data):
        import numpy as np
        rate, pcm = parse_wav(data)
        self.frame()
        with self.audio_lock:
            if self.playback is not None and self.playback.active:
                raise RuntimeError("Previous audio is still playing")
            self.sd.play(np.frombuffer(pcm, dtype="<i2"), samplerate=rate,
                         device=self.audio_device, blocking=False)
            self.playback = self.sd.get_stream()
        return {"accepted": True}

    def close(self):
        self.closed.set()
        self.thread.join(timeout=2)
        self.sd.stop()
        self.camera.release()


def handler(media, token):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never log spoken content or credentials.

        def reply(self, code, body, mime="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                self.reply(401, {"error": "Unauthorized"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            try:
                if self.path == "/status":
                    try:
                        media.frame()
                        ready = True
                    except RuntimeError:
                        ready = False
                    self.reply(200, {"protocol": 1, "ready": ready,
                                     "scope": "transport-only; Unreal scene requires visual verification"})
                elif self.path == "/frame":
                    self.reply(200, media.frame(), "image/jpeg")
                else:
                    self.reply(404, {"error": "Not found"})
            except RuntimeError:
                self.reply(503, {"error": "Virtual camera unavailable"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/audio":
                self.reply(404, {"error": "Not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 44 <= size <= MAX_AUDIO:
                    self.reply(413, {"error": "Invalid audio size"})
                    return
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("Incomplete audio")
                self.reply(200, media.play(data))
            except (ValueError, wave.Error, EOFError):
                self.reply(400, {"error": "Expected mono PCM16 WAV"})
            except Exception:
                self.reply(503, {"error": "Audio device busy or unavailable"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera-index", type=int, required=True)
    parser.add_argument("--audio-device", type=int, required=True)
    parser.add_argument("--confirm-metahuman-source", action="store_true", required=True)
    args = parser.parse_args()
    token = os.getenv("METAHUMAN_BRIDGE_TOKEN", "")
    if len(token) < 32:
        parser.error("Set METAHUMAN_BRIDGE_TOKEN to a random token of at least 32 characters")
    media = Media(args.camera_index, args.audio_device)
    server = ThreadingHTTPServer((args.bind, args.port), handler(media, token))
    print("MetaHuman transport listening; verify the Unreal scene and Live Link visually.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        media.close()


if __name__ == "__main__":
    main()
