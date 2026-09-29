import argparse
import json
import math
import random
import sys
import wave
from array import array
from pathlib import Path

import av
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat


DEFAULT_IDLE_VIDEO = Path("/data/idle-base.mp4")


def read_envelope(audio_path: Path, fps: int) -> tuple[list[float], float]:
    with wave.open(str(audio_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)

    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM audio, got {sample_width * 8}-bit")

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        samples = array("h", samples[::channels])

    duration = len(samples) / sample_rate
    video_frames = max(1, math.ceil(duration * fps))
    levels: list[float] = []
    for index in range(video_frames):
        start = int(index * sample_rate / fps)
        end = min(len(samples), int((index + 1) * sample_rate / fps))
        window = samples[start:end]
        if not window:
            levels.append(0.0)
            continue
        mean_square = sum(value * value for value in window) / len(window)
        levels.append(math.sqrt(mean_square) / 32768.0)

    non_silent = sorted(level for level in levels if level > 0.002)
    if non_silent:
        floor = max(0.0025, non_silent[int(len(non_silent) * 0.08)])
        speech = non_silent[min(len(non_silent) - 1, int(len(non_silent) * 0.90))]
    else:
        floor, speech = 0.003, 0.08
    scale = max(0.015, speech - floor)

    envelope: list[float] = []
    current = 0.0
    for level in levels:
        target = max(0.0, min(1.0, (level - floor) / scale))
        target = target ** 0.62
        if target < 0.08:
            target = 0.0
        speed = 0.72 if target > current else 0.38
        current += (target - current) * speed
        envelope.append(max(0.0, min(1.0, current)))
    return envelope, duration


def make_blink_schedule(duration: float) -> list[tuple[float, float]]:
    randomizer = random.Random(20260821)
    result: list[tuple[float, float]] = []
    current = 3.8
    while current < duration:
        blink_duration = randomizer.uniform(0.18, 0.24)
        result.append((current, blink_duration))
        current += randomizer.uniform(5.2, 8.0)
    return result


def blink_closure(time_seconds: float, schedule: list[tuple[float, float]]) -> float:
    for start, duration in schedule:
        position = (time_seconds - start) / duration
        if 0.0 <= position <= 1.0:
            return math.sin(math.pi * position) ** 1.35
        if start > time_seconds:
            break
    return 0.0


def average_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    stat = ImageStat.Stat(image.crop(box).convert("RGB"))
    return tuple(int(value) for value in stat.mean[:3])


def add_blink(image: Image.Image, closure: float) -> None:
    if closure <= 0.02:
        return
    width, height = image.size
    eye_centers = ((0.451, 0.312), (0.543, 0.312))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    eye_half_width = max(7, round(width * 0.018))
    for normalized_x, normalized_y in eye_centers:
        center_x = round(width * normalized_x)
        center_y = round(height * normalized_y)
        sample_box = (
            center_x - eye_half_width,
            center_y - max(4, round(height * 0.028)),
            center_x + eye_half_width,
            center_y - max(2, round(height * 0.010)),
        )
        skin = average_color(image, sample_box)
        cover_height = max(1, round((2.0 + height * 0.012) * closure))
        draw.ellipse(
            (
                center_x - eye_half_width,
                center_y - cover_height,
                center_x + eye_half_width,
                center_y + cover_height,
            ),
            fill=(*skin, round(238 * closure)),
        )
        if closure > 0.42:
            alpha = round(155 * (closure - 0.42) / 0.58)
            points = [
                (center_x - eye_half_width + 2, center_y),
                (center_x, center_y + 1),
                (center_x + eye_half_width - 2, center_y),
            ]
            draw.line(points, fill=(73, 48, 42, alpha), width=max(1, width // 500))
    image.alpha_composite(overlay)


def add_mouth(image: Image.Image, openness: float, time_seconds: float) -> None:
    if openness <= 0.12:
        return
    width, height = image.size
    center_x = round(width * 0.500)
    center_y = round(height * 0.444)
    patch_half_width = max(13, round(width * 0.035))
    patch_half_height = max(6, round(height * 0.028))
    box = (
        center_x - patch_half_width,
        center_y - patch_half_height,
        center_x + patch_half_width,
        center_y + patch_half_height,
    )
    original = image.crop(box).convert("RGBA")
    patch_width, patch_height = original.size
    middle = patch_height // 2
    normalized = min(1.0, max(0.0, (openness - 0.12) / 0.88))
    gap = max(1, round(max(1, height * 0.007) * normalized))

    animated = original.copy()
    lower = original.crop((0, middle, patch_width, patch_height - gap))
    animated.paste(lower, (0, middle + gap))
    mouth_overlay = Image.new("RGBA", original.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(mouth_overlay)
    interior_half_width = max(6, round(patch_width * 0.24))
    overlay_draw.ellipse(
        (
            patch_width // 2 - interior_half_width,
            middle,
            patch_width // 2 + interior_half_width,
            middle + gap + 1,
        ),
        fill=(76, 43, 46, 165),
    )
    animated = Image.alpha_composite(animated, mouth_overlay)

    blend_mask = Image.new("L", original.size, 0)
    ImageDraw.Draw(blend_mask).ellipse((1, 1, patch_width - 1, patch_height - 1), fill=205)
    blend_mask = blend_mask.filter(ImageFilter.GaussianBlur(radius=2.0))
    image.paste(animated, box[:2], blend_mask)


def natural_camera_motion(image: Image.Image, time_seconds: float) -> Image.Image:
    """Add subtle, seamless idle motion when no idle video is available."""
    width, height = image.size
    breathing = (1.0 - math.cos(time_seconds * math.tau / 4.8)) / 2.0
    sway_x = math.sin(time_seconds * math.tau / 7.2) * width * 0.004
    sway_y = math.sin(time_seconds * math.tau / 4.8) * height * 0.004
    zoom = 1.018 + breathing * 0.006
    scaled_width = max(width + 2, round(width * zoom))
    scaled_height = max(height + 2, round(height * zoom))
    scaled = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
    left = round((scaled_width - width) / 2 + sway_x)
    top = round((scaled_height - height) / 2 + sway_y)
    left = max(0, min(scaled_width - width, left))
    top = max(0, min(scaled_height - height, top))
    return scaled.crop((left, top, left + width, top + height))


def load_idle_frames(idle_video: Path | None, width: int, height: int) -> tuple[list[Image.Image], float | None]:
    """Load a short idle clip for seamless looping."""
    if idle_video is None or not idle_video.is_file():
        return [], None
    frames: list[Image.Image] = []
    with av.open(str(idle_video)) as container:
        stream = container.streams.video[0]
        rate = float(stream.average_rate) if stream.average_rate else 25.0
        for frame in container.decode(video=0):
            image = frame.to_image().convert("RGB")
            frames.append(ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS).convert("RGBA"))
    return frames, rate if frames else None


def idle_frame_at(frames: list[Image.Image], source_fps: float | None, time_seconds: float) -> Image.Image | None:
    if not frames or not source_fps:
        return None
    return frames[int(time_seconds * source_fps) % len(frames)].copy()


def yuv_payload(image: Image.Image, width: int, height: int) -> bytes:
    frame = av.VideoFrame.from_image(image.convert("RGB"))
    converted = frame.reformat(width=width, height=height, format="yuv420p")
    planes: list[bytes] = []
    for plane, row_width, rows in (
        (converted.planes[0], width, height),
        (converted.planes[1], width // 2, height // 2),
        (converted.planes[2], width // 2, height // 2),
    ):
        raw = bytes(plane)
        planes.append(
            b"".join(
                raw[row * plane.line_size : row * plane.line_size + row_width]
                for row in range(rows)
            )
        )
    return b"FRAME\n" + b"".join(planes)


def build_virtual_camera(
    source: Path,
    audio: Path,
    output: Path,
    width: int = 512,
    height: int = 288,
    fps: int = 30,
    idle_video: Path | None = DEFAULT_IDLE_VIDEO,
) -> dict:
    if width % 2 or height % 2:
        raise ValueError("YUV420 output width and height must be even")
    if not source.exists():
        packaged_source = Path("/srv/assets/virtual-checkin-operator.png")
        if packaged_source.exists():
            source = packaged_source
        else:
            raise FileNotFoundError(f"avatar source not found: {source}")
    if not audio.exists():
        raise FileNotFoundError(f"avatar audio not found: {audio}")
    envelope, duration = read_envelope(audio, fps)
    base = ImageOps.fit(
        Image.open(source).convert("RGB"),
        (width, height),
        method=Image.Resampling.LANCZOS,
    ).convert("RGBA")
    idle_frames, idle_fps = load_idle_frames(idle_video, width, height)
    blinks = make_blink_schedule(duration)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.y4m")
    with temporary.open("wb") as stream:
        stream.write(
            f"YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C420jpeg\n".encode("ascii")
        )
        for index, mouth_open in enumerate(envelope):
            time_seconds = index / fps
            frame = idle_frame_at(idle_frames, idle_fps, time_seconds)
            if frame is None:
                frame = natural_camera_motion(base.copy(), time_seconds)
            add_blink(frame, blink_closure(time_seconds, blinks))
            add_mouth(frame, mouth_open, time_seconds)
            stream.write(yuv_payload(frame, width, height))
    temporary.replace(output)
    result = {
        "source": str(source),
        "audio": str(audio),
        "output": str(output),
        "duration_seconds": round(duration, 3),
        "frames": len(envelope),
        "fps": fps,
        "resolution": f"{width}x{height}",
        "idle_mode": "video_loop" if idle_frames else "procedural",
        "idle_video": str(idle_video) if idle_frames else None,
        "idle_source_frames": len(idle_frames),
        "bytes": output.stat().st_size,
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/data/virtual-checkin-operator.png")
    parser.add_argument("--audio", default="/meet-config/virtual-mic.wav")
    parser.add_argument("--output", default="/meet-config/virtual-camera.y4m")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=288)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--idle-video", default=str(DEFAULT_IDLE_VIDEO))
    parser.add_argument("--skip-idle-video", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_virtual_camera(
                Path(args.source),
                Path(args.audio),
                Path(args.output),
                args.width,
                args.height,
                args.fps,
                None if args.skip_idle_video else Path(args.idle_video),
            ),
            ensure_ascii=False,
        )
    )
