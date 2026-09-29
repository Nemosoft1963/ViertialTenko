import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from app.build_virtual_avatar import build_virtual_camera, idle_frame_at, natural_camera_motion


class VirtualAvatarTests(unittest.TestCase):
    def test_idle_frames_loop_by_source_frame_rate(self):
        frames = [Image.new("RGBA", (8, 8), (value, 0, 0, 255)) for value in (10, 20, 30)]
        self.assertEqual(idle_frame_at(frames, 2.0, 0.0).getpixel((0, 0))[0], 10)
        self.assertEqual(idle_frame_at(frames, 2.0, 1.0).getpixel((0, 0))[0], 30)
        self.assertEqual(idle_frame_at(frames, 2.0, 1.5).getpixel((0, 0))[0], 10)

    def test_procedural_motion_keeps_output_dimensions(self):
        image = Image.new("RGBA", (64, 36), (120, 80, 40, 255))
        self.assertEqual(natural_camera_motion(image, 2.4).size, (64, 36))

    def test_builds_30_fps_procedural_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audio, output = root / "avatar.png", root / "audio.wav", root / "camera.y4m"
            Image.new("RGB", (64, 36), (180, 140, 110)).save(source)
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8_000)
                wav.writeframes(b"\x00\x00" * 8_000)
            result = build_virtual_camera(source, audio, output, 64, 36, 30, root / "missing.mp4")
            self.assertEqual(result["frames"], 30)
            self.assertEqual(result["idle_mode"], "procedural")
            self.assertEqual(result["idle_source_frames"], 0)
            self.assertTrue(output.read_bytes().startswith(b"YUV4MPEG2 W64 H36 F30:1"))


if __name__ == "__main__":
    unittest.main()
