import asyncio
import hashlib
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import edge_tts

DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")
OUT_DIR = Path(os.getenv("GPU_PROMPT_DIR", "/meet-config/gpu-prompts"))
VOICE = os.getenv("REALTIME_TTS_VOICE", "ja-JP-NanamiNeural")
POLL_SECONDS = float(os.getenv("GPU_POLL_SECONDS", "0.5"))


def setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def ensure_models():
    required = Path("/workspace/MuseTalk/models/musetalkV15/unet.pth")
    if not required.is_file():
        subprocess.run(["/usr/local/bin/download-musetalk-models"], check=True)


async def synthesize(text, output):
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(str(output))


def render(prompt_id, text):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = OUT_DIR / "cache"
    cache_dir.mkdir(exist_ok=True)
    cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    cached_video = cache_dir / f"{cache_key}.mp4"
    audio = OUT_DIR / f"{prompt_id}.mp3"
    video = OUT_DIR / f"{prompt_id}.mp4"
    if cached_video.is_file() and cached_video.stat().st_size >= 1024:
        shutil.copy2(cached_video, video)
        return video
    asyncio.run(synthesize(text, audio))
    env = os.environ.copy()
    env.update({
        "GPU_INPUT_AUDIO": str(audio),
        "GPU_OUTPUT_MP4": str(video),
        "GPU_WORK_DIR": str(OUT_DIR / "work"),
        "PUBLISH_Y4M": "0",
    })
    subprocess.run(["/usr/local/bin/render-tenko-avatar"], env=env, check=True)
    if not video.is_file() or video.stat().st_size < 1024:
        raise RuntimeError("MuseTalk output was not created")
    shutil.copy2(video, cached_video)
    return video


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models_ready = False
    while True:
        try:
            with sqlite3.connect(DB_PATH, timeout=30) as con:
                if setting(con, "operation_mode", "legacy") not in ("realtime_gpu", "realtime_natural"):
                    save(con, "gpu_pipeline_state", "standby")
                    con.commit()
                    time.sleep(POLL_SECONDS)
                    continue
                prompt_id = int(setting(con, "realtime_prompt_id", "0"))
                ready_id = int(setting(con, "gpu_prompt_ready_id", "0"))
                text = setting(con, "realtime_prompt_text")
                if prompt_id > ready_id and setting(con, "realtime_prompt_fast", "0") == "1":
                    save(con, "gpu_prompt_ready_id", prompt_id)
                    save(con, "gpu_pipeline_state", "ready")
                    con.commit()
                    time.sleep(POLL_SECONDS)
                    continue
                if not text or prompt_id <= ready_id:
                    save(con, "gpu_pipeline_state", "ready" if models_ready else "loading_models")
                    con.commit()
                    if not models_ready:
                        ensure_models(); models_ready = True
                    time.sleep(POLL_SECONDS)
                    continue
                save(con, "gpu_pipeline_state", "rendering")
                save(con, "gpu_pipeline_prompt_id", prompt_id)
                con.commit()
            if not models_ready:
                ensure_models(); models_ready = True
            video = render(prompt_id, text)
            with sqlite3.connect(DB_PATH, timeout=30) as con:
                save(con, "gpu_prompt_video", str(video))
                save(con, "gpu_prompt_ready_id", prompt_id)
                save(con, "gpu_pipeline_state", "ready")
                save(con, "gpu_pipeline_error", "")
        except Exception as exc:
            with sqlite3.connect(DB_PATH, timeout=30) as con:
                save(con, "gpu_pipeline_state", "error")
                save(con, "gpu_pipeline_error", f"{type(exc).__name__}: {exc}"[:500])
            time.sleep(3)


if __name__ == "__main__":
    main()